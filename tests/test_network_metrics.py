import pandas as pd

from aave_bns.network import compute_snapshot_metrics, temporal_metrics
from aave_bns.pipeline import build_synthetic_fixture
from aave_bns.transform import apply_entity_map


def test_snapshot_metrics_are_bounded():
    frame = apply_entity_map(build_synthetic_fixture(), None)
    first_week_end = frame["timestamp"].min() + pd.to_timedelta(7, unit="D")
    one = frame[
        (frame["chain_id"] == 1) & (frame["timestamp"] < first_week_end)
    ]
    metrics = compute_snapshot_metrics(one)
    assert metrics["active_nodes"] >= 2
    assert 0 < metrics["activity_hhi"] <= 1
    assert metrics["effective_entities"] >= 1
    assert 0 <= metrics["giant_component_ratio"] <= 1
    assert 0 <= metrics["core_share"] <= 1


def test_temporal_metrics_has_both_chains():
    frame = apply_entity_map(build_synthetic_fixture(), None)
    metrics = temporal_metrics(frame)
    assert set(metrics["chain_id"]) == {1, 42161}
    assert metrics.groupby("chain_id").size().min() == 16
