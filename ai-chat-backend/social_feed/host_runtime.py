"""Explicit host injection for the previously published social module.

This module does not start a listener, provide credentials, or implement an
encounter engine. The deploying host owns identity validation and lifecycle.
"""
_runtime = None
_home_import = None
_cognition = None


def configure_runtime(runtime):
    global _runtime
    _runtime = runtime


def current_runtime():
    return _runtime


def require_runtime():
    if _runtime is None:
        raise RuntimeError('social_host_not_configured')
    return _runtime


def configure_home_ports(*, home_import=None, cognition=None):
    global _home_import, _cognition
    _home_import, _cognition = home_import, cognition


def friends():
    if _home_import is None:
        from fastapi import HTTPException
        raise HTTPException(503, 'home_import_port_not_configured')
    return _home_import


def cognition_profile(actor):
    # No binding is a valid optional state, never an inferred identity.
    return _cognition.resolve(actor) or {} if _cognition is not None else {}
