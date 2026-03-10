from __future__ import annotations

import pytest

from sportscanner.testing.workflow_harness import ScenarioHarness


pytestmark = pytest.mark.deterministic_integration


def test_deterministic_workflow_harness() -> None:
    harness = ScenarioHarness(keep_temp=False)
    try:
        harness.run()
    finally:
        harness.close()
