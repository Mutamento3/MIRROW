"""Evidence-based automatic admission, separate from ranking and tool search.

These are retrieval heuristics, not calibrated probabilities or event identity.
Context can corroborate a weak current match, never admit an unrelated one.
"""


def recall_support_level(hit):
    if hit.direct_match or hit.lexical_score >= .45:
        return 'strong'
    semantic = hit.semantic_score
    if semantic is not None and semantic >= .55:
        return 'strong'
    context = getattr(hit, 'context_score', None)
    if semantic is not None and context is not None:
        if semantic >= .50 and context >= .65:
            return 'strong'
        if semantic >= .42 and context >= .55:
            return 'tentative'
    # Optional embeddings can be unavailable; keep genuine lexical recovery.
    return 'strong' if semantic is None and hit.lexical_score >= .32 else 'none'


def has_recall_support(hit):
    return recall_support_level(hit) != 'none'


def tentative_budget_rejections(hits):
    """Share one uncertain association across lanes, not one per source type."""
    seen = False
    rejected = set()
    for hit in hits:
        if recall_support_level(hit) != 'tentative':
            continue
        if seen:
            rejected.add((hit.document.source_layer, hit.document.document_id))
        seen = True
    return rejected
