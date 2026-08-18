from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from .real_v5_topology import _pagerank_values, load_topology_events

CHAINS = (("Ethereum", 1), ("Arbitrum", 42161))
ROMBACH_ALPHA_GRID = (0.25, 0.50, 0.75)
ROMBACH_BETA_GRID = (0.70, 0.80, 0.90)
DISPLAY_NODE_CAP = 160
DISPLAY_EDGE_CAP = 420


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hhi(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    total = float(values.sum())
    return float(np.square(values / total).sum()) if total else 0.0


def build_role_graphs(events: pd.DataFrame) -> tuple[nx.DiGraph, nx.Graph, pd.DataFrame]:
    """Build the locked non-self actor-to-beneficiary topology and its projection."""
    self_directed = events["actor_address"] == events["beneficiary_address"]
    delegated = events.loc[~self_directed, ["actor_address", "beneficiary_address"]]
    edges = (
        delegated.groupby(["actor_address", "beneficiary_address"], sort=False)
        .size()
        .rename("weight")
        .reset_index()
    )
    directed = nx.from_pandas_edgelist(
        edges,
        source="actor_address",
        target="beneficiary_address",
        edge_attr="weight",
        create_using=nx.DiGraph,
    )
    undirected = nx.Graph()
    for left, right, weight in edges.itertuples(index=False, name=None):
        value = float(weight)
        if undirected.has_edge(left, right):
            undirected[left][right]["weight"] += value
        else:
            undirected.add_edge(left, right, weight=value)
    return directed, undirected, edges


def _graph_arrays(
    graph: nx.Graph,
) -> tuple[list[str], dict[str, int], np.ndarray, np.ndarray, np.ndarray]:
    nodes = sorted(str(node) for node in graph.nodes())
    index = {node: position for position, node in enumerate(nodes)}
    edge_rows = sorted(
        (
            min(index[str(left)], index[str(right)]),
            max(index[str(left)], index[str(right)]),
            float(data.get("weight", 1.0)),
        )
        for left, right, data in graph.edges(data=True)
    )
    left = np.fromiter((row[0] for row in edge_rows), dtype=np.int64)
    right = np.fromiter((row[1] for row in edge_rows), dtype=np.int64)
    weight = np.fromiter((row[2] for row in edge_rows), dtype=float)
    return nodes, index, left, right, weight


def _adjacency_multiply(
    values: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    weight: np.ndarray,
) -> np.ndarray:
    count = len(values)
    return np.bincount(left, weights=weight * values[right], minlength=count) + np.bincount(
        right, weights=weight * values[left], minlength=count
    )


def _quadratic_quality(
    values: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    weight: np.ndarray,
) -> float:
    return float(2.0 * np.sum(weight * values[left] * values[right]))


def rombach_template(count: int, *, alpha: float, beta: float) -> np.ndarray:
    """Return the normalized Rombach profile, ordered periphery to core.

    This is the sharp transition function in Rombach et al. (2014), Eq. (2.9),
    using their one-based node rank.  Normalization does not change the best
    permutation for a fixed parameter pair and makes qualities comparable when
    the parameter-specific vectors are aggregated.
    """
    if count <= 0:
        return np.array([], dtype=float)
    boundary = int(np.floor(count * beta))
    boundary = min(max(boundary, 1), count - 1) if count > 1 else 1
    order = np.arange(1, count + 1, dtype=float)
    profile = np.empty(count, dtype=float)
    lower = order <= boundary
    profile[lower] = (1.0 - alpha) * order[lower] / (2.0 * boundary)
    if count > 1:
        profile[~lower] = (
            (order[~lower] - boundary)
            * (1.0 - alpha)
            / (2.0 * (count - boundary))
            + (1.0 + alpha) / 2.0
        )
    total = float(profile.sum())
    return profile / total if total else profile


def _assign_profile(profile: np.ndarray, score: np.ndarray) -> np.ndarray:
    stable_index = np.arange(len(score), dtype=np.int64)
    order = np.lexsort((stable_index, score))
    values = np.empty(len(score), dtype=float)
    values[order] = profile
    return values


def _fit_one_rombach_profile(
    initial_scores: Iterable[np.ndarray],
    left: np.ndarray,
    right: np.ndarray,
    weight: np.ndarray,
    *,
    alpha: float,
    beta: float,
    maximum_iterations: int = 50,
) -> tuple[np.ndarray, float]:
    """Maximize the Rombach objective with a deterministic sparse rank-switch heuristic.

    The Rombach objective and transition profile are unchanged. The global permutation
    problem is NP-hard, so this scalable implementation evaluates several structural starts
    and accepts only objective-improving full rank switches based on A @ c.
    """
    count = int(max(left.max(initial=-1), right.max(initial=-1)) + 1)
    profile = rombach_template(count, alpha=alpha, beta=beta)
    best_values = np.zeros(count, dtype=float)
    best_quality = -np.inf
    for initial in initial_scores:
        values = _assign_profile(profile, np.asarray(initial, dtype=float))
        quality = _quadratic_quality(values, left, right, weight)
        for _ in range(maximum_iterations):
            marginal = _adjacency_multiply(values, left, right, weight)
            scale = float(np.max(np.abs(marginal))) or 1.0
            proposal = _assign_profile(profile, marginal / scale + 1e-9 * values)
            proposed_quality = _quadratic_quality(proposal, left, right, weight)
            if proposed_quality <= quality + max(1e-10, abs(quality) * 1e-12):
                break
            values, quality = proposal, proposed_quality
        if quality > best_quality:
            best_values, best_quality = values.copy(), float(quality)
    return best_values, best_quality


def fit_rombach_aggregate(
    graph: nx.Graph,
    *,
    core_number: dict[str, int],
    pagerank: dict[str, float],
) -> tuple[dict[str, float], dict[str, object]]:
    nodes, _, left, right, weight = _graph_arrays(graph)
    weighted_degree = np.array([graph.degree(node, weight="weight") for node in nodes], float)
    unweighted_degree = np.array([graph.degree(node) for node in nodes], float)
    core = np.array([core_number[node] for node in nodes], float)
    rank = np.array([pagerank[node] for node in nodes], float)
    initial_scores = (
        weighted_degree,
        unweighted_degree,
        core * (float(weighted_degree.max(initial=0.0)) + 1.0) + weighted_degree,
        rank,
    )
    vectors: list[np.ndarray] = []
    raw_qualities: list[float] = []
    qualities: list[dict[str, float]] = []
    total_weight_twice = float(2.0 * weight.sum())
    for alpha in ROMBACH_ALPHA_GRID:
        for beta in ROMBACH_BETA_GRID:
            values, quality = _fit_one_rombach_profile(
                initial_scores,
                left,
                right,
                weight,
                alpha=alpha,
                beta=beta,
            )
            vectors.append(values)
            raw_qualities.append(quality)
            maximum_edge_quality = total_weight_twice * float(np.max(values) ** 2)
            qualities.append(
                {
                    "alpha": alpha,
                    "beta": beta,
                    "raw_quality": quality,
                    "normalized_quality": (
                        quality / maximum_edge_quality if maximum_edge_quality else 0.0
                    ),
                }
            )
    quality_weights = np.asarray(raw_qualities, dtype=float)
    if float(quality_weights.sum()) > 0:
        aggregate = np.average(np.vstack(vectors), axis=0, weights=quality_weights)
    else:
        aggregate = np.mean(np.vstack(vectors), axis=0)
    maximum_aggregate = float(aggregate.max(initial=0.0))
    if maximum_aggregate:
        aggregate = aggregate / maximum_aggregate
    return dict(zip(nodes, aggregate, strict=True)), {
        "alpha_grid": list(ROMBACH_ALPHA_GRID),
        "beta_grid": list(ROMBACH_BETA_GRID),
        "aggregation": "quality_weighted_parameter_scores_normalized_to_unit_maximum",
        "optimization": "deterministic_sparse_multi_start_rank_switch",
        "parameter_fits": qualities,
        "mean_normalized_quality": float(
            np.mean([row["normalized_quality"] for row in qualities])
        ),
        "minimum_normalized_quality": float(
            np.min([row["normalized_quality"] for row in qualities])
        ),
        "maximum_normalized_quality": float(
            np.max([row["normalized_quality"] for row in qualities])
        ),
    }


def borgatti_everett_score(
    *,
    node_count: int,
    edge_count: int,
    core_size: int,
    peripheral_edge_count: int,
) -> float:
    """Pearson fit between an observed binary graph and the BE ideal matrix."""
    total_pairs = node_count * (node_count - 1) / 2.0
    if total_pairs <= 0 or core_size <= 0 or core_size >= node_count:
        return 0.0
    ideal_ones = total_pairs - (node_count - core_size) * (node_count - core_size - 1) / 2.0
    overlap = edge_count - peripheral_edge_count
    observed_share = edge_count / total_pairs
    ideal_share = ideal_ones / total_pairs
    denominator = np.sqrt(
        observed_share
        * (1.0 - observed_share)
        * ideal_share
        * (1.0 - ideal_share)
    )
    if denominator <= 0:
        return 0.0
    return float((overlap / total_pairs - observed_share * ideal_share) / denominator)


def _scan_be_order(
    order: np.ndarray,
    neighbors: list[np.ndarray],
    *,
    edge_count: int,
) -> tuple[np.ndarray, float, int]:
    node_count = len(neighbors)
    core = np.zeros(node_count, dtype=bool)
    peripheral_edges = int(edge_count)
    best_score = -np.inf
    best_size = 0
    for core_size, node in enumerate(order, start=1):
        core[node] = True
        peripheral_edges -= int(np.count_nonzero(~core[neighbors[node]]))
        # A singleton is a degenerate ``core``: it has no within-core dyad and
        # turns a star hub into a formally perfect-looking BE solution. Start
        # the binary BE audit at two nodes while retaining the original fit.
        if core_size < min(2, node_count - 1):
            continue
        score = borgatti_everett_score(
            node_count=node_count,
            edge_count=edge_count,
            core_size=core_size,
            peripheral_edge_count=peripheral_edges,
        )
        if score > best_score + 1e-15:
            best_score, best_size = score, core_size
    fitted = np.zeros(node_count, dtype=bool)
    fitted[order[:best_size]] = True
    return fitted, float(best_score), best_size


def _refine_be_fixed_size(
    core: np.ndarray,
    neighbors: list[np.ndarray],
    *,
    maximum_swaps: int = 500,
    candidate_count: int = 64,
) -> np.ndarray:
    """Locally minimize periphery-periphery edges at the selected core size."""
    core = core.copy()
    for _ in range(maximum_swaps):
        peripheral = ~core
        peripheral_degree = np.array(
            [np.count_nonzero(peripheral[adjacent]) for adjacent in neighbors], dtype=int
        )
        stable = np.arange(len(core), dtype=int)
        core_candidates = np.lexsort((stable[core], peripheral_degree[core]))[:candidate_count]
        core_nodes = np.flatnonzero(core)[core_candidates]
        periphery_candidates = np.lexsort(
            (stable[peripheral], -peripheral_degree[peripheral])
        )[:candidate_count]
        periphery_nodes = np.flatnonzero(peripheral)[periphery_candidates]
        best_delta = 0
        best_pair: tuple[int, int] | None = None
        for left_node in core_nodes:
            adjacent = set(neighbors[left_node].tolist())
            for right_node in periphery_nodes:
                delta = (
                    int(peripheral_degree[left_node])
                    - int(right_node in adjacent)
                    - int(peripheral_degree[right_node])
                )
                if delta < best_delta:
                    best_delta, best_pair = delta, (int(left_node), int(right_node))
        if best_pair is None:
            break
        core[best_pair[0]] = False
        core[best_pair[1]] = True
    return core


def fit_borgatti_everett(
    graph: nx.Graph,
    *,
    core_number: dict[str, int],
    pagerank: dict[str, float],
    rombach: dict[str, float],
) -> tuple[dict[str, bool], dict[str, object]]:
    nodes, _, left, right, weight = _graph_arrays(graph)
    neighbors: list[list[int]] = [[] for _ in nodes]
    for first, second in zip(left, right, strict=True):
        neighbors[int(first)].append(int(second))
        neighbors[int(second)].append(int(first))
    neighbor_arrays = [np.asarray(sorted(row), dtype=np.int64) for row in neighbors]
    stable = np.arange(len(nodes), dtype=int)
    unweighted_degree = np.array([len(row) for row in neighbor_arrays], dtype=float)
    weighted_degree = np.bincount(left, weights=weight, minlength=len(nodes)) + np.bincount(
        right, weights=weight, minlength=len(nodes)
    )
    shell = np.array([core_number[node] for node in nodes], dtype=float)
    rank = np.array([pagerank[node] for node in nodes], dtype=float)
    continuous = np.array([rombach[node] for node in nodes], dtype=float)
    orderings = {
        "unweighted_degree": np.lexsort((stable, -unweighted_degree)),
        "weighted_degree": np.lexsort((stable, -weighted_degree)),
        "k_core_then_degree": np.lexsort((stable, -unweighted_degree, -shell)),
        "pagerank": np.lexsort((stable, -rank)),
        "rombach_coreness": np.lexsort((stable, -continuous)),
    }
    candidates: list[tuple[np.ndarray, float, int, str]] = []
    for name, order in orderings.items():
        fitted, score, size = _scan_be_order(order, neighbor_arrays, edge_count=len(left))
        candidates.append((fitted, score, size, name))
    fitted, score, size, source = max(candidates, key=lambda row: (row[1], -row[2], row[3]))
    fitted = _refine_be_fixed_size(fitted, neighbor_arrays)
    peripheral = ~fitted
    peripheral_edges = int(np.count_nonzero(peripheral[left] & peripheral[right]))
    score = borgatti_everett_score(
        node_count=len(nodes),
        edge_count=len(left),
        core_size=int(fitted.sum()),
        peripheral_edge_count=peripheral_edges,
    )
    return dict(zip(nodes, fitted.tolist(), strict=True)), {
        "model": "borgatti_everett_binary_ideal_matrix",
        "optimization": "deterministic_nested_multi_start_plus_fixed_size_swaps",
        "selected_start": source,
        "fit_correlation": score,
        "core_node_count": int(fitted.sum()),
        "core_node_share": float(fitted.mean()),
        "candidate_scores": {
            name: {"fit_correlation": value, "core_node_count": int(candidate_size)}
            for _, value, candidate_size, name in candidates
        },
    }


def temporal_core_persistence(events: pd.DataFrame) -> dict[str, dict[str, float]]:
    active_weeks: defaultdict[str, int] = defaultdict(int)
    maximum_core_weeks: defaultdict[str, int] = defaultdict(int)
    normalized_core_sum: defaultdict[str, float] = defaultdict(float)
    for week in range(-16, 17):
        weekly = events.loc[events["event_week"] == week]
        _, graph, _ = build_role_graphs(weekly)
        if graph.number_of_edges() == 0:
            continue
        core = nx.core_number(graph)
        maximum = max(core.values(), default=0)
        for node, value in core.items():
            active_weeks[node] += 1
            normalized_core_sum[node] += value / maximum if maximum else 0.0
            if value == maximum:
                maximum_core_weeks[node] += 1
    return {
        node: {
            "topology_active_weeks": float(count),
            "maximum_core_weeks": float(maximum_core_weeks[node]),
            "maximum_core_persistence": maximum_core_weeks[node] / count,
            "mean_normalized_weekly_core": normalized_core_sum[node] / count,
        }
        for node, count in active_weeks.items()
    }


def _spearman(left: Iterable[float], right: Iterable[float]) -> float:
    first = pd.Series(list(left), dtype=float).rank(method="average").to_numpy()
    second = pd.Series(list(right), dtype=float).rank(method="average").to_numpy()
    if len(first) < 2 or np.std(first) == 0 or np.std(second) == 0:
        return 0.0
    return float(np.corrcoef(first, second)[0, 1])


def analyze_chain(events: pd.DataFrame, *, chain: str) -> tuple[pd.DataFrame, dict[str, object]]:
    directed, undirected, _ = build_role_graphs(events)
    if directed.number_of_edges() == 0:
        raise ValueError(
            f"{chain}: no non-self actor-to-beneficiary edges remain after "
            "topology construction"
        )
    nodes = sorted(str(node) for node in directed.nodes())
    pagerank_values = _pagerank_values(directed)
    pagerank = dict(zip(list(directed.nodes()), pagerank_values, strict=True))
    core_number = nx.core_number(undirected)
    maximum_core = max(core_number.values(), default=0)
    rombach, rombach_summary = fit_rombach_aggregate(
        undirected, core_number=core_number, pagerank=pagerank
    )
    be_core, be_summary = fit_borgatti_everett(
        undirected,
        core_number=core_number,
        pagerank=pagerank,
        rombach=rombach,
    )
    persistence = temporal_core_persistence(events)
    nonself = events["actor_address"] != events["beneficiary_address"]
    actors = set(events.loc[nonself, "actor_address"])
    beneficiaries = set(
        events.loc[nonself, "beneficiary_address"]
    )
    rows = []
    for node in nodes:
        if node in actors and node in beneficiaries:
            role = "actor_and_beneficiary"
        elif node in actors:
            role = "actor_only"
        else:
            role = "beneficiary_only"
        temporal = persistence.get(
            node,
            {
                "topology_active_weeks": 0.0,
                "maximum_core_weeks": 0.0,
                "maximum_core_persistence": 0.0,
                "mean_normalized_weekly_core": 0.0,
            },
        )
        rows.append(
            {
                "chain": chain,
                "chain_id": int(events["chain_id"].iloc[0]),
                "address": node,
                "address_role": role,
                "weighted_out_degree": float(directed.out_degree(node, weight="weight")),
                "weighted_in_degree": float(directed.in_degree(node, weight="weight")),
                "pagerank": float(pagerank[node]),
                "core_number": int(core_number[node]),
                "maximum_k_core_member": bool(core_number[node] == maximum_core),
                "borgatti_everett_core": bool(be_core[node]),
                "rombach_coreness": float(rombach[node]),
                "topology_active_weeks": int(temporal["topology_active_weeks"]),
                "maximum_core_weeks": int(temporal["maximum_core_weeks"]),
                "maximum_core_persistence": float(temporal["maximum_core_persistence"]),
                "mean_normalized_weekly_core": float(temporal["mean_normalized_weekly_core"]),
                "observed_unit": "address_role",
                "interpretation_status": "descriptive_noncausal",
            }
        )
    node_frame = pd.DataFrame(rows).sort_values("address").reset_index(drop=True)
    max_core_nodes = set(node_frame.loc[node_frame["maximum_k_core_member"], "address"])
    be_nodes = set(node_frame.loc[node_frame["borgatti_everett_core"], "address"])
    union = max_core_nodes | be_nodes
    weighted_out = node_frame["weighted_out_degree"].to_numpy(dtype=float)
    weighted_in = node_frame["weighted_in_degree"].to_numpy(dtype=float)
    summary = {
        "chain": chain,
        "chain_id": int(events["chain_id"].iloc[0]),
        "event_count": int(len(events)),
        "delegated_event_count": int(
            (events["actor_address"] != events["beneficiary_address"]).sum()
        ),
        "directed_node_count": int(directed.number_of_nodes()),
        "directed_edge_count": int(directed.number_of_edges()),
        "undirected_edge_count": int(undirected.number_of_edges()),
        "weighted_out_degree_hhi": _hhi(weighted_out),
        "weighted_in_degree_hhi": _hhi(weighted_in),
        "pagerank_hhi": _hhi(node_frame["pagerank"].to_numpy(dtype=float)),
        "maximum_k_core": int(maximum_core),
        "maximum_k_core_node_count": int(len(max_core_nodes)),
        "maximum_k_core_node_share": len(max_core_nodes) / len(node_frame),
        "borgatti_everett": be_summary,
        "rombach": rombach_summary,
        "method_agreement": {
            "be_nodes_in_maximum_k_core": int(len(be_nodes & max_core_nodes)),
            "be_maximum_k_core_jaccard": (
                len(be_nodes & max_core_nodes) / len(union) if union else 0.0
            ),
            "rombach_pagerank_spearman": _spearman(
                node_frame["rombach_coreness"], node_frame["pagerank"]
            ),
            "rombach_k_core_spearman": _spearman(
                node_frame["rombach_coreness"], node_frame["core_number"]
            ),
        },
        "temporal_core": {
            "addresses_active_in_topology": int(
                (node_frame["topology_active_weeks"] > 0).sum()
            ),
            "addresses_ever_in_weekly_maximum_core": int(
                (node_frame["maximum_core_weeks"] > 0).sum()
            ),
            "addresses_with_persistence_at_least_half": int(
                (node_frame["maximum_core_persistence"] >= 0.5).sum()
            ),
        },
        "evidence_status": "observed_address_role_descriptive_noncausal",
    }
    return node_frame, summary


def validate_against_locked_metrics(
    summary: dict[str, object], locked_row: pd.Series
) -> None:
    exact_pairs = {
        "event_count": "event_count",
        "delegated_event_count": "delegated_event_count",
        "directed_node_count": "topology_node_count",
        "directed_edge_count": "topology_edge_count",
        "maximum_k_core": "maximum_k_core",
    }
    for observed_key, locked_key in exact_pairs.items():
        if int(summary[observed_key]) != int(locked_row[locked_key]):
            raise ValueError(f"Core-periphery input drift for {observed_key}")
    tolerance_pairs = {
        "weighted_out_degree_hhi": "weighted_out_degree_hhi",
        "weighted_in_degree_hhi": "weighted_in_degree_hhi",
        "pagerank_hhi": "pagerank_hhi",
        "maximum_k_core_node_share": "maximum_core_node_share",
    }
    for observed_key, locked_key in tolerance_pairs.items():
        if not np.isclose(
            float(summary[observed_key]),
            float(locked_row[locked_key]),
            rtol=1e-10,
            atol=1e-12,
        ):
            raise ValueError(f"Core-periphery input drift for {observed_key}")


def _stable_node_order(frame: pd.DataFrame, field: str) -> list[str]:
    return (
        frame.sort_values([field, "address"], ascending=[False, True], kind="mergesort")[
            "address"
        ]
        .astype(str)
        .tolist()
    )


def _ring_coordinates(
    addresses: list[str], radii: tuple[float, ...], offset: float
) -> dict[str, tuple[float, float]]:
    coordinates: dict[str, tuple[float, float]] = {}
    groups = [addresses[index :: len(radii)] for index in range(len(radii))]
    for ring_index, (radius, group) in enumerate(zip(radii, groups, strict=True)):
        if not group:
            continue
        for index, address in enumerate(group):
            angle = offset + 2.0 * np.pi * (index + 0.5 * ring_index) / len(group)
            coordinates[address] = (float(radius * np.cos(angle)), float(radius * np.sin(angle)))
    return coordinates


def build_display_backbone(
    events: pd.DataFrame,
    node_frame: pd.DataFrame,
    *,
    chain: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Select an auditable full-data backbone and deterministic radial layout.

    The display is not the estimation sample.  All reported metrics use the full
    address-role graph; this routine retains the fitted cores and adds their strongest
    observed context ties so that the two full graphs remain legible on a journal page.
    """
    _, graph, _ = build_role_graphs(events)
    largest_component = max(nx.connected_components(graph), key=len)
    frame = node_frame.loc[node_frame["address"].isin(largest_component)].copy()
    frame = frame.set_index("address", drop=False)
    frame.index.name = None
    reason: dict[str, str] = {}

    be_nodes = set(frame.loc[frame["borgatti_everett_core"], "address"])
    maximum_core_nodes = set(frame.loc[frame["maximum_k_core_member"], "address"])
    for address in sorted(maximum_core_nodes):
        reason[address] = "maximum_k_core"
    for address in sorted(be_nodes):
        reason[address] = "borgatti_everett_core"

    for field, label in (
        ("rombach_coreness", "high_continuous_coreness"),
        ("pagerank", "high_pagerank"),
    ):
        available = frame.loc[~frame.index.isin(reason)]
        for address in _stable_node_order(available, field)[:12]:
            reason.setdefault(address, label)

    # Preserve at least one observed context tie for each non-core node selected
    # above before filling the remaining display budget by connection to the core.
    for address in list(reason):
        if address in maximum_core_nodes or len(reason) >= DISPLAY_NODE_CAP:
            continue
        candidates = sorted(
            (
                (-float(data.get("weight", 1.0)), str(neighbor))
                for neighbor, data in graph[address].items()
                if str(neighbor) in frame.index
            )
        )
        if candidates:
            reason.setdefault(candidates[0][1], "context_neighbor")

    structural_core = maximum_core_nodes | be_nodes
    connection_weight: defaultdict[str, float] = defaultdict(float)
    for left, right, data in graph.edges(data=True):
        left, right = str(left), str(right)
        weight = float(data.get("weight", 1.0))
        if left in structural_core and right in frame.index and right not in reason:
            connection_weight[right] += weight
        if right in structural_core and left in frame.index and left not in reason:
            connection_weight[left] += weight
    for address, _ in sorted(connection_weight.items(), key=lambda row: (-row[1], row[0])):
        if len(reason) >= DISPLAY_NODE_CAP:
            break
        reason[address] = "core_neighbor"

    if len(reason) < DISPLAY_NODE_CAP:
        available = frame.loc[~frame.index.isin(reason)]
        for address in _stable_node_order(available, "pagerank"):
            if len(reason) >= DISPLAY_NODE_CAP:
                break
            reason[address] = "pagerank_context"

    selected = set(reason)
    display = frame.loc[sorted(selected)].copy()
    display["display_reason"] = display["address"].map(reason)
    display["rombach_percentile"] = display["rombach_coreness"].rank(
        method="average", pct=True
    )

    be_order = _stable_node_order(display.loc[display["borgatti_everett_core"]], "pagerank")
    kcore_order = _stable_node_order(
        display.loc[display["maximum_k_core_member"] & ~display["borgatti_everett_core"]],
        "pagerank",
    )
    continuous_order = _stable_node_order(
        display.loc[
            ~display["maximum_k_core_member"]
            & display["display_reason"].isin(("high_continuous_coreness", "high_pagerank"))
        ],
        "rombach_coreness",
    )
    context_order = sorted(
        set(display["address"]) - set(be_order) - set(kcore_order) - set(continuous_order)
    )
    chain_offset = 0.20 if chain == "Ethereum" else 0.44
    coordinates: dict[str, tuple[float, float]] = {}
    coordinates.update(_ring_coordinates(be_order, (0.075,), chain_offset))
    coordinates.update(_ring_coordinates(kcore_order, (0.31, 0.47), chain_offset + 0.17))
    coordinates.update(_ring_coordinates(continuous_order, (0.67,), chain_offset + 0.08))
    coordinates.update(_ring_coordinates(context_order, (0.90,), chain_offset))
    display["display_x"] = display["address"].map(lambda value: coordinates[value][0])
    display["display_y"] = display["address"].map(lambda value: coordinates[value][1])
    display = display.reset_index(drop=True)

    induced_rows = []
    for left, right, data in graph.edges(data=True):
        left, right = str(left), str(right)
        if left not in selected or right not in selected:
            continue
        first, second = sorted((left, right))
        induced_rows.append((first, second, float(data.get("weight", 1.0))))
    induced_rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    strongest_by_node: dict[str, tuple[str, str, float]] = {}
    for row in induced_rows:
        for address in row[:2]:
            strongest_by_node.setdefault(address, row)
    retained = {row for row in strongest_by_node.values()}
    for row in induced_rows:
        if len(retained) >= DISPLAY_EDGE_CAP:
            break
        retained.add(row)
    edge_frame = pd.DataFrame(
        sorted(retained, key=lambda row: (-row[2], row[0], row[1])),
        columns=("source_address", "target_address", "weight"),
    )
    edge_frame.insert(0, "chain", chain)
    edge_frame["observed_unit"] = "address_role_undirected_projection"
    edge_frame["display_status"] = "top_weight_backbone_not_estimation_sample"

    display_columns = [
        "chain",
        "chain_id",
        "address",
        "address_role",
        "pagerank",
        "core_number",
        "maximum_k_core_member",
        "borgatti_everett_core",
        "rombach_coreness",
        "maximum_core_persistence",
        "display_reason",
        "rombach_percentile",
        "display_x",
        "display_y",
        "observed_unit",
        "interpretation_status",
    ]
    metadata = {
        "chain": chain,
        "full_graph_nodes": int(graph.number_of_nodes()),
        "full_graph_edges": int(graph.number_of_edges()),
        "display_nodes": int(len(display)),
        "display_edges": int(len(edge_frame)),
        "maximum_k_core_nodes_retained": int(
            display["maximum_k_core_member"].sum()
        ),
        "borgatti_everett_nodes_retained": int(
            display["borgatti_everett_core"].sum()
        ),
        "display_rule": (
            "All fitted BE and maximum-k-core nodes in the largest component, plus high "
            "continuous-coreness/PageRank nodes and strongest observed context ties; top-weight "
            "induced edges with one strongest tie retained per displayed node."
        ),
    }
    return display[display_columns], edge_frame, metadata


def _write_deterministic_csv_gzip(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.stem, fileobj=raw, mode="wb", mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                frame.to_csv(text, index=False, lineterminator="\n")


def run_core_periphery_analysis(
    ethereum_path: str | Path,
    arbitrum_path: str | Path,
    output_directory: str | Path,
    *,
    locked_metrics_path: str | Path = "outputs/real_v5/topology/address_role_topology_metrics.csv",
) -> dict[str, Path]:
    inputs = {
        "Ethereum": (Path(ethereum_path), 1),
        "Arbitrum": (Path(arbitrum_path), 42161),
    }
    locked = pd.read_csv(locked_metrics_path)
    locked = locked.loc[locked["layer"] == "all_actions"].set_index("chain")
    node_frames: list[pd.DataFrame] = []
    display_node_frames: list[pd.DataFrame] = []
    display_edge_frames: list[pd.DataFrame] = []
    display_summaries: dict[str, object] = {}
    chain_summaries: dict[str, object] = {}
    for chain, (path, chain_id) in inputs.items():
        events = load_topology_events(path, chain_id=chain_id)
        nodes, summary = analyze_chain(events, chain=chain)
        validate_against_locked_metrics(summary, locked.loc[chain])
        node_frames.append(nodes)
        display_nodes, display_edges, display_summary = build_display_backbone(
            events, nodes, chain=chain
        )
        display_node_frames.append(display_nodes)
        display_edge_frames.append(display_edges)
        display_summaries[chain] = display_summary
        chain_summaries[chain] = summary
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    node_path = output / "node_coreness.csv.gz"
    _write_deterministic_csv_gzip(pd.concat(node_frames, ignore_index=True), node_path)
    display_node_path = output / "display_backbone_nodes.csv.gz"
    display_edge_path = output / "display_backbone_edges.csv.gz"
    _write_deterministic_csv_gzip(
        pd.concat(display_node_frames, ignore_index=True), display_node_path
    )
    _write_deterministic_csv_gzip(
        pd.concat(display_edge_frames, ignore_index=True), display_edge_path
    )
    summary = {
        "schema_version": 1,
        "status": "audited_address_role_core_periphery_descriptive",
        "input_sha256": {chain: sha256_file(path) for chain, (path, _) in inputs.items()},
        "locked_metrics_sha256": sha256_file(locked_metrics_path),
        "node_coreness_sha256": sha256_file(node_path),
        "display_backbone_node_sha256": sha256_file(display_node_path),
        "display_backbone_edge_sha256": sha256_file(display_edge_path),
        "edge_rule": (
            "Directed actor-to-beneficiary Pool-event edges exclude self-directed actions; "
            "core-periphery methods use the weighted undirected projection."
        ),
        "algorithms": {
            "k_core": "unweighted undirected core decomposition",
            "borgatti_everett": (
                "binary ideal-matrix correlation; deterministic multi-start nested fit with "
                "fixed-size local swaps"
            ),
            "rombach": (
                "weighted continuous profile aggregated over alpha={0.25,0.50,0.75} and "
                "beta={0.70,0.80,0.90}; deterministic sparse rank-switch heuristic"
            ),
        },
        "chains": chain_summaries,
        "display_backbone": display_summaries,
        "structural_network_result_produced": True,
        "entity_level_primary_result_produced": False,
        "infrastructure_dependence_result_produced": False,
        "causal_estimate_produced": False,
        "interpretation": (
            "Coreness describes observed address-role topology. It is not ownership, governance "
            "power, economic-actor concentration, migration, or a treatment effect."
        ),
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "nodes": node_path,
        "display_nodes": display_node_path,
        "display_edges": display_edge_path,
        "summary": summary_path,
    }
