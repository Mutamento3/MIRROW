"""Exact review coverage, not a semantic entailment or personality rule.

Every changed theme is accounted for by source-bound assertions or explicit
verbatim retention. The reviewer still decides mehuman, scope and duration.
"""
import re

from .other_temporal import content_text

_TITLES = re.compile(r'《([^《》\r\n]+)》')


def _named_in(name, text):
    # A shorter/longer marked work name is not a substring alias. Unmarked
    # verbatim occurrences remain eligible for ordinary prose references.
    return name in _TITLES.findall(text) or name in _TITLES.sub('', text)


def titles_bound(unit):
    """Bind explicitly delimited work names; no topic/intent classification.

    This is exact named-source fidelity, not fuzzy name resolution. Unmarked
    names and entity aliases remain the semantic review/registry's concern.
    """
    if not unit.get('coverage_required') and not any(
            claim.get('change') or claim.get('time_basis') for claim in unit.get('theme_claims', [])):
        return True
    for claim in unit.get('theme_claims', []):
        after = claim.get('after', unit.get('after', ''))
        names = _TITLES.findall(claim.get('section', '') + '\n' + after)
        before = unit.get('before', '') if unit.get('facet') == '__restructure__' else claim.get(
            'before', unit.get('before', ''))
        numbers = claim.get('source_numbers', [])
        sources = [row.get('text', '') for row in unit.get('source_focus', [])
                   if row['n'] in numbers]
        if any(not any(_named_in(name, text) for text in [before, *sources]) for name in names):
            return False
    return True


def _spans(text, fragment):
    start = 0
    while fragment:
        start = text.find(fragment, start)
        if start < 0:
            return
        yield start, start + len(fragment)
        start += len(fragment)


def retained_parts(assertion, section, before, unit):
    """Expand only a prepared, source-bound structural join, not loose quotes."""
    if assertion in before:
        return [assertion]
    if unit.get('facet') != '__restructure__':
        return []
    from .other_facets import split
    originals = dict(split(before)[1])
    covered, parts = set(), []
    for binding in unit.get('retained_bindings', []):
        if not isinstance(binding, dict) or binding.get('section') != section:
            continue
        quote = binding.get('assertion')
        original = originals.get(binding.get('source_section'), '')
        if not isinstance(quote, str) or not quote.strip() or quote not in original:
            continue
        spans = list(_spans(assertion, quote))
        if spans:
            parts.append(quote)
            for start, end in spans:
                covered.update(range(start, end))
    return parts if all(index in covered or char.isspace()
                        for index, char in enumerate(assertion)) else []


def retained_removed(finding, unit):
    """Pure structural moves preserve every removed body's mehumanful text.

    New-mehuman revisions stay with semantic review. This check is only a
    lossless-transfer contract, not a rule deciding which belief remains valid.
    """
    if unit.get('facet') != '__restructure__' or finding.get('grounds'):
        return True
    fragments = [content_text(part) for row in finding.get('retained', [])
                 for part in retained_parts(row['assertion'], row.get('section'),
                                            unit.get('before', ''), unit)]
    for old in unit.get('removed_sections', []):
        text = content_text(old['text'])
        covered = set()
        for fragment in fragments:
            for start, end in _spans(text, fragment):
                covered.update(range(start, end))
        if any(index not in covered and not char.isspace()
               for index, char in enumerate(text)):
            return False
    return True


def complete(finding, unit):
    claims = unit.get('theme_claims', [])
    if not unit.get('coverage_required') and not any(
            claim.get('change') or claim.get('time_basis') for claim in claims):
        return True  # legacy preparation has no per-theme change contract
    if not claims:
        return False
    retained = finding.get('retained', [])
    if not isinstance(retained, list):
        return False
    covered, texts = {}, {}
    for claim in claims:
        section = claim.get('section', '')
        text = claim.get('after', unit.get('after', ''))
        prefix = len(text) - len(content_text(text)) if claim.get('period') in {
            'recent', 'unspecified'} else 0
        texts[section], covered[section] = text, set(range(prefix))
    for retaining, rows in ((False, finding.get('grounds', [])), (True, retained)):
        for row in rows:
            if not isinstance(row, dict):
                return False
            section = row.get('section')
            if not section and len(claims) == 1:
                section = claims[0].get('section', '')
            if section not in texts:
                return False
            assertion = row.get('assertion')
            if not isinstance(assertion, str) or not assertion.strip() or assertion not in texts[section]:
                return False
            if retaining:
                claim = next(claim for claim in claims if claim.get('section', '') == section)
                before = unit.get('before', '') if unit.get('facet') == '__restructure__' else claim.get(
                    'before', unit.get('before', ''))
                if not retained_parts(assertion, section, before, unit):
                    return False
            for start, end in _spans(texts[section], assertion):
                covered[section].update(range(start, end))
    return retained_removed(finding, unit) and all(all(index in covered[section] or char.isspace()
                   for index, char in enumerate(text)) for section, text in texts.items())
