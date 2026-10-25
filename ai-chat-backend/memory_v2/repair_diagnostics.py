"""Content-free repair failure codes shared by staging and retry receipts."""

PHASES = frozenset({"copy", "encode", "commit", "replan", "replacement",
                    "projection", "manifest", "promotion"})
ERROR_TYPES = frozenset({"RuntimeError", "ValueError", "TypeError", "KeyError",
                        "OSError", "PermissionError", "FileNotFoundError",
                        "OperationalError", "IntegrityError", "DatabaseError",
                        "SourceBoundaryError", "ImmutableConflictError", "ThreadConflictError",
                        "DayRepairPromotionError"})
ENCODING_STATUSES = frozenset({"ok", "contract_error", "provider_error",
                             "assembly_error", "request_limit", "quality_cache_conflict"})

PROMOTION_CHECKS = {
    'day repair receipt identity already has different content': 'receipt_conflict',
    'day repair staging result identity is invalid': 'stage_identity',
    'day repair event mapping changed after staging': 'event_mapping',
    'day repair thread mapping changed after staging': 'thread_mapping',
    'affected thread identity changed after staging': 'thread_identity',
    'affected thread membership changed after staging': 'thread_membership',
    'affected thread status changed after staging': 'thread_status',
    'projected existing event is no longer active': 'projected_event_status',
    'production baseline changed before promotion': 'baseline_changed',
    'conversation authority changed before promotion': 'authority_changed',
    'conversation authority changed during promotion': 'authority_changed',
}
PROMOTION_CODES = frozenset({*PROMOTION_CHECKS.values(), 'table_schema', 'delta_conflict',
                           'sqlite_error', 'other_exception'})


def safe_promotion_check(error):
    value = str(error)
    if value in PROMOTION_CHECKS:
        return PROMOTION_CHECKS[value]
    if value.startswith('repair table schema differs: '):
        return 'table_schema'
    if value.startswith('repair delta already exists without receipt: '):
        return 'delta_conflict'
    return 'sqlite_error' if type(error).__module__ == 'sqlite3' else 'other_exception'


def safe_repair_diagnostics(report):
    """Whitelist values; never serialize exception messages or source material."""
    phase = report.get("failure_phase")
    if not isinstance(phase, str) or phase not in PHASES:
        return {}
    kind = report.get("error_code")
    status = report.get("encoding_status")
    count = report.get("completed_batch_count")
    result = {"failure_phase": phase,
            "error_type": kind if isinstance(kind, str) and kind in ERROR_TYPES else "other_exception",
            "encoding_status": status if isinstance(status, str) and status in ENCODING_STATUSES else "unknown",
            "completed_batch_count": count if type(count) is int and count >= 0 else 0}
    check = report.get('promotion_check')
    if isinstance(check, str) and check in PROMOTION_CODES:
        result['promotion_check'] = check
    return result


def repair_failure_code(report):
    detail = safe_repair_diagnostics(report)
    if not detail:
        return "staging_error"
    return "staging_error:" + detail["failure_phase"] + ":" + detail["error_type"]
