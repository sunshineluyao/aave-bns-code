.PHONY: demo simulation test lint release-smoke release-contract result-index-validate reproduce-release verify-release-reference pipeline-figure pipeline-validate policy-assets verify-real-v2 reproduce-real-v2 verify-real-v3 reproduce-real-v3 real-v3-assets verify-real-v4 verify-real-v4-local reproduce-real-v4 real-v4-assets reproduce-real-v5-candidate real-v5-descriptive real-v5-topology real-v5-core-periphery real-v5-assets real-v5-core-periphery-assets real-v6-gnosis-assets network-glossary real-v5-pilot-did clean

PYTHON ?= python
DATASET_ROOT ?= ../aave-bns-data-HF

demo:
	PYTHONPATH=src $(PYTHON) -m aave_bns.cli demo

policy-assets:
	PYTHONPATH=src $(PYTHON) -m aave_bns.cli policy-assets

simulation:
	PYTHONPATH=src $(PYTHON) -m aave_bns.cli simulate

test:
	PYTHONPATH=src pytest

lint:
	ruff check src tests scripts

release-smoke:
	$(PYTHON) scripts/run_release_smoke.py

release-contract:
	$(PYTHON) scripts/validate_release_contract.py

result-index-validate:
	$(PYTHON) scripts/validate_result_replication_index.py \
		--dataset-root $(DATASET_ROOT)

reproduce-release:
	$(PYTHON) scripts/reproduce_release.py \
		--dataset-root $(DATASET_ROOT) \
		--checksum-scope all \
		--output-dir outputs/release_review

verify-release-reference:
	cd release && sha256sum -c reference_results.sha256

pipeline-figure:
	$(PYTHON) scripts/render_open_science_pipeline.py
	inkscape docs/open-science-pipeline/open_science_pipeline.svg \
		--export-type=pdf \
		--export-filename=docs/open-science-pipeline/open_science_pipeline.pdf
	inkscape docs/open-science-pipeline/open_science_pipeline.svg \
		--export-type=png \
		--export-width=2400 \
		--export-filename=docs/open-science-pipeline/open_science_pipeline.png

pipeline-validate: pipeline-figure
	$(PYTHON) scripts/validate_cross_repository_pipeline.py \
		--dataset-root $(DATASET_ROOT) \
		--json-out docs/open-science-pipeline/pipeline_validation.json

verify-real-v2:
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v2_ethereum.py

reproduce-real-v2:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v2_ethereum.py
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v2_ethereum.py --require-local-data

verify-real-v3:
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v3_entities.py

real-v3-assets:
	PYTHONPATH=src $(PYTHON) scripts/render_real_v3_entity_appendix.py

reproduce-real-v3:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v3_entities.py
	PYTHONPATH=src $(PYTHON) scripts/render_real_v3_entity_appendix.py
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v3_entities.py --require-local-data

verify-real-v4:
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v4_partial_identification.py

verify-real-v4-local:
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v4_partial_identification.py --require-local-data

real-v4-assets:
	PYTHONPATH=src $(PYTHON) scripts/render_real_v4_partial_identification.py

reproduce-real-v4:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v4_partial_identification.py
	PYTHONPATH=src $(PYTHON) scripts/render_real_v4_partial_identification.py
	PYTHONPATH=src $(PYTHON) scripts/verify_real_v4_partial_identification.py --require-local-data

reproduce-real-v5-candidate:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v5_arbitrum.py

real-v5-descriptive:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v5_descriptive.py \
		--ethereum outputs/real_v4/ethereum/beneficiary_event_panel.csv.gz \
		--arbitrum data/processed/real_v5/arbitrum/aave_v3_pool_actions.csv.gz

real-v5-topology:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v5_topology.py \
		--ethereum data/processed/real_v2/ethereum/aave_v3_pool_actions.csv.gz \
		--arbitrum data/processed/real_v5/arbitrum/aave_v3_pool_actions.csv.gz

real-v5-core-periphery:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v5_core_periphery.py \
		--ethereum data/processed/real_v2/ethereum/aave_v3_pool_actions.csv.gz \
		--arbitrum data/processed/real_v5/arbitrum/aave_v3_pool_actions.csv.gz

real-v5-assets:
	PYTHONPATH=src $(PYTHON) scripts/render_real_v5_paper_assets.py

real-v5-core-periphery-assets:
	PYTHONPATH=src $(PYTHON) scripts/render_real_v5_core_periphery.py

real-v6-gnosis-assets:
	PYTHONPATH=src $(PYTHON) scripts/render_real_v6_gnosis_paper_assets.py

network-glossary:
	PYTHONPATH=src $(PYTHON) scripts/render_network_measure_glossary.py

real-v5-pilot-did:
	PYTHONPATH=src $(PYTHON) scripts/run_real_v5_pilot_did.py

clean:
	rm -rf outputs/release_review outputs/simulation
