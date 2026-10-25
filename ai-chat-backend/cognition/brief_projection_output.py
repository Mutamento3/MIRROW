"""Repair draft structure without repairing rejected evidence or adding claims."""
from . import books
from .brief_contract import validate as validate_candidate
from .output_repair import ContractError, parse


# Only these explicit envelope/identity errors receive a correction attempt.
# Per-point source/temporal failures retain the existing isolation behaviour.
FIELDS = {
    '认知简版结构无效': 'identities/capsules',
    '认知简版身份无效': 'identities[].subject_id',
    '认知简版来源无效': 'identities[].references',
    '认知简版称呼无效': 'identities[].name/aliases',
    '认知简版嵌套领域无效': 'capsules[].writing',
    '认知简版领域无效': 'capsules[].subject_id/scope/points',
    '认知简版领域不完整': 'capsules',
}


def validator(rows):
    def validate(raw):
        rejected = []
        value = parse(raw)
        if isinstance(value.get('identities'), list):
            allowed = {row['n'] for row in rows}
            issues = []
            for index, item in enumerate(value['identities']):
                if not isinstance(item, dict):
                    continue
                refs = item.get('references')
                if (not isinstance(refs, list) or not refs
                        or any(type(n) is not int or n not in allowed for n in refs)):
                    issues.append({'field': f'identities[{index}].references',
                        'required': 'nonempty array of source books.n integers',
                        'allowed_references': sorted(allowed)})
            if issues:
                raise ContractError('brief_projection_structure',
                    '身份缺少有效引用；每项 identities 都填写支持该名称的 books.n 数组，观点仍使用 selection。',
                    issues=issues)
        try:
            result = validate_candidate(value, rows, reject_point=lambda: rejected.append(True),
                                        require_selection=True, candidate_pool=True)
        except books.BookError as exc:
            field = FIELDS.get(str(exc))
            if field is None:
                raise
            raise ContractError('brief_projection_structure', str(exc),
                                issues=[{'field': field}]) from exc
        return result, len(rejected)
    return validate
