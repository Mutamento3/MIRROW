"""Pack whole semantically approved points; never edit an approved statement."""
from .profile_scopes import CAPSULE_LIMITS


def select_whole_points(result):
    capsules, omissions = [], []
    for capsule in result['capsules']:
        points, used, omitted = [], 0, 0
        for point in capsule['points']:
            size = len(point['text'])
            if used + size > CAPSULE_LIMITS[capsule['scope']]:
                omitted += 1
                continue
            points.append(point)
            used += size
        capsules.append({**capsule, 'points': points})
        omissions.append({'subject_id': capsule['subject_id'], 'scope': capsule['scope'],
                          'point_count': omitted})
    return {**result, 'capsules': capsules}, omissions
