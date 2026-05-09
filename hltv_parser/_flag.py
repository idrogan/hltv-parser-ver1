"""HLTV pause flag.

HLTV is paused (May 2026) until paid bypass budget is available. The
flag defaults to off; every public entry point in this package consults
it and short-circuits with a structured log line.

To resume HLTV scraping, set ``HLTV_ENABLED=true`` in the environment.
"""
from __future__ import annotations

import functools
import logging
import os

from .client import HLTVPausedError

log = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    return os.getenv("HLTV_ENABLED", "false").strip().lower() in _TRUTHY


def ensure_enabled(entry_point: str) -> None:
    if is_enabled():
        return
    log.warning(
        "event=hltv_paused entry_point=%s reason=HLTV_ENABLED_false", entry_point
    )
    raise HLTVPausedError(
        f"HLTV pipeline is paused (HLTV_ENABLED=false). Entry point: {entry_point}"
    )


def gated(method):
    """Decorate a service method so it raises ``HLTVPausedError`` when the
    flag is off. Logs once per call at WARNING with structured key=value."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        ensure_enabled(f"service.{method.__name__}")
        return method(self, *args, **kwargs)

    return wrapper
