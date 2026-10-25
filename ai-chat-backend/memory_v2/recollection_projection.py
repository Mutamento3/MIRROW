"""Deduplicate diary impressions at rendering time, not historical events."""
from dataclasses import replace
from hashlib import sha256


def select_impression(hit, seen):
    document = hit.document
    if document.source_layer != 'event' or document.recurrence_members or document.recurrence_samples:
        return hit, ()
    fragments = tuple((item.identity, item.text) for item in document.recollections)
    if not fragments and document.display_text:
        fragments = (('', document.display_text),)
    for identity, text in fragments:
        keys = ('text:' + sha256(text.strip().encode('utf-8')).hexdigest(),)
        if identity:
            keys += (identity,)
        if any(key in seen for key in keys):
            continue
        # Process previews have room for one authored fragment, not three
        # silently concatenated impressions with lost individual identities.
        return replace(hit, document=replace(document, display_text=text)), keys
    return replace(hit, document=replace(document, display_text='')), ()
