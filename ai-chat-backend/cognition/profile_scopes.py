"""Compiled serving domains, separate from book ownership and live state."""

LABELS = {'general': '通识', 'writing': '写作', 'life_rhythm': '生活节律',
          'music': '音乐', 'gaming': '游戏', 'social': '交往'}
SCOPES = tuple(LABELS)
CAPSULE_LIMITS = {'general': 700, 'writing': 1000,
                  'life_rhythm': 700, 'music': 600, 'gaming': 600, 'social': 600}
TASK_SCOPES = {
    'general': ('general',), 'writing': ('general', 'writing'),
    'discussion': ('general', 'writing'), 'reading': (),
    'life_rhythm': ('general', 'life_rhythm'), 'music': ('general', 'music'),
    'gaming': ('general', 'gaming'), 'social': ('general', 'social'),
    'daily': ('general', 'life_rhythm', 'social'),
    'wander': ('general', 'life_rhythm', 'music', 'gaming'),
}


def empty_capsules(subjects=('k', 'human')):
    return [{'subject_id': sid, 'scope': scope, 'points': []}
            for sid in subjects for scope in SCOPES]
