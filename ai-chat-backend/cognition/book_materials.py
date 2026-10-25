"""Current semantic book contents for models; local receipts stay authoritative."""
import copy

FIELDS = ('name', 'domain', 'body', 'aliases', 'keywords', 'category', 'revision',
          'subject_id', 'knower_id', 'kind', 'core', 'source', 'updated_at',
          'owner_id', 'scope', 'basis', 'state', 'expires_at', 'entry_id', 'topics')


def project_snapshot(snapshot):
    """Keep complete current text and identity, excluding duplicate edit history."""
    return {domain: [{key: copy.deepcopy(entry[key]) for key in FIELDS if key in entry}
                     for entry in entries] for domain, entries in snapshot.items()}
