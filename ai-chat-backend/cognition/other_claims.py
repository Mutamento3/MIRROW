"""Source and temporal scope of changed themes in the existing review lane.

These claims are review inputs, not a second cognition store. Semantic scope
is judged by the two existing model stages; code only checks source binding.
"""
from . import books
from .other_temporal import facet_title


def validate_facets(patch, material, entity, *, referenced, basis_supported, stable, old_body=''):
    result = []
    if not isinstance(patch.get('facets', []), list):
        raise books.BookError('主题结构需要列表')
    for facet in patch.get('facets', []):
        if not isinstance(facet, dict):
            raise books.BookError('主题结构无效')
        title, text = facet.get('title'), facet.get('text')
        if (not isinstance(title, str) or not title.strip() or '\n' in title or '\r' in title
                or not isinstance(text, str) or not text.strip()):
            raise books.BookError('他者认识维度无效')
        refs = referenced({'reference_ids': facet.get('reference_ids', patch['reference_ids'])}, material)
        ids = [row['id'] for row in refs]
        # Each theme binds to the authoritative material itself. The page's
        # summary receipt may omit a sibling theme's independently valid IDs;
        # it is not a second authority or the theme's evidence boundary.
        facet['reference_ids'] = ids
        basis = facet.get('basis', patch.get('basis', 'inferred'))
        if not basis_supported(basis, refs, patch['subject_id'], entity):
            raise books.BookError('主题认识依据与说话者不符')
        stability = facet.get('stability', patch.get('stability', 'tentative'))
        if stability not in {'stable', 'tentative'} or not stable(
                {'stability': stability, 'period': facet.get('period', 'unspecified')},
                refs, patch['subject_id'], entity, basis):
            raise books.BookError('主题稳定性缺少对应来源')
        from .other_temporal import qualify, retained_facets
        qualify(facet, refs, retained_texts=retained_facets(old_body, patch.get('remove_facets', [])))
        from .topics import normalize
        section = facet_title(facet)
        annotation = {'section': section, **{key: facet[key] for key in
                      ('types', 'areas', 'context') if key in facet}}
        normalize([annotation], '## ' + section + '\n' + text)
        result.append({'section': facet_title(facet), 'period': facet.get('period', 'unspecified'),
                       'basis': basis, 'stability': stability, 'reference_ids': ids,
                       'change': facet.get('change', ''), 'time_basis': facet.get('time_basis', ''),
                       'context': facet.get('context', '')})
    return result


def page_provenance(claims):
    """Page-level receipt of the selected changes, not a theme's default."""
    if not claims:
        return {}
    bases = {claim['basis'] for claim in claims}
    return {'basis': next(iter(bases)) if len(bases) == 1 else 'inferred',
            'stability': 'stable' if len(bases) == 1 and all(
                claim['stability'] == 'stable' for claim in claims) else 'tentative'}


def for_review(entry, title=None):
    return [claim for claim in entry.get('theme_claims', [])
            if title is None or claim['section'] == title]


def select_facets(patch, material, entity, *, rejected, **validators):
    """Isolate source/attribution failures for ordinary independent edits.

    A full restructure keeps its all-or-nothing preparation contract, as one
    missing replacement could erase an old condition. Semantic review follows
    for every remaining theme; this never downgrades or rewrites a bad claim.
    """
    if patch.get('remove_facets') or not isinstance(patch.get('facets', []), list):
        return patch, validate_facets(patch, material, entity, **validators)
    selected, claims = [], []
    for facet in patch.get('facets', []):
        try:
            checked = validate_facets({**patch, 'facets': [facet]}, material, entity, **validators)
            selected.append(facet)
            claims.extend(checked)
        except books.BookError as exc:
            rejected.append({'candidate_type': 'facet', 'entity_name': entity['name'],
                'facet': facet.get('title', '') if isinstance(facet, dict) else '',
                'reason': str(exc), 'reference_ids': facet.get('reference_ids', patch['reference_ids'])
                    if isinstance(facet, dict) else []})
    return {**patch, 'facets': selected}, claims


def source_focus(units, material):
    """Bring complete cited messages beside claims without removing context."""
    from .maintenance_sources import model_materials, resolve_many
    numbered = model_materials(material)
    by_id = {row['id']: view for row, view in zip(material, numbered)}
    for unit in units:
        selected = []
        for claim in unit.get('theme_claims', []):
            refs = resolve_many(claim.get('reference_ids'), material) or []
            claim['source_numbers'] = [by_id[row['id']]['n'] for row in refs]
            selected.extend(row['id'] for row in refs)
        unit['source_focus'] = [by_id[mid] for mid in dict.fromkeys(selected)]
    return units


def require_grounding(review, raw, units):
    """Check quote-bound decisions, not the model's self-scored booleans.

    The model decides semantic validity; this validates completeness of its
    decision. Legacy prepared candidates keep their existing review contract.
    """
    findings = raw.get('findings', []) if isinstance(raw, dict) else []
    if not isinstance(findings, list):
        findings = []
    accepted, rejected = [], list(review['rejections'])
    for index in review['approved_indices']:
        claims = units[index].get('theme_claims', [])
        scoped = units[index].get('coverage_required') or any(
            claim.get('change') or claim.get('time_basis') for claim in claims)
        matches = [row for row in findings if isinstance(row, dict) and type(row.get('index')) is int
                   and row['index'] == index]
        supported = len(matches) == 1 and grounded(matches[0], units[index])
        from .other_review_coverage import titles_bound
        if scoped and not titles_bound(units[index]):
            rejected.append({'index': index, 'reason': '新主题中的作品题名缺少对应原话或保留旧文'})
        elif scoped and not supported:
            rejected.append({'index': index, 'reason': '新主题复核缺少原话绑定或认识归因不一致'})
        else:
            accepted.append(index)
    return {'approved_indices': accepted, 'rejections': rejected}


def grounded(finding, unit):
    """Code validates exact citations/declared provenance, not entailment."""
    sources = {row['n']: row for row in unit.get('source_focus', [])}
    grounds = finding.get('grounds')
    if not isinstance(grounds, list):
        return False
    if not grounds and (unit.get('facet') != '__restructure__' or
                        not unit.get('coverage_required')):
        return False
    claims = unit.get('theme_claims', [])
    for ground in grounds:
        if not isinstance(ground, dict):
            return False
        assertion, quote = ground.get('assertion'), ground.get('quote')
        if (not isinstance(assertion, str) or not assertion.strip() or assertion not in unit.get('after', '')
                or not isinstance(quote, str) or not quote.strip()):
            return False
        n = ground.get('n')
        source = sources.get(n) if type(n) is int else None
        if source is None or quote not in source.get('text', ''):
            return False
        if ground.get('support') not in {'direct', 'interpretation'}:
            return False
        section = ground.get('section')
        relevant = [claim for claim in claims if claim.get('section') == section] if section else claims
        # A complete-page restructure remains atomic, but its citation and
        # attribution contracts belong to individual themes, not the page.
        if len(claims) > 1 and (not section or len(relevant) != 1):
            return False
        if section and not relevant:
            return False
        if any(assertion not in claim['after'] for claim in relevant if 'after' in claim):
            return False
        if any(n not in claim['source_numbers'] for claim in relevant if 'source_numbers' in claim):
            return False
        if ground['support'] == 'direct' and any(claim.get('basis') == 'self_report' for claim in relevant):
            from .other_analysis import _speaker_is_entity
            if not _speaker_is_entity(source, unit.get('subject_id', ''),
                                      {'name': unit.get('entity_name', ''), 'aliases': []}):
                return False
        if ground['support'] == 'interpretation' and any(claim.get('basis') in {
                'self_report', 'mutual_agreement'} for claim in relevant):
            return False
    if unit.get('coverage_required') or any(claim.get('change') or claim.get('time_basis') for claim in claims):
        for claim in claims:
            if claim.get('basis') != 'inferred':
                continue
            relevant = [ground for ground in grounds if len(claims) == 1 or
                        ground.get('section') == claim.get('section')]
            # A AI interpretation must actually be reviewed as an interpretation,
            # not approved solely as words the other entity directly confirmed.
            # Unchanged text-only restructuring needs no newly invented claim.
            if relevant and not any(ground['support'] == 'interpretation' for ground in relevant):
                return False
    from .other_review_coverage import complete
    return (isinstance(finding.get('reason'), str) and bool(finding['reason'].strip())
            and complete(finding, unit))
