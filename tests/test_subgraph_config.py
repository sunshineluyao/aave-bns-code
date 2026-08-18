from aave_bns.config import load_yaml


def test_official_aave_v3_subgraphs_are_registered_but_not_falsely_pinned():
    config = load_yaml("configs/subgraphs.yaml")
    assert config["source"]["owner"] == "aave"
    assert config["source"]["repository"] == "https://github.com/aave/protocol-subgraphs"

    required = {
        "ethereum_v3",
        "ethereum_v3_gho",
        "arbitrum_v3",
        "base_v3",
        "avalanche_v3",
        "gnosis_v3",
    }
    deployments = config["deployments"]
    assert required.issubset(deployments)

    subgraph_ids = [deployments[name]["subgraph_id"] for name in required]
    assert len(subgraph_ids) == len(set(subgraph_ids))
    assert all(identifier.isalnum() and len(identifier) >= 40 for identifier in subgraph_ids)
    # A mutable latest subgraph ID is not a pinned deployment. The validation gate
    # remains intentionally closed until immutable deployment IDs are recorded.
    assert all(deployments[name]["deployment_id"] is None for name in required)
    assert config["validation_protocol"]["completed"] is False
    assert all(int(deployments[name]["chain_id"]) > 0 for name in required)
