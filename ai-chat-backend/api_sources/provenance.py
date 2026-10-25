"""Carry UI receipts through legacy tuple contracts without shared mutable state."""
from .message_sources import sanitize


class GeneratedTuple(tuple):
    def __new__(cls, values, model_source=None):
        instance = super().__new__(cls, values)
        instance.model_source = sanitize(model_source)
        return instance
