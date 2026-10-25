"""Other native account work invalidates workshop-only attribution."""
from contextlib import contextmanager

_external = 0
_epoch = 0


def external_active():
    return _external > 0


def external_epoch():
    return _epoch


@contextmanager
def external_execution():
    global _external, _epoch
    _external += 1
    _epoch += 1
    try:
        yield
    finally:
        _external -= 1
        _epoch += 1
