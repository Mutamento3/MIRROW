"""Repairable reviewer-contract errors, distinct from legitimate rejection."""
from . import books
from .brief_contract import reviewed
from .output_repair import ContractError, parse

ERRORS = {
    '认知简版逐项核验字段缺失或不是布尔值': 'review_check_fields',
    '认知复核结构无效': 'review_structure',
    '认知复核拒绝项无效': 'review_rejection_structure',
    '认知复核未完整覆盖候选': 'review_coverage',
    '认知简版复核检查结构无效': 'selection_check_coverage',
    '认知简版复核检查未恰好覆盖观点候选；身份名称不填写观点检查': 'selection_check_coverage',
    '认知简版缺少常驻性质核验': 'review_resident_kind',
    '认知简版上下文疑点缺少收据': 'review_context_receipt',
    '认知简版上下文疑点类型无效': 'review_context_kind',
    '认知简版上下文疑点缺少自身原文': 'review_context_quote',
    '认知简版遗漏证据不能重复已选内容': 'review_missing_qualifier',
    '认知简版断裂或指代证据不在选段中': 'review_fragment_quote',
    '认知简版上下文疑点缺少拒绝说明': 'review_rejection_reason',
}


def validator(result, review_input):
    def validate(raw):
        parsed = parse(raw)
        # Report both index spaces together; a generic first-error message made
        # the reviewer repeat the same identity/point mix-up during repair.
        candidates = review_input['candidates']
        expected = {c['index'] for c in candidates}
        points = {c['index'] for c in candidates if c['kind'] == 'point'}
        identities = {c['index'] for c in candidates if c['kind'] == 'identity_name'}
        checks = parsed.get('selection_checks')
        if isinstance(checks, list):
            # These fields have no mehuman for an identity. Ignore only known
            # identity indices; never invent missing decisions or point checks.
            parsed['selection_checks'] = [check for check in checks if not (
                isinstance(check, dict) and type(check.get('index')) is int
                and check['index'] in identities)]
        issues = []
        groups = [('approved_indices/rejections', expected,
                   parsed.get('approved_indices'), parsed.get('rejections')),
                  ('selection_checks', points, [], parsed.get('selection_checks'))]
        for field, wanted, approved, rows in groups:
            if not isinstance(approved, list) or not isinstance(rows, list):
                continue  # The ordinary parser reports the malformed container.
            indices = approved + [r.get('index') if isinstance(r, dict) else None for r in rows]
            integers = [i for i in indices if type(i) is int]
            counts = {i: integers.count(i) for i in set(integers)}
            missing, extra = sorted(wanted - set(integers)), sorted(set(integers) - wanted)
            duplicates = sorted(i for i, count in counts.items() if count > 1)
            if missing or extra or duplicates or len(integers) != len(indices):
                issues.append({'field': field, 'missing_indices': missing, 'extra_indices': extra,
                               'duplicate_indices': duplicates, 'invalid_index_count': len(indices) - len(integers)})
        if issues:
            code = 'review_coverage' if issues[0]['field'] == 'approved_indices/rejections' else 'selection_check_coverage'
            raise ContractError(code,
                '批准与拒绝须覆盖全部候选；selection_checks 只填写观点。'
                '按各字段所列 missing_indices 补齐、extra_indices 移除，重复编号只保留一次。'
                '身份名称也须明确批准或拒绝，但不填写观点检查。', issues=issues)
        try:
            return reviewed(parsed, result, len(candidates), review_input=review_input)
        except books.BookError as exc:
            code = ERRORS.get(str(exc))
            if code is None:
                raise
            issues = [{'index': exc.candidate_index}] if hasattr(exc, 'candidate_index') else []
            raise ContractError(code, str(exc), issues=issues) from exc
    return validate
