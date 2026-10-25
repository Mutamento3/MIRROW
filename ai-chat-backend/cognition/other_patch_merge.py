"""Mechanical per-subject merging before the normal evidence/review checks."""
from collections import defaultdict
from . import books
from .other_temporal import facet_title


def _combine(group):
    if len(group) == 1:
        return group[0]
    targets = {str(p.get('target_entry', '')) for p in group}
    modes = {p.get('overview_mode', 'preserve') for p in group}
    if len(targets) != 1 or modes != {'preserve'}:
        raise books.BookError('同一实体多份修订的整体认识或目标冲突')
    facets, titles, refs, removed = [], set(), [], []
    for patch in group:
        if not isinstance(patch.get('facets', []), list) or not isinstance(patch.get('reference_ids', []), list):
            raise books.BookError('他者认识修订无效')
        for facet in patch.get('facets', []):
            title = facet_title(facet) if isinstance(facet, dict) else None
            if not title or title in titles:
                raise books.BookError('同一实体多份修订修改了相同栏目')
            # Freeze the originating patch's defaults before its references and
            # provenance are combined with other patches for the same person.
            inherited = {key: patch.get(key, default) for key, default in (
                ('reference_ids', []), ('basis', 'inferred'), ('stability', 'tentative'))}
            titles.add(title); facets.append({**inherited, **facet})
        refs.extend(patch.get('reference_ids', []))
        removals = patch.get('remove_facets', [])
        if not isinstance(removals, list) or any(not isinstance(title, str) for title in removals):
            raise books.BookError('栏目重组旧标题无效')
        removed.extend(removals)
    if any(not isinstance(ref, (str, int)) for ref in refs):
        raise books.BookError('他者认识引用无效')
    bases = {p.get('basis', 'inferred') for p in group}
    return {**group[0], 'facets': facets, 'reference_ids': list(dict.fromkeys(refs)),
            'remove_facets': list(dict.fromkeys(removed)),
            'basis': next(iter(bases)) if len(bases) == 1 else 'inferred',
            'stability': 'tentative' if any(p.get('stability', 'tentative') == 'tentative' for p in group) else 'stable'}


def merge_patches(patches):
    """One conflicting person does not discard independently valid people."""
    groups = defaultdict(list)
    merged, audit, rejected = [], [], []
    for patch in patches:
        if not isinstance(patch, dict):
            rejected.append({'candidate_type': 'patch', 'reason': '他者认识修订无效', 'reference_ids': []})
            continue
        sid, name = str(patch.get('subject_id', '')), str(patch.get('entity_name', ''))
        groups[(sid, '' if sid else name)].append(patch)
    for (sid, _), group in groups.items():
        names = {p.get('entity_name') for p in group if p.get('entity_name')}
        name = str(group[0].get('entity_name', ''))
        audit.append({'subject_id': sid, 'entity_name': name, 'patches': group})
        try:
            if len(names) > 1:
                raise books.BookError('同一实体多份修订的名称冲突')
            merged.append(_combine(group))
        except books.BookError as exc:
            rejected.append({'candidate_type': 'patch', 'subject_id': sid,
                             'entity_name': name, 'reason': str(exc),
                             'reference_ids': [ref for patch in group
                                               for ref in patch.get('reference_ids', [])
                                               if isinstance(ref, (str, int))]
                             if all(isinstance(p.get('reference_ids', []), list) for p in group) else []})
    return merged, audit, rejected
