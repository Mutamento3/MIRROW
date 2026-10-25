"""Report processing scope, not a second source of cognition or a retry gate."""


def state(receipt):
    if receipt.get('status') != 'completed':
        return 'unconfirmed'
    outcome = receipt.get('outcome')
    if outcome == 'supported_no_change':
        return ('cited_claims_supported' if receipt.get('claim_support') or
                receipt.get('coverage_scope') == 'cited_claims_only' else 'legacy_target_report')
    return {'insufficient': 'material_insufficient', 'updated': 'committed_update',
            'conflicting': 'conflict_recorded'}.get(outcome, 'unknown')


def counts(receipts):
    result = {}
    for receipt in receipts:
        scope = state(receipt)
        result[scope] = result.get(scope, 0) + 1
    return result
