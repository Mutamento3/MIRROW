"""Private, source-bound validated outputs awaiting the authoritative transaction."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .continuation_inventory import inventory_payload
from .day_settlement import (
    day_settlement_input_digest, day_settlement_response_schema, parse_day_settlement_output,
)
from .day_settlement_runner import (
    DaySettlementRunResult, build_day_settlement_messages, day_settlement_execution_digest,
    day_settlement_prompt_version, _include_legacy_scene_groups,
)


class CheckpointError(ValueError):
    """A paid or uncertain output must not be silently generated again."""


class CheckpointedDayCall:
    """Persist the public return before parsing; retain unknown in-flight state."""
    def __init__(self, checkpoint, model_call):
        self.checkpoint, self.model_call = checkpoint, model_call
        self.returned = checkpoint.returned_output()
        self.calls = 0
        self.persistence_error = None
        if self.returned is None:
            checkpoint.save_state('started')

    async def __call__(self, **kwargs):
        if self.returned is not None:
            return self.returned['raw_output']
        self.calls = 1  # Business invocation, not supplier wire attempts or bills.
        try:
            raw = await self.model_call(**kwargs)
        except Exception as exc:
            try:
                if getattr(exc, 'request_dispatched', None) is False:
                    self.calls = 0
                    self.checkpoint.save_state('not_dispatched', error_type=type(exc).__name__)
                else:
                    response = getattr(exc, 'response', {})
                    if isinstance(response, dict) and isinstance(response.get('content'), str):
                        content = response['content']
                        self.checkpoint.save_state('provider_output_error', raw_output=content,
                            raw_digest=_digest(content), finish_reason=response.get('finish_reason', ''),
                            usage=getattr(exc, 'usage', {}))
            except (OSError, TypeError, ValueError) as error:
                self.persistence_error = error
            raise
        try:
            self.checkpoint.save_returned(raw)
        except (OSError, TypeError, ValueError) as exc:
            self.persistence_error = exc
            raise
        return raw


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".day-output-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class DaySettlementCheckpoint:
    def __init__(self, db_path, assembly, max_output_tokens):
        self.assembly = assembly
        plan = assembly.plan
        self.identity = {
            "version": "day-settlement-output-v1", "source_namespace": plan.source_namespace,
            "active_date": plan.active_date, "settlement_version": plan.settlement_version,
            "assembler_version": assembly.assembler_version,
            "prompt_version": day_settlement_prompt_version(plan.active_date),
            "source_digest": plan.source_digest, "input_digest": day_settlement_input_digest(plan),
            "execution_digest": day_settlement_execution_digest(plan),
            "inventory_digest": _digest(inventory_payload(assembly.candidate_inventory)),
            "request_digest": _digest(build_day_settlement_messages(plan)),
            "schema_digest": _digest(day_settlement_response_schema(
                include_scenes=_include_legacy_scene_groups(plan.active_date))),
            "max_output_tokens": max_output_tokens,
        }
        db_path = Path(db_path)
        self.path = db_path.with_name("." + db_path.name + ".day-settlement-outputs") / (_digest(self.identity) + ".json")

    def save_state(self, status, **fields):
        atomic_write(self.path, {"identity": self.identity, "status": status, **fields})

    def load(self):
        try:
            raw = self.path.read_text("utf-8")
        except FileNotFoundError:
            return None
        except UnicodeError as exc:
            raise CheckpointError("checkpoint_invalid_encoding") from exc
        try:
            value = json.loads(raw)
            if value["identity"] != self.identity:
                raise CheckpointError("checkpoint_identity_mismatch")
            if value["status"] == "not_dispatched":
                return None
            if value["status"] == "returned":
                self._check_return(value)
                return None  # The orchestrator revalidates this saved return locally.
            if value["status"] != "validated":
                raise CheckpointError("checkpoint_output_unknown")
            payload = value["payload"]
            if value["payload_digest"] != _digest(payload):
                raise CheckpointError("checkpoint_payload_mismatch")
            parsed = parse_day_settlement_output(payload, self.assembly.plan)
            if _include_legacy_scene_groups(self.assembly.active_date) and not parsed.scene_groups:
                raise CheckpointError("checkpoint_scene_groups_missing")
            run = dict(value["run"])
            run["normalization_codes"] = tuple(run.get("normalization_codes", ()))
            # These are the original generation's receipts, not this invocation's calls.
            result = DaySettlementRunResult(parsed=parsed, **run)
            from .day_settlement_persistence import prepare_day_settlement_draft
            prepare_day_settlement_draft(self.assembly, result)
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointError("checkpoint_invalid") from exc

    def _check_return(self, value):
        if (value.get('identity') != self.identity or 'raw_output' not in value
                or value.get('raw_digest') != _digest(value['raw_output'])):
            raise CheckpointError('checkpoint_raw_output_mismatch')

    def returned_output(self):
        try:
            value = json.loads(self.path.read_text('utf-8'))
        except FileNotFoundError:
            return None
        if value.get('status') != 'returned':
            return None
        self._check_return(value)
        return {'raw_output': value['raw_output']}

    def save_returned(self, raw):
        self.save_state('returned', raw_output=raw, raw_digest=_digest(raw))

    def save_validated(self, result):
        try:
            self._save_validated(result)
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointError("checkpoint_output_invalid") from exc

    def _save_validated(self, result):
        from .day_settlement_persistence import prepare_day_settlement_draft
        prepare_day_settlement_draft(self.assembly, result)
        parsed = result.parsed
        payload = {
            "day_compact": [{"event_refs": list(item.event_refs), "summary": item.summary}
                            for item in parsed.day_compact],
            "thread_judgments": [{"candidate_ref": item.candidate_ref, "outcome": item.outcome,
                "confidence": item.confidence, "reason_codes": list(item.reason_codes)}
                for item in parsed.thread_judgments],
        }
        if parsed.scene_groups:
            payload["scene_groups"] = [{"event_refs": list(item.event_refs), "summary": item.summary}
                                       for item in parsed.scene_groups]
        # Use the public output contract, never provider prose or a serialized prompt.
        reparsed = parse_day_settlement_output(payload, self.assembly.plan)
        if reparsed != parsed:
            raise CheckpointError("checkpoint_roundtrip_mismatch")
        run = dict(vars(replace(result, parsed=None)))
        run.pop("parsed")
        returned = self.returned_output()
        self.save_state("validated", payload=payload, payload_digest=_digest(payload), run=run,
                        **({'raw_output': returned['raw_output'], 'raw_digest': _digest(returned['raw_output'])}
                           if returned is not None else {}))
