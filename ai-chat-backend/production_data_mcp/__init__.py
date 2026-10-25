"""Fail-closed, read-only MCP gateway for MIRROW production projections."""

from .gateway import create_server

__all__ = ["create_server"]
