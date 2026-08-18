from __future__ import annotations

import hashlib
import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {
    "chain_id",
    "action",
    "block_number",
    "tx_hash",
    "log_index",
    "event_week",
    "reserve_address",
    "actor_address",
    "beneficiary_address",
}
EXPECTED_ACTIONS = ("borrow", "liquidation", "repay", "supply", "withdraw")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_topology_events(path: str | Path, *, chain_id: int) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=lambda column: column in REQUIRED_COLUMNS)
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Topology input is missing columns: {sorted(missing)}")
    frame["chain_id"] = pd.to_numeric(frame["chain_id"], errors="raise").astype("int64")
    if set(frame["chain_id"]) != {chain_id}:
        raise ValueError("Topology input does not match the configured chain")
    if frame[["tx_hash", "log_index"]].duplicated().any():
        raise ValueError("Topology input contains duplicate transaction-log keys")
    for column in ("actor_address", "beneficiary_address", "reserve_address"):
        if frame[column].isna().any() or frame[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Topology input contains a missing {column}")
        frame[column] = frame[column].astype(str).str.lower()
    frame["action"] = frame["action"].astype(str).str.lower()
    if set(frame["action"]) != set(EXPECTED_ACTIONS):
        raise ValueError("Topology input does not contain the five locked Aave actions")
    frame["event_week"] = pd.to_numeric(frame["event_week"], errors="raise").astype("int64")
    if (int(frame["event_week"].min()), int(frame["event_week"].max())) != (-16, 16):
        raise ValueError("Topology input does not cover the locked event-week window")
    return frame


def _hhi(values: np.ndarray) -> float:
    total = float(values.sum())
    return float(np.square(values / total).sum()) if total else 0.0


def _pagerank_values(graph: nx.DiGraph, *, alpha: float = 0.85) -> np.ndarray:
    """Compute weighted PageRank without adding SciPy as a project dependency."""
    nodes = list(graph)
    count = len(nodes)
    if not count:
        return np.array([], dtype=float)
    index = {node: position for position, node in enumerate(nodes)}
    edge_rows = list(graph.edges(data="weight", default=1.0))
    source = np.fromiter((index[left] for left, _, _ in edge_rows), dtype=np.int64)
    target = np.fromiter((index[right] for _, right, _ in edge_rows), dtype=np.int64)
    weight = np.fromiter((float(value) for _, _, value in edge_rows), dtype=float)
    out_weight = np.bincount(source, weights=weight, minlength=count)
    rank = np.full(count, 1.0 / count)
    base = (1.0 - alpha) / count
    for _ in range(100):
        contribution = rank[source] * weight / out_weight[source]
        updated = np.full(count, base + alpha * rank[out_weight == 0].sum() / count)
        updated += alpha * np.bincount(target, weights=contribution, minlength=count)
        if np.abs(updated - rank).sum() < count * 1e-10:
            rank = updated
            break
        rank = updated
    return rank


def topology_metrics(events: pd.DataFrame, *, chain: str, layer: str) -> dict[str, object]:
    self_loop = events["actor_address"] == events["beneficiary_address"]
    delegated = events.loc[~self_loop]
    edges = (
        delegated.groupby(["actor_address", "beneficiary_address"], sort=False)
        .size()
        .rename("weight")
        .reset_index()
    )
    graph = nx.from_pandas_edgelist(
        edges,
        source="actor_address",
        target="beneficiary_address",
        edge_attr="weight",
        create_using=nx.DiGraph,
    )
    if graph.number_of_nodes():
        pagerank = _pagerank_values(graph)
        weak_components = list(nx.weakly_connected_components(graph))
        undirected = graph.to_undirected()
        core_numbers = nx.core_number(undirected) if undirected.number_of_edges() else {}
        maximum_core = max(core_numbers.values(), default=0)
        maximum_core_nodes = sum(value == maximum_core for value in core_numbers.values())
        weighted_out = np.fromiter((value for _, value in graph.out_degree(weight="weight")), float)
        weighted_in = np.fromiter((value for _, value in graph.in_degree(weight="weight")), float)
    else:
        pagerank = np.array([], dtype=float)
        weak_components = []
        maximum_core = 0
        maximum_core_nodes = 0
        weighted_out = np.array([], dtype=float)
        weighted_in = np.array([], dtype=float)
    node_count = graph.number_of_nodes()
    return {
        "chain": chain,
        "chain_id": int(events["chain_id"].iloc[0]),
        "layer": layer,
        "event_count": int(len(events)),
        "self_directed_event_share": float(self_loop.mean()),
        "delegated_event_count": int(len(delegated)),
        "topology_node_count": node_count,
        "topology_edge_count": graph.number_of_edges(),
        "directed_reciprocity": (
            float(nx.reciprocity(graph) or 0.0) if graph.number_of_edges() else 0.0
        ),
        "largest_weak_component_share": (
            max(map(len, weak_components), default=0) / node_count if node_count else 0.0
        ),
        "weighted_out_degree_hhi": _hhi(weighted_out),
        "weighted_in_degree_hhi": _hhi(weighted_in),
        "pagerank_hhi": _hhi(pagerank),
        "maximum_pagerank_share": float(pagerank.max()) if pagerank.size else 0.0,
        "maximum_k_core": maximum_core,
        "maximum_core_node_share": maximum_core_nodes / node_count if node_count else 0.0,
        "observed_unit": "address_role",
        "interpretation_status": "descriptive_noncausal",
    }


def build_topology_metrics(events: pd.DataFrame, *, chain: str) -> pd.DataFrame:
    rows = [topology_metrics(events, chain=chain, layer="all_actions")]
    for action in EXPECTED_ACTIONS:
        rows.append(
            topology_metrics(events.loc[events["action"] == action], chain=chain, layer=action)
        )
    return pd.DataFrame(rows)


def run_topology_analysis(
    ethereum_path: str | Path,
    arbitrum_path: str | Path,
    output_directory: str | Path,
) -> dict[str, Path]:
    ethereum_path, arbitrum_path = Path(ethereum_path), Path(arbitrum_path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    ethereum = load_topology_events(ethereum_path, chain_id=1)
    arbitrum = load_topology_events(arbitrum_path, chain_id=42161)
    metrics = pd.concat(
        [
            build_topology_metrics(ethereum, chain="Ethereum"),
            build_topology_metrics(arbitrum, chain="Arbitrum"),
        ],
        ignore_index=True,
    )
    metrics_path = output / "address_role_topology_metrics.csv"
    metrics.to_csv(metrics_path, index=False)
    summary = {
        "schema_version": 1,
        "status": "audited_address_role_topology_descriptive",
        "input_sha256": {
            "ethereum_aave_v3_pool_actions": _sha256(ethereum_path),
            "arbitrum_aave_v3_pool_actions": _sha256(arbitrum_path),
        },
        "metric_rows": int(len(metrics)),
        "structural_network_result_produced": True,
        "entity_level_primary_result_produced": False,
        "infrastructure_dependence_result_produced": False,
        "causal_estimate_produced": False,
        "topology_rule": (
            "Actor-to-beneficiary edges represent delegated or third-party position actions. "
            "Self-directed events are reported but excluded from graph topology."
        ),
        "withheld_reason": (
            "No verified bridge-route event table exists; route concentration, removal loss, "
            "disjoint paths, and minimum cut remain withheld."
        ),
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return {"metrics": metrics_path, "summary": summary_path}
