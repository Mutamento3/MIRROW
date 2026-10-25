"""Internal song-choice context; shares the activity lifecycle's source scope."""
async def build_context(*, persona, session_id, activity_context=None):
    from .activity_context import build_context as build_activity_context
    return await build_activity_context(persona=persona, session_id=session_id,
        activity_context=activity_context, recipe='WANDER_MUSIC_SELECTION')
