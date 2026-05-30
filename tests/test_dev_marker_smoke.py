"""Smoke test for the @pytest.mark.integration auto-skip mechanism.

This file is INFRASTRUCTURE — it verifies that the collection hook in
conftest.py correctly skips integration tests when MAICHI_TEST_DB is
unset, and lets them run when set. It will be removed in Sprint 2 once
real integration tests exercise the same path.

Expected behavior:
- Local (no env var):  this test SKIPPED, all other tests pass
- CI integration job:  this test runs and trivially passes
"""

import pytest


@pytest.mark.integration
def test_integration_marker_is_respected_by_collection_hook():
    """If you see this PASS locally, MAICHI_TEST_DB must be set in your
    shell — which is unusual on a dev box without Docker. If you see
    SKIPPED with reason "needs DB", the gating works as designed."""
    assert True
