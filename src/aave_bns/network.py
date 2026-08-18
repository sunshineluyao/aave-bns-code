from __future__ import annotations

from collections.abc import Iterable

import networkx as nx
import numpy as np
import pandas as pd


def _gini(values: Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0 or np.isclose(array.sum(), 0.0):
        return 0.0
    array = np.sort(np.maximum(array, 0.0))
    n = array.size
    cumulative = np.cumsum(array)
    return float((n + 1 - 2 * (cumulative.sum() / cumulative[-1])) / n)


def _activity_shares(edges: pd.DataFrame, source: str, target: str) -> pd.Series:
    outbound = edges.groupby(source, observed=True)["value"].sum()
    inbound = edges.groupby(target, observed=True)["value"].sum()
    activity = outbound.add(inbound, fill_value=0.0)
    total = float(activity.sum())
    if total <= 0:
        return pd.Series(dtype=float)
    return activity / total


def compute_snapshot_metrics(
    edges: pd.DataFrame,
    *,
    source: str = "from_entity",
    target: str = "to_entity",
) -> dict[str, float | int]:
    if edges.empty:
        return {
            "active_nodes": 0,
            "edge_count": 0,
            "total_value": 0.0,
            "activity_hhi": 0.0,
            "effective_entities": 0.0,
            "activity_gini": 0.0,
            "giant_component_ratio": 0.0,
            "density": 0.0,
            "core_share": 0.0,
        }

    graph = nx.DiGraph()
    aggregated = edges.groupby([source, target], observed=True, as_index=False)["value"].sum()
    for row in aggregated.itertuples(index=False):
        u = str(getattr(row, source))
        v = str(getattr(row, target))
        if u == v:
            graph.add_node(u)
            continue
        graph.add_edge(u, v, weight=float(row.value))

    active_nodes = graph.number_of_nodes()
    weak = graph.to_undirected()
    giant = max((len(component) for component in nx.connected_components(weak)), default=0)
    shares = _activity_shares(edges, source, target)
    hhi = float(np.square(shares).sum()) if not shares.empty else 0.0
    effective = float(1.0 / hhi) if hhi > 0 else 0.0

    if active_nodes >= 2 and weak.number_of_edges() > 0:
        core_numbers = nx.core_number(weak)
        max_core = max(core_numbers.values())
        core_share = sum(value == max_core for value in core_numbers.values()) / active_nodes
    else:
        core_share = float(active_nodes > 0)

    return {
        "active_nodes": active_nodes,
        "edge_count": graph.number_of_edges(),
        "total_value": float(edges["value"].sum()),
        "activity_hhi": hhi,
        "effective_entities": effective,
        "activity_gini": _gini(shares.values),
        "giant_component_ratio": float(giant / active_nodes) if active_nodes else 0.0,
        "density": float(nx.density(graph)) if active_nodes > 1 else 0.0,
        "core_share": float(core_share),
    }


def temporal_metrics(
    transfers: pd.DataFrame,
    *,
    period: str = "W-SUN",
    source: str = "from_entity",
    target: str = "to_entity",
) -> pd.DataFrame:
    frame = transfers.copy()
    frame["period"] = frame["timestamp"].dt.tz_convert("UTC").dt.floor("D")
    week_offset = (frame["period"].dt.dayofweek + 1) % 7
    frame["period"] = frame["period"] - pd.to_timedelta(week_offset, unit="D")
    rows: list[dict[str, object]] = []
    grouping = ["period", "chain_id", "asset"]
    for keys, group in frame.groupby(grouping, observed=True, sort=True):
        metrics = compute_snapshot_metrics(group, source=source, target=target)
        rows.append(dict(zip(grouping, keys, strict=True), **metrics))
    return pd.DataFrame(rows).sort_values(grouping).reset_index(drop=True)
