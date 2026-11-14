"""Keep tests hermetic: name-derived domain guesses must not hit real DNS.

discover_website() DNS-checks guessed domains (acmeventures.no ...). On
networks with wildcard DNS those guesses "resolve", which made results
depend on the machine. Tests that exercise guesses pass their own
``resolver=``; everything else sees no guessed domain resolve.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    from signalpost_agent import discover

    monkeypatch.setattr(discover, "_resolves", lambda _domain: False)
