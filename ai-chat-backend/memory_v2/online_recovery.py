"""Content-free retry receipts. These never represent memory completion."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import time

RECOVERY_CONTRACT_VERSION = 'actor-receipts-and-time-v3'
MAX_AUTOMATIC_ATTEMPTS = 3
BASE_RETRY_SECONDS = 1800

_CONTRACT_CHECKS = {
    'invalid temporal_basis': 'temporal_basis',
    'invalid time_precision': 'time_precision',
    'invalid epistemic_status': 'epistemic_status',
    'contemporaneous report has non-authoritative time_expression': 'contemporaneous_expression',
    'contemporaneous reports require reported_at precision': 'contemporaneous_precision',
    'explicit time_expression must occur verbatim in a cited source': 'unsourced_time_expression',
    'explicit time expression has an incompatible precision': 'explicit_precision',
    'explicit time expression requires a resolved occurred_at': 'explicit_time_unresolved',
    'unresolved time must leave occurred_at and calendar_date empty': 'unresolved_time_values',
    'unresolved time requires relative or unknown precision': 'unresolved_precision',
    'unresolved time_expression must occur verbatim in a cited source': 'unsourced_unresolved_expression',
    'every episode requires a participant_ids string array': 'participants_type',
    'participant_ids must include primary_subject_id': 'focal_participant_missing',
    'event_type is not in the stable taxonomy': 'event_type',
    'primary_subject_id must be a bounded non-empty string': 'focal_subject_invalid',
    'every event requires source_refs': 'sources_missing',
    'event source refs must be non-empty and unique': 'sources_duplicate',
    'aspects must be an array of at most four items': 'aspect_count',
    'each aspect requires only event_type and source_refs': 'aspect_fields',
    'aspect event types must be unique secondary taxonomy members': 'aspect_types',
    'aspect source refs must be members of the episode sources': 'aspect_sources_outside_episode',
    'AI events sourced only from assistant rows require direct_observation': 'k_epistemic_status',
    'Human events sourced only from user rows require explicit_report': 'human_epistemic_status',
    'Human events require at least one Human source row': 'human_actor_evidence_missing',
    'AI events require at least one AI action source row': 'k_actor_evidence_missing',
}


def safe_contract_check(error):
    value = str(error)
    if value in _CONTRACT_CHECKS:
        return _CONTRACT_CHECKS[value]
    for prefix, code in [
        ('event cites refs outside the batch:', 'invented_source'),
        ('aspect cites refs outside the batch:', 'invented_aspect_source'),
        ('every source ref must be accounted for:', 'source_accounting_incomplete'),
        ('non-event refs must not repeat event or overflow sources:', 'source_accounting_overlap'),
        ('overflow refs must not repeat already emitted event sources:', 'overflow_overlap'),
    ]:
        if value.startswith(prefix):
            return code
    if re.match(r'^episode \d+ field mismatch;', value):
        return 'episode_fields'
    if re.fullmatch(r'episode \d+ summary length \d+ is outside 1\.\.\d+', value):
        return 'summary_length'
    return 'other_contract_check'


class RetryDeferred(RuntimeError):
    pass


def recovery_signature(*, authority_digest, prompt_versions, work_kind):
    value = [RECOVERY_CONTRACT_VERSION, authority_digest, prompt_versions, work_kind]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def safe_encoding_diagnostics(report):
    """Keep shard-local coordinates, never summaries, raw outputs or stable message IDs."""
    diagnostics = []
    batches = list(report.get('encoding_reports') or [])
    # Repair staging transfers only sanitized shard coordinates, not raw reports.
    batches.extend({'batch_number': item.get('batch_number'), 'shards': [item]}
                   for item in (report.get('encoding_diagnostics') or [])[-16:]
                   if isinstance(item, dict))
    allowed_checks = set(_CONTRACT_CHECKS.values()) | {
        'invented_source', 'invented_aspect_source', 'source_accounting_incomplete',
        'source_accounting_overlap', 'overflow_overlap', 'episode_fields',
        'summary_length', 'other_contract_check'}
    for batch in batches[-16:]:
        for shard in (batch.get('shards') or [])[-16:]:
            code = shard.get('error_code')
            if code not in {'human_actor_evidence_missing', 'k_actor_evidence_missing',
                            'encoding_contract_rejected'}:
                continue
            item = {'batch_number': batch.get('batch_number'),
                    'batch_id': shard.get('batch_id'), 'attempt_number': shard.get('attempt_number'),
                    'error_code': code, 'actor_mismatches': []}
            check = shard.get('contract_check')
            item['contract_check'] = (check if isinstance(check, str) and check in allowed_checks
                                      else safe_contract_check(shard.get('error', '')))
            fields = shard.get('field_diagnostics') or {}
            fields = fields if isinstance(fields, dict) else {}
            from .encoding import _EPISODE_OUTPUT_FIELDS
            missing = fields.get('missing_fields')
            if (type(fields.get('episode_number')) is int and 1 <= fields['episode_number'] <= 64
                    and isinstance(missing, list)):
                known = sorted({name for name in missing if isinstance(name, str)
                                and name in _EPISODE_OUTPUT_FIELDS})
                if known:
                    item['field_diagnostics'] = {'episode_number': fields['episode_number'],
                                                 'missing_fields': known}
            locations = []
            times = []
            for location in (shard.get('temporal_diagnostics') or [])[:64]:
                if (isinstance(location, dict) and type(location.get('episode_number')) is int
                        and 1 <= location['episode_number'] <= 64
                        and location.get('reason') in {'contemporaneous_expression',
                            'unsourced_time_expression', 'unsourced_unresolved_expression'}):
                    times.append({k: location[k] for k in ('episode_number', 'reason')})
            if times:
                item['temporal_diagnostics'] = times
            for location in (shard.get('contract_diagnostics') or shard.get('contract_locations') or [])[:64]:
                if (isinstance(location, dict) and type(location.get('episode_number')) is int
                        and 1 <= location['episode_number'] <= 64
                        and type(location.get('aspect_number')) is int and 1 <= location['aspect_number'] <= 4
                        and location.get('reason') in {'unknown_type', 'primary_type_repeated', 'secondary_type_repeated'}):
                    locations.append({k:location[k] for k in ('episode_number','aspect_number','reason')})
            if locations:
                item['contract_locations'] = locations
            for error in (shard.get('actor_evidence_errors') or shard.get('actor_mismatches') or [])[:64]:
                if not isinstance(error, dict):
                    continue
                subject = error.get('primary_subject_id')
                number = error.get('episode_number')
                if subject not in {'human', 'k'} or not isinstance(number, int) or not 1 <= number <= 64:
                    continue
                refs = [ref for ref in error.get('source_refs', [])
                        if isinstance(ref, str) and re.fullmatch(r's[1-9]\d{0,5}', ref)][:256]
                item['actor_mismatches'].append({
                    'episode_number': number, 'primary_subject_id': subject,
                    'source_refs': refs,
                    'source_roles': [role for role in error.get('source_roles', [])
                                     if role in {'user', 'assistant', 'event', 'system', 'notification'}][:256],
                })
            # Batch identifiers are backend digests, not arbitrary provider strings.
            if not isinstance(item['batch_id'], str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,160}', item['batch_id']):
                item['batch_id'] = ''
            for key in ('batch_number', 'attempt_number'):
                if not isinstance(item[key], int):
                    item[key] = None
            diagnostics.append(item)
    return diagnostics


def safe_scene_diagnostics(report):
    from .scene_failure_diagnostics import project_scene_failure
    return project_scene_failure(report.get('reconstruction_report'))


def safe_continuation_diagnostics(report):
    page = report.get('continuation_report') or {}
    if page.get('status') not in {'contract_error', 'provider_error', 'needs_replan', 'attention_required'}:
        return {}
    return {'status': page['status'], 'request_count': int(page.get('model_call_count') or 0)}


class OnlineRetryReceipts:
    def __init__(self, memory_db_path):
        path = Path(memory_db_path)
        self.path = path.with_name('.' + path.name + '.online-recovery.json')

    @contextmanager
    def _claim(self):
        """Serialize the recovery command with backend startup without holding a model-call lock."""
        with self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as stream:
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RetryDeferred('retry_receipt_busy') from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError) as exc:
            raise RetryDeferred('retry_receipt_invalid') from exc

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + '.')
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def begin(self, active_date, signature, *, explicit_retry=False, now=None):
        with self._claim():
            return self._begin(active_date, signature, explicit_retry=explicit_retry, now=now)

    def _begin(self, active_date, signature, *, explicit_retry=False, now=None):
        now = time.time() if now is None else now
        records = self._read()
        previous = records.get(active_date) or {}
        if (not explicit_retry and previous.get('signature') == signature
                and previous.get('operator_hold')):
            raise RetryDeferred('operator_retry_required')
        if explicit_retry or previous.get('signature') != signature:
            previous = {}
        attempts = int(previous.get('attempt_count', 0))
        if attempts >= MAX_AUTOMATIC_ATTEMPTS:
            raise RetryDeferred('automatic_retry_exhausted')
        if now < float(previous.get('retry_after', 0)):
            raise RetryDeferred('retry_cooldown')
        attempts += 1
        # Persist before calling the model: restart/cancellation cannot erase the budget.
        records[active_date] = {
            'signature': signature, 'attempt_count': attempts, 'status': 'attempting',
            'last_attempt_at': now, 'retry_after': now + BASE_RETRY_SECONDS * 2 ** (attempts - 1),
        }
        self._write(records)

    def failed(self, active_date, *, error_code, report=None):
        with self._claim():
            return self._failed(active_date, error_code=error_code, report=report)

    def _failed(self, active_date, *, error_code, report=None):
        records = self._read()
        receipt = records[active_date]
        receipt.update(status='error', error_code=error_code,
                       encoding_diagnostics=safe_encoding_diagnostics(report or {}),
                       scene_diagnostics=safe_scene_diagnostics(report or {}),
                       continuation_diagnostics=safe_continuation_diagnostics(report or {}))
        from .repair_diagnostics import safe_repair_diagnostics
        receipt['repair_diagnostics'] = safe_repair_diagnostics(report or {})
        self._write(records)

    def succeeded(self, active_date):
        with self._claim():
            return self._succeeded(active_date)

    def hold_failed(self, active_date, signature):
        """Honor a one-pass maintenance boundary without inventing attempts."""
        with self._claim():
            records = self._read()
            receipt = records.get(active_date) or {}
            if receipt.get('signature') != signature or receipt.get('status') != 'error':
                raise RetryDeferred('failed_retry_receipt_changed')
            receipt['operator_hold'] = True
            self._write(records)

    def _succeeded(self, active_date):
        records = self._read()
        if active_date in records:
            del records[active_date]
            self._write(records)
