"""Public test-suite policy for optional historical integration artifacts."""

from __future__ import annotations

import os

import pytest

EXTERNAL_ASSET_TEST_MODULES = {
    "test_causal_v2_design.py",
    "test_manuscript_claim_consistency.py",
    "test_manuscript_visual_consistency.py",
    "test_network_measure_glossary.py",
    "test_pipeline.py",
    "test_real_v2.py",
    "test_real_v2_ethereum.py",
    "test_real_v2_ethereum_reporting.py",
    "test_real_v3_reporting.py",
    "test_real_v4_reporting.py",
    "test_real_v5_core_periphery.py",
    "test_real_v5_pilot_did.py",
    "test_real_v6_gnosis_donor.py",
    "test_real_v6_gnosis_reporting.py",
    "test_real_v6_multichain.py",
    "test_source_audit.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests whose governed historical inputs are not in the code package."""
    if os.getenv("AAVE_BNS_RUN_EXTERNAL_ASSET_TESTS") == "1":
        return
    marker = pytest.mark.skip(
        reason=(
            "requires separately governed historical integration artifacts; "
            "set AAVE_BNS_RUN_EXTERNAL_ASSET_TESTS=1 only when those inputs exist"
        )
    )
    for item in items:
        if item.path.name in EXTERNAL_ASSET_TEST_MODULES:
            item.add_marker(marker)
