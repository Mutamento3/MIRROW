"""Separate durable encounter cards from AI's natural-language sharing."""

def classify_message(event_type, tool_calls, *, plain_followup=False):
    if plain_followup:
        return ('assistant', '')
    return ('assistant', event_type)
