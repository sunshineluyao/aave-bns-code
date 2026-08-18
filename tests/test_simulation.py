import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import yaml

from aave_bns.config import load_yaml
from aave_bns.simulation import (
    equilibrium_activity,
    hhi,
    hhi_derivative,
    run_simulation,
    simulate,
)


def test_equilibrium_and_hhi_derivative_match_numerical_check():
    adjacency = np.array([
        [0.0, 0.4, 0.0],
        [0.4, 0.0, 0.3],
        [0.0, 0.3, 0.0],
    ])
    benefit = np.array([0.5, 0.7, 0.4])
    marginal_benefit = np.array([0.1, 0.0, 0.3])
    beta = 0.4

    activity = equilibrium_activity(adjacency, benefit, beta)
    residual = activity - benefit - beta * adjacency @ activity
    assert np.max(np.abs(residual)) < 1e-10

    multiplier = np.linalg.solve(
        np.eye(len(benefit)) - beta * adjacency,
        marginal_benefit,
    )
    analytical = hhi_derivative(activity, multiplier)
    epsilon = 1e-6
    perturbed = equilibrium_activity(
        adjacency,
        benefit + epsilon * marginal_benefit,
        beta,
    )
    numerical = (hhi(perturbed) - hhi(activity)) / epsilon
    assert np.isclose(analytical, numerical, rtol=1e-4, atol=1e-7)


def test_matched_cross_chain_scenarios_expose_route_tradeoff():
    config = deepcopy(load_yaml("configs/simulation.yaml"))
    config["simulation"]["n_agents"] = 80
    config["simulation"]["beta_grid"] = [0.5]
    results = simulate(config).set_index("scenario_id")

    benchmark = results.loc["ethereum_aave"]
    issuance = results.loc["ethereum_gho"]
    single = results.loc["crosschain_single"]
    redundant = results.loc["crosschain_redundant"]

    assert issuance["active_entities"] >= benchmark["active_entities"]
    assert single["chain_hhi"] < 1.0
    assert (results["structural_hhi"] <= 1.0).all()
    assert (
        results["structural_hhi"]
        >= 1.0 / results["active_entities"].astype(float)
    ).all()
    assert 0.0 < single["max_route_removal_loss"] <= 0.5
    assert np.isclose(single["total_activity"], redundant["total_activity"])
    assert np.isclose(single["activity_hhi"], redundant["activity_hhi"])
    assert np.isclose(
        redundant["max_route_removal_loss"],
        single["max_route_removal_loss"] / 2,
    )


def test_simulation_outputs_are_explicitly_uncalibrated(tmp_path: Path):
    config = deepcopy(load_yaml("configs/simulation.yaml"))
    config["simulation"]["n_agents"] = 60
    config["simulation"]["beta_grid"] = [0.5]
    config_path = tmp_path / "configs" / "simulation.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    outputs = run_simulation(tmp_path)
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert manifest["empirically_calibrated"] is False
    assert outputs["paper_table"].exists()
