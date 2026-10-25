"""Content-free dispatch evidence across an existing finite route chain."""


def combine(previous, current):
    """Only explicit False proves that no request in the chain was started."""
    if previous is True or current is True:
        return True
    if previous is False and current is False:
        return False
    return None
