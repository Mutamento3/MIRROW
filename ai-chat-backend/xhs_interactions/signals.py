"""Typed, serialized access to dedicated-phone XHS wakeup metadata only."""
import base64
import json
import re
import time

from android_device_gateway.gateway import _as_text
from xhs_publish.device import PublishDevice, HELPER


def decode_ids(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw).decode('ascii') != encoded:
            return None
        values = json.loads(raw.decode('utf-8', 'strict'))
        if (not isinstance(values, list) or len(values) > 256
                or any(not isinstance(x, str) or not re.fullmatch('[0-9a-f]{64}', x) for x in values)
                or len(set(values)) != len(values)):
            return None
        return values
    except (ValueError, UnicodeError):
        return None


class SignalDevice(PublishDevice):
    @classmethod
    def _allowed_argv(cls, argv):
        parts = tuple(argv)
        args = parts[3:] if len(parts) > 3 and parts[1] == '-s' else parts[1:]
        prefix = ('shell', 'am', 'instrument', '-w', '-r', '-e', 'op')
        if args == prefix + ('notifications', HELPER):
            return True
        if (len(args) in {12, 15} and args[:8] == prefix + ('ack',)
                and args[8:10] == ('-e', 'signal_ids_b64') and len(args[10]) <= 24000
                and decode_ids(args[10]) is not None and args[-1] == HELPER):
            return len(args) == 12 or (args[11:13] == ('-e', 'full_scan_token')
                                       and re.fullmatch('[0-9a-f]{32}', args[13]) is not None)
        return super()._allowed_argv(argv)


def result_fields(output):
    fields = {}
    for line in output.splitlines():
        prefix = 'INSTRUMENTATION_RESULT: '
        if not line.startswith(prefix):
            continue
        key, separator, value = line[len(prefix):].partition('=')
        if not separator or key in fields:
            raise ValueError('invalid_signal_receipt')
        fields[key] = value
    if fields.get('status') != 'ok' or 'INSTRUMENTATION_CODE: -1' not in output:
        raise ValueError('signal_helper_unavailable')
    return fields


def snapshot_fields(fields, *, now_ms=None):
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    try:
        observed = int(fields['observed_at_ms'])
        availability = fields['availability']
        needs_full_scan = fields['needs_full_scan']
        token = fields['full_scan_token']
        rows = json.loads(fields['signals_json'])
        count = int(fields['signal_count'])
        overflow = int(fields['overflow_count'])
        if (availability not in {'connected', 'disconnected', 'unknown'}
                or needs_full_scan not in {'true', 'false'}
                or not re.fullmatch('[0-9a-f]{32}', token)
                or not isinstance(rows, list) or not 0 <= count <= 256 or len(rows) != count
                or overflow < 0 or not -60000 <= now_ms - observed <= 120000):
            raise ValueError()
        ids = set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'id', 'key_hash', 'posted_at_ms',
                                                         'observed_at_ms', 'content_sha256', 'stale'}
                    or any(not isinstance(row.get(k), str) or not re.fullmatch('[0-9a-f]{64}', row[k])
                           for k in ('id', 'key_hash', 'content_sha256'))
                    or any(type(row.get(k)) is not int for k in ('posted_at_ms', 'observed_at_ms'))
                    or type(row.get('stale')) is not bool or row['id'] in ids):
                raise ValueError()
            ids.add(row['id'])
        return {'availability': availability, 'needs_full_scan': needs_full_scan == 'true',
                'full_scan_token': token, 'signals': rows, 'overflow_count': overflow,
                'observed_at_ms': observed}
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid_signal_snapshot') from None


class SignalReader:
    def __init__(self, device=None):
        self.device = device or SignalDevice(command_timeout_seconds=20)

    async def snapshot(self):
        with await self.device.lease_async('xhs_notification_signals'):
            result = await self.device._device_invoke('shell', 'am', 'instrument', '-w', '-r',
                                                      '-e', 'op', 'notifications', HELPER)
            if not result or result.returncode != 0:
                raise ValueError('signal_device_unavailable')
            return snapshot_fields(result_fields(_as_text(result.stdout)))

    async def acknowledge(self, ids, *, full_scan_token=''):
        encoded = base64.b64encode(json.dumps(ids, separators=(',', ':')).encode()).decode('ascii')
        if decode_ids(encoded) is None:
            raise ValueError('invalid_signal_ids')
        if full_scan_token and not re.fullmatch('[0-9a-f]{32}', full_scan_token):
            raise ValueError('invalid_full_scan_token')
        args = ['shell', 'am', 'instrument', '-w', '-r', '-e', 'op', 'ack',
                '-e', 'signal_ids_b64', encoded]
        if full_scan_token:
            args.extend(['-e', 'full_scan_token', full_scan_token])
        args.append(HELPER)
        with await self.device.lease_async('xhs_notification_signal_ack'):
            result = await self.device._device_invoke(*args)
            if not result or result.returncode != 0:
                raise ValueError('signal_device_unavailable')
            return result_fields(_as_text(result.stdout))
