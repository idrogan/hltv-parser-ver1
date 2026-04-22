"""Shared pytest fixtures.

Each test sets the env it needs **before** importing ``app`` and uses
``importlib.reload`` to pick up the new env. This keeps tests
independent and lets us cover both auth-required and auth-disabled
boot modes from one process.
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Iterator

import pytest


@pytest.fixture
def fresh_app(monkeypatch):
    """Build a fresh ``app.app`` with the env values supplied by the caller.

    Usage::

        def test_x(fresh_app):
            app = fresh_app(API_TOKEN="t", API_REQUIRE_TOKEN="true")
    """

    def _build(**env: str):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        # Drop any cached copy so module-level env reads re-execute.
        for mod in ("app", "common", "common.middleware"):
            sys.modules.pop(mod, None)
        return importlib.import_module("app").app

    return _build
