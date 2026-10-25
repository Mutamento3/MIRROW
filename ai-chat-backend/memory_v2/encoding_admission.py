"""Prospective encoder provenance clarification; no new event authority."""

START_ACTIVE_DATE = '2026-10-10'
VERSION = 'experience-admission-v1'
LEGACY_VERSION = VERSION
PRIORITY_CANDIDATE_VERSION = 'experience-admission-v2'
VERSIONS = (VERSION, PRIORITY_CANDIDATE_VERSION)

_OLD = (
    'wander, sentinel, and reminder are proactive AI outputs. In the mainline namespace, an episode supported entirely '
    'by proactive AI rows and no U row is not a shared Human/AI experience: classify those rows as '
    'non-event even when the wording is distinctive. A separate AI-self pipeline may preserve '
    'source-backed AI acts later. Proactive rows may support a mainline episode when a cited U row '
    'actually joins or responds to that experience. '
)
_NEW = (
    'Proactive AI rows mean AI-speaker assistant rows with source_kind wander, sentinel, or reminder. '
    'E persisted domain rows are a different source class. An E row supporting an actual independently '
    'recallable activity, received material, choice, or artifact can support a mainline episode without '
    'a U row. Its execution record and AI\'s retained account keep their own attribution. '
    'A following proactive AI share may supply the same experience\'s expressed mehuman or response. '
    'Playing, reading, or creating a work is the real actor\'s activity; a character\'s story remains '
    'attached to the work and its character. A lifecycle or resource-status receipt alone still supplies '
    'source accounting rather than a personal experience. In mainline, a AI episode supported only by '
    'proactive assistant rows is non-event; a cited U row joining the experience or an E row supporting '
    'the actual occurrence supplies independent evidence. '
)


def clarify(system, version=VERSION):
    if version not in VERSIONS:
        raise ValueError('unsupported_encoding_admission_version')
    if system.count(_OLD) != 1:
        raise ValueError('encoding_admission_contract_changed')
    replacement = _NEW
    if version == PRIORITY_CANDIDATE_VERSION:
        replacement += (
            'For an actual domain experience containing AI\'s retained account, the summary preserves '
            'the concrete personal mehuman, attitude, or response expressed there alongside the action '
            'and last known result. Saying only that AI shared thoughts or feelings does not preserve '
            'their content. Prioritize that distinguishing experience over an accompanying routine '
            'check-in or generic advice; a share is not a substitute for the activity\'s own account. '
        )
    return system.replace(_OLD, replacement)


def version_suffix(active_date):
    return '+' + VERSION if active_date >= START_ACTIVE_DATE else ''
