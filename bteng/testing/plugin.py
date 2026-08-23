"""Pytest fixtures for testing behavior trees.

``Blackboard.create(name)`` returns a **process-wide singleton**: two calls with
the same name give the same object, so whatever one test writes is still there
for the next one. That makes leaked state the default unless every test cleans up
by hand.

This plugin removes the chore. Enable it once in your ``conftest.py``::

    pytest_plugins = ["bteng.testing.plugin"]

From then on every test starts and ends with a clean set of named blackboards.

If you would rather opt in per test, skip the line above and request the
``bteng_clean_blackboards`` fixture directly, or use ``bteng_blackboard`` to get
a throwaway blackboard that is never shared with anything else.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from bteng.blackboard.blackboard import Blackboard


@pytest.fixture(autouse=True)
def bteng_clean_blackboards() -> Iterator[None]:
    """Reset every named global blackboard around each test.

    Runs before as well as after, so a test is unaffected by anything that ran
    earlier — including code that touched a blackboard at import time.
    """
    Blackboard.reset_all()
    yield
    Blackboard.reset_all()


@pytest.fixture
def bteng_blackboard(request: pytest.FixtureRequest) -> Iterator[Blackboard]:
    """A blackboard scoped to the requesting test, shared with nothing.

    Built with the plain constructor rather than ``Blackboard.create()``, so it is
    a genuinely new object and needs no cleanup::

        def test_navigation(bteng_blackboard):
            bteng_blackboard.set("goal", (1.0, 2.0))
    """
    bb = Blackboard(scope_name=f"test::{request.node.name}")
    yield bb
    bb.clear()
