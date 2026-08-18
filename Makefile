.PHONY: demo simulation test lint release-smoke reproduce-release verify-release-reference policy-assets verify-real-v2 reproduce-real-v2 verify-real-v3 reproduce-real-v3 real-v3-assets verify-real-v4 verify-real-v4-local reproduce-real-v4 real-v4-assets reproduce-real-v5-candidate real-v5-descriptive real-v5-topology real-v5-core-periphery real-v5-assets real-v5-core-periphery-assets real-v6-gnosis-assets network-glossary real-v5-pilot-did publication-visual-assets paper paper-layout-audit submission clean

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
	ruff check src tests scripts hf_space

release-smoke:
	$(PYTHON) scripts/run_release_smoke.py

reproduce-release:
	$(PYTHON) scripts/reproduce_release.py \
		--dataset-root $(DATASET_ROOT) \
		--checksum-scope all \
		--output-dir outputs/release_review

verify-release-reference:
	cd release && sha256sum -c reference_results.sha256

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

publication-visual-assets: simulation real-v3-assets real-v4-assets real-v5-assets real-v5-core-periphery-assets real-v6-gnosis-assets network-glossary real-v5-pilot-did
	PYTHONPATH=src $(PYTHON) scripts/render_publication_visuals.py
	$(PYTHON) paper/scripts/generate_figures.py

paper: demo policy-assets publication-visual-assets
	$(MAKE) -C paper paper

paper-layout-audit:
	$(PYTHON) scripts/audit_pdf_layout.py --self-test
	$(PYTHON) scripts/audit_pdf_layout.py paper/main.pdf \
		--pages 4,10,27,29,32,37,63,65,67,68 --figure-regions \
		--json paper/layout_audit.json

submission: demo policy-assets publication-visual-assets
	$(MAKE) -C paper submission

clean:
	rm -rf outputs/* paper/generated/tables/* paper/generated/figures/*
	touch outputs/.gitkeep paper/generated/tables/.gitkeep paper/generated/figures/.gitkeep
	$(MAKE) -C paper clean
