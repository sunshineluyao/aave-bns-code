from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from .config import load_yaml
from .provenance import sha256_file, utc_now_iso, write_manifest
from .reporting import write_simulation_result_table


@dataclass(frozen=True)
class MatchedAgentInputs:
    base_value: np.ndarray
    ethereum_cost: np.ndarray
    remote_cost: np.ndarray
    gho_affinity: np.ndarray
    remote_affinity: np.ndarray
    fitness: np.ndarray
    link_draws: np.ndarray


def _matched_inputs(n_agents: int, seed: int) -> MatchedAgentInputs:
    rng = np.random.default_rng(seed)
    draws = rng.random((n_agents, n_agents))
    draws = np.triu(draws, k=1)
    draws = draws + draws.T
    return MatchedAgentInputs(
        base_value=rng.normal(0.94, 0.20, size=n_agents),
        ethereum_cost=rng.uniform(0.28, 0.72, size=n_agents),
        remote_cost=rng.uniform(0.32, 0.72, size=n_agents),
        gho_affinity=np.maximum(rng.normal(0.72, 0.22, size=n_agents), 0.05),
        remote_affinity=rng.normal(-0.03, 0.18, size=n_agents),
        fitness=np.clip(rng.lognormal(0.0, 0.55, size=n_agents), 0.25, 4.0),
        link_draws=draws,
    )


def equilibrium_activity(
    adjacency: np.ndarray,
    net_benefit: np.ndarray,
    beta: float,
) -> np.ndarray:
    """Solve the interior linear-quadratic network-game equilibrium."""
    matrix = np.asarray(adjacency, dtype=float)
    benefit = np.asarray(net_benefit, dtype=float)
    if matrix.shape != (benefit.size, benefit.size):
        raise ValueError("Adjacency and benefit dimensions do not agree")
    radius = float(np.max(np.abs(np.linalg.eigvalsh(matrix)))) if matrix.size else 0.0
    if beta < 0 or beta * radius >= 1:
        raise ValueError("Equilibrium requires beta >= 0 and beta * spectral_radius < 1")
    activity = np.linalg.solve(np.eye(benefit.size) - beta * matrix, benefit)
    return np.maximum(activity, 0.0)


def hhi(values: np.ndarray) -> float:
    values = np.maximum(np.asarray(values, dtype=float), 0.0)
    total = float(values.sum())
    if total <= 0:
        return 0.0
    shares = values / total
    return float(np.square(shares).sum())


def hhi_derivative(
    activity: np.ndarray,
    marginal_activity: np.ndarray,
) -> float:
    """Analytical derivative of activity HHI for a scalar policy change."""
    x = np.asarray(activity, dtype=float)
    dx = np.asarray(marginal_activity, dtype=float)
    total = float(x.sum())
    if total <= 0:
        return 0.0
    shares = x / total
    concentration = float(np.square(shares).sum())
    return float((2.0 / total) * np.sum((shares - concentration) * dx))


def _scenario_state(
    inputs: MatchedAgentInputs,
    settings: dict[str, Any],
    scenario: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gho = bool(scenario["gho"])
    cross_chain = bool(scenario["cross_chain"])
    outside = float(settings["outside_option"])

    gho_gain = float(settings["gho_access_gain"]) * inputs.gho_affinity if gho else 0.0
    ethereum_value = inputs.base_value - inputs.ethereum_cost + gho_gain

    if cross_chain:
        route_cost = float(settings["bridge_private_cost"])
        remote_value = (
            inputs.base_value
            - inputs.remote_cost
            + inputs.remote_affinity
            + gho_gain
            + float(settings["cross_chain_access_gain"])
            - route_cost
        )
    else:
        remote_value = np.full_like(ethereum_value, -np.inf)

    remote = remote_value > ethereum_value
    best_value = np.where(remote, remote_value, ethereum_value)
    net_benefit = np.maximum(best_value - outside, 0.0)
    venues = remote.astype(int)

    active_seed = net_benefit > 0
    fitness_product = np.sqrt(np.outer(inputs.fitness, inputs.fitness))
    fitness_product /= max(float(np.mean(fitness_product)), 1e-12)
    probability = float(settings["base_link_probability"]) * (
        0.35 + 0.65 * np.minimum(fitness_product, 3.0)
    )
    same_venue = venues[:, None] == venues[None, :]
    compatibility = np.where(
        same_venue,
        1.0,
        float(settings["cross_venue_link_factor"]) if cross_chain else 0.0,
    )
    eligible = active_seed[:, None] & active_seed[None, :]
    probability = np.clip(probability * compatibility * eligible, 0.0, 0.55)
    edges = (inputs.link_draws < probability).astype(float)
    np.fill_diagonal(edges, 0.0)

    weighted = edges * fitness_product
    radius = float(np.max(np.abs(np.linalg.eigvalsh(weighted)))) if weighted.any() else 0.0
    adjacency = weighted / radius if radius > 0 else weighted
    return venues, net_benefit, adjacency


def _structural_metrics(
    adjacency: np.ndarray,
    activity: np.ndarray,
    alpha: float,
    threshold: float,
) -> tuple[float, float]:
    active_nodes = np.flatnonzero(activity > threshold)
    n_active = active_nodes.size
    if n_active == 0:
        return 0.0, 0.0
    active_adjacency = adjacency[np.ix_(active_nodes, active_nodes)]
    if not active_adjacency.any():
        return 1.0 / n_active, 1.0
    centrality = np.linalg.solve(
        np.eye(n_active) - alpha * active_adjacency,
        np.ones(n_active),
    )
    structural_hhi = hhi(centrality)

    graph = nx.from_numpy_array((active_adjacency > 0).astype(int))
    if graph.number_of_nodes() < 2 or graph.number_of_edges() == 0:
        return structural_hhi, 1.0
    core_numbers = nx.core_number(graph)
    maximum = max(core_numbers.values())
    core_share = sum(value == maximum for value in core_numbers.values()) / len(core_numbers)
    return structural_hhi, float(core_share)


def _measure_scenario(
    scenario: dict[str, Any],
    settings: dict[str, Any],
    beta: float,
    venues: np.ndarray,
    net_benefit: np.ndarray,
    adjacency: np.ndarray,
) -> dict[str, float | int | str | bool]:
    activity = equilibrium_activity(adjacency, net_benefit, beta)
    total = float(activity.sum())
    threshold = float(settings["activity_threshold"])
    active = int(np.sum(activity > threshold))
    activity_concentration = hhi(activity)
    effective = float(1.0 / activity_concentration) if activity_concentration > 0 else 0.0

    chain_activity = np.array(
        [float(activity[venues == venue].sum()) for venue in sorted(set(venues.tolist()))]
    )
    chain_concentration = hhi(chain_activity)
    remote_share = float(activity[venues == 1].sum() / total) if total > 0 else 0.0
    routes = int(scenario["routes"])
    removal_loss = remote_share / routes if bool(scenario["cross_chain"]) and routes else 0.0
    route_hhi = 1.0 / routes if routes else 0.0
    structural_hhi, core_share = _structural_metrics(
        adjacency,
        activity,
        float(settings["structural_alpha"]),
        threshold,
    )

    private_surplus = (
        float(net_benefit @ activity)
        - 0.5 * float(activity @ activity)
        + beta * float(activity @ adjacency @ activity)
    )
    return {
        "scenario_id": str(scenario["id"]),
        "scenario": str(scenario["label"]),
        "gho": bool(scenario["gho"]),
        "cross_chain": bool(scenario["cross_chain"]),
        "routes": routes,
        "beta": beta,
        "active_entities": active,
        "total_activity": total,
        "effective_entities": effective,
        "activity_hhi": activity_concentration,
        "chain_hhi": chain_concentration,
        "structural_hhi": structural_hhi,
        "core_share": core_share,
        "route_hhi": route_hhi,
        "max_route_removal_loss": removal_loss,
        "private_surplus": private_surplus,
    }


def simulate(config: dict[str, Any]) -> pd.DataFrame:
    settings = config["simulation"]
    scenarios = config["scenarios"]
    inputs = _matched_inputs(int(settings["n_agents"]), int(settings["seed"]))
    rows: list[dict[str, float | int | str | bool]] = []
    for scenario in scenarios:
        venues, net_benefit, adjacency = _scenario_state(inputs, settings, scenario)
        for beta in settings["beta_grid"]:
            rows.append(
                _measure_scenario(
                    scenario,
                    settings,
                    float(beta),
                    venues,
                    net_benefit,
                    adjacency,
                )
            )
    return pd.DataFrame(rows)


def run_simulation(
    root: str | Path = ".",
    config_path: str | Path = "configs/simulation.yaml",
) -> dict[str, Path]:
    project = Path(root).resolve()
    source = Path(config_path)
    if not source.is_absolute():
        source = project / source
    config = load_yaml(source)
    results = simulate(config)

    output_dir = project / "outputs" / "simulation"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "scenario_results.csv"
    results.to_csv(results_path, index=False)

    reference_beta = float(config["simulation"]["reference_beta"])
    reference = results[np.isclose(results["beta"], reference_beta)].copy()
    table_path = write_simulation_result_table(
        reference,
        project / "paper" / "generated" / "tables" / "simulation_mechanism_check.tex",
    )

    manifest_path = output_dir / "manifest.json"
    write_manifest(
        manifest_path,
        {
            "project": "aave-bns",
            "artifact": "stylized network-game simulation",
            "synthetic": True,
            "empirically_calibrated": False,
            "warning": "Mechanism check only; not an empirical estimate.",
            "generated_at": utc_now_iso(),
            "config": str(source.relative_to(project)),
            "config_sha256": sha256_file(source),
            "results_sha256": sha256_file(results_path),
            "reference_beta": reference_beta,
        },
    )
    return {
        "results": results_path,
        "manifest": manifest_path,
        "paper_table": table_path,
    }
