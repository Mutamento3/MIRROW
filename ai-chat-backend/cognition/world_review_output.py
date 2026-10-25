"""Persist/recover the review envelope; existing domain decisions stay authoritative."""
from .output_repair import ContractError, parse


def validator(proposal_count):
    def validate(raw):
        value = parse(raw)
        # A habit-only review has no factual candidates to decide. Missing is
        # equivalent to an empty list only in that case, never to an approval.
        if 'reviews' not in value and proposal_count == 0:
            value['reviews'] = []
        if not isinstance(value.get('reviews'), list):
            raise ContractError('world_review_structure', '事实审核的 reviews 必须是列表；无提议时可为空。',
                                issues=[{'field': 'reviews', 'proposal_count': proposal_count}])
        return value
    return validate
