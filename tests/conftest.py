"""Pytest configuration for BTEng's own suite.

Enables the plugin BTEng ships to its users, so the engine is tested under the
same conditions it recommends: every named global blackboard is reset around each
test, and no test inherits state from the one before it.
"""

pytest_plugins = ["bteng.testing.plugin"]
