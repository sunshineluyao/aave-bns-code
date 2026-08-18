import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from aave_bns.real_v5_core_periphery import (
    _scan_be_order,
    analyze_chain,
    borgatti_everett_score,
    fit_rombach_aggregate,
    rombach_template,
)
from aave_bns.real_v5_topology import _pagerank_values


def _load_core_renderer():
    path = Path(__file__).resolve().parents[1] / "scripts/render_real_v5_core_periphery.py"
    spec = spec_from_file_location("render_real_v5_core_periphery", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load core-periphery renderer from {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core_renderer = _load_core_renderer()


def _ideal_core_periphery_graph() -> nx.Graph:
    graph = nx.Graph()
    core = ("0xa", "0xb")
    periphery = ("0xc", "0xd", "0xe", "0xf")
    graph.add_edge(*core, weight=4.0)
    for core_node in core:
        for peripheral_node in periphery:
            graph.add_edge(core_node, peripheral_node, weight=1.0)
    return graph


def _events() -> pd.DataFrame:
    rows = []
    action_cycle = ("supply", "withdraw", "borrow", "repay", "liquidation")
    for index, (left, right, data) in enumerate(_ideal_core_periphery_graph().edges(data=True)):
        for repeat in range(int(data["weight"])):
            rows.append(
                {
                    "chain_id": 1,
                    "action": action_cycle[len(rows) % len(action_cycle)],
                    "block_number": 100 + len(rows),
                    "tx_hash": f"0x{len(rows):064x}",
                    "log_index": len(rows),
                    "event_week": -16 if (index + repeat) % 2 == 0 else 16,
                    "reserve_address": "0xreserve",
                    "actor_address": left,
                    "beneficiary_address": right,
                }
            )
    return pd.DataFrame(rows)


def test_borgatti_everett_ideal_matrix_prefers_two_node_core():
    graph = _ideal_core_periphery_graph()
    nodes = sorted(graph)
    index = {node: position for position, node in enumerate(nodes)}
    neighbors = [
        np.asarray(sorted(index[neighbor] for neighbor in graph.neighbors(node)), dtype=np.int64)
        for node in nodes
    ]
    fitted, score, core_size = _scan_be_order(
        np.arange(len(nodes)), neighbors, edge_count=graph.number_of_edges()
    )
    assert core_size == 2
    assert fitted.tolist() == [True, True, False, False, False, False]
    assert np.isclose(score, 1.0)
    assert borgatti_everett_score(
        node_count=6, edge_count=9, core_size=2, peripheral_edge_count=0
    ) == 1.0


def test_rombach_profile_uses_one_based_equation_and_unit_sum():
    profile = rombach_template(4, alpha=0.5, beta=0.5)
    expected = np.array([0.125, 0.25, 0.875, 1.0])
    expected = expected / expected.sum()
    assert np.allclose(profile, expected)
    assert np.isclose(profile.sum(), 1.0)


def test_rombach_aggregate_is_deterministic_and_core_weighted():
    graph = _ideal_core_periphery_graph()
    core_number = nx.core_number(graph)
    directed = graph.to_directed()
    pagerank = dict(
        zip(directed, _pagerank_values(directed), strict=True)
    )
    first, first_summary = fit_rombach_aggregate(
        graph, core_number=core_number, pagerank=pagerank
    )
    second, second_summary = fit_rombach_aggregate(
        graph, core_number=core_number, pagerank=pagerank
    )
    assert first == second
    assert first_summary == second_summary
    assert max(first.values()) == 1.0
    assert min(first[node] for node in ("0xa", "0xb")) > max(
        first[node] for node in ("0xc", "0xd", "0xe", "0xf")
    )


def test_chain_summary_keeps_address_role_and_causal_gates_closed():
    nodes, summary = analyze_chain(_events(), chain="Ethereum")
    assert set(nodes["observed_unit"]) == {"address_role"}
    assert set(nodes["interpretation_status"]) == {"descriptive_noncausal"}
    assert summary["evidence_status"] == "observed_address_role_descriptive_noncausal"
    assert summary["borgatti_everett"]["core_node_count"] >= 2
    assert summary["maximum_k_core_node_count"] >= 2


@pytest.mark.parametrize(
    "events",
    [
        pytest.param(_events().iloc[0:0].copy(), id="empty"),
        pytest.param(
            _events().assign(beneficiary_address=lambda frame: frame["actor_address"]),
            id="self-directed-only",
        ),
    ],
)
def test_analyze_chain_rejects_topologies_without_delegated_edges(events):
    with pytest.raises(
        ValueError,
        match=(
            "Ethereum: no non-self actor-to-beneficiary edges remain after "
            "topology construction"
        ),
    ):
        analyze_chain(events, chain="Ethereum")


def test_node_draw_order_breaks_pagerank_ties_by_address():
    nodes = pd.DataFrame(
        {
            "address": ["0xc", "0xb", "0xa"],
            "pagerank": [0.2, 0.1, 0.1],
        }
    ).set_index("address")
    expected = ["0xa", "0xb", "0xc"]
    assert core_renderer.nodes_in_draw_order(nodes).index.tolist() == expected
    assert core_renderer.nodes_in_draw_order(nodes.iloc[::-1]).index.tolist() == expected


def test_renderer_rejects_full_node_artifact_drift(tmp_path, monkeypatch):
    corrupted = tmp_path / "node_coreness.csv.gz"
    corrupted.write_bytes(b"corrupted")
    monkeypatch.setattr(core_renderer, "FULL_NODE_PATH", corrupted)
    with pytest.raises(ValueError, match="core-periphery input hash drift"):
        core_renderer.load_inputs()


def test_figure_manifest_tracks_full_node_artifact():
    manifest = json.loads(core_renderer.MANIFEST_PATH.read_text(encoding="utf-8"))
    key = str(core_renderer.FULL_NODE_PATH.relative_to(core_renderer.ROOT))
    assert manifest["inputs"][key] == core_renderer.sha256(core_renderer.FULL_NODE_PATH)
