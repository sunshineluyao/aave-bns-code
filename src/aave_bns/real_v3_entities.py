from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .evm_rpc import RpcClient, canonical_json_bytes
from .real_v2_ethereum import (
    _read_jsonl_gzip,
    _write_deterministic_jsonl_gzip,
    project_relative_path,
    safe_rpc_endpoint,
    sha256_file,
    source_revision,
    utc_now_iso,
)
from .transform import normalize_address

PARTICIPANT_COLUMNS = (
    "actor_address",
    "beneficiary_address",
    "counterparty_address",
)

REQUIRED_EVENT_COLUMNS = {
    "chain_id",
    "action",
    "block_number",
    "tx_hash",
    "log_index",
    "event_week",
    *PARTICIPANT_COLUMNS,
}

REQUIRED_LABEL_COLUMNS = {
    "release_version",
    "chain_id",
    "address",
    "address_label",
    "entity_id",
    "entity_label",
    "entity_category",
    "entity_scope",
    "infrastructure_category",
    "source_id",
    "source_url",
    "source_revision",
    "source_path",
    "valid_from_block",
    "valid_to_block",
    "confidence",
    "review_status",
    "notes",
}


def load_real_v3_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("real_v3 configuration must be a YAML object")
    if int(config.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported real_v3 configuration schema")
    return config


def _clean_optional_address(value: object) -> str | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return normalize_address(value)


def build_address_universe(
    events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the observed participant universe and one incidence per event-address.

    If one address occupies two roles in the same event (for example actor and
    beneficiary), the address receives one event incidence.  The complete role set is
    retained separately so that measurement breadth is not inflated by ABI role aliases.
    """
    missing = REQUIRED_EVENT_COLUMNS.difference(events.columns)
    if missing:
        raise ValueError(f"Missing real_v2 event columns: {sorted(missing)}")

    frame = events.copy()
    frame["chain_id"] = pd.to_numeric(frame["chain_id"], errors="raise").astype("int64")
    frame["block_number"] = pd.to_numeric(
        frame["block_number"], errors="raise"
    ).astype("int64")
    frame["log_index"] = pd.to_numeric(frame["log_index"], errors="raise").astype("int64")
    frame["event_week"] = pd.to_numeric(frame["event_week"], errors="raise").astype("int64")
    frame["action"] = frame["action"].astype(str).str.lower().str.strip()
    if frame[["tx_hash", "log_index"]].duplicated().any():
        raise ValueError("real_v2 events contain duplicate transaction-log keys")

    role_frames: list[pd.DataFrame] = []
    for column in PARTICIPANT_COLUMNS:
        role = column.removesuffix("_address")
        selected = frame[
            ["chain_id", "action", "block_number", "tx_hash", "log_index", "event_week", column]
        ].copy()
        selected["address"] = selected[column].map(_clean_optional_address)
        selected = selected.dropna(subset=["address"]).drop(columns=[column])
        selected["observed_role"] = role
        role_frames.append(selected)

    role_rows = pd.concat(role_frames, ignore_index=True)
    event_key = ["chain_id", "tx_hash", "log_index", "address"]
    role_sets = (
        role_rows.groupby(event_key, observed=True)["observed_role"]
        .agg(lambda values: "|".join(sorted(set(values))))
        .rename("roles_in_event")
        .reset_index()
    )
    incidences = (
        role_rows.drop(columns=["observed_role"])
        .drop_duplicates(event_key)
        .merge(role_sets, on=event_key, how="left", validate="one_to_one")
        .sort_values(["block_number", "tx_hash", "log_index", "address"])
        .reset_index(drop=True)
    )

    grouping = ["chain_id", "address"]
    role_summary = (
        role_rows.groupby(grouping, observed=True, sort=True)
        .agg(
            observed_roles=(
                "observed_role",
                lambda values: "|".join(sorted(set(values))),
            ),
            observed_actions=("action", lambda values: "|".join(sorted(set(values)))),
            first_observed_block=("block_number", "min"),
            last_observed_block=("block_number", "max"),
            first_event_week=("event_week", "min"),
            last_event_week=("event_week", "max"),
            role_incidence_count=("address", "size"),
            transaction_count=("tx_hash", "nunique"),
        )
        .reset_index()
    )
    incidence_summary = (
        incidences.groupby(grouping, observed=True, sort=True)
        .size()
        .rename("event_incidence_count")
        .reset_index()
    )
    universe = (
        role_summary.merge(
            incidence_summary, on=grouping, how="left", validate="one_to_one"
        )
        .sort_values(grouping)
        .reset_index(drop=True)
    )
    if universe["address"].duplicated().any():
        raise ValueError("Address universe contains duplicate addresses")
    if int(universe["event_incidence_count"].sum()) != len(incidences):
        raise ValueError("Address-universe incidences do not reconcile")
    return universe, incidences


def _validate_code(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("eth_getCode returned malformed bytecode")
    payload = value[2:]
    if len(payload) % 2 or any(character not in "0123456789abcdefABCDEF" for character in payload):
        raise ValueError("eth_getCode returned invalid hexadecimal bytecode")
    return value.lower()


def _validate_code_chunk(
    records: list[dict[str, Any]], expected: pd.DataFrame
) -> list[dict[str, Any]]:
    expected_addresses = expected["address"].tolist()
    observed_addresses = [record.get("address") for record in records]
    if observed_addresses != expected_addresses:
        raise ValueError("Cached code chunk does not match the expected address order")
    validated: list[dict[str, Any]] = []
    for record, row in zip(records, expected.itertuples(index=False), strict=True):
        first_block = int(record.get("first_observed_block", -1))
        last_block = int(record.get("last_observed_block", -1))
        if first_block != int(row.first_observed_block) or last_block != int(
            row.last_observed_block
        ):
            raise ValueError(f"Cached code chunk has stale block bounds for {row.address}")
        validated.append(
            {
                "address": str(row.address),
                "first_observed_block": first_block,
                "first_runtime_code": _validate_code(record.get("first_runtime_code")),
                "last_observed_block": last_block,
                "last_runtime_code": _validate_code(record.get("last_runtime_code")),
            }
        )
    return validated


def fetch_address_code_snapshots(
    universe: pd.DataFrame,
    *,
    client: RpcClient,
    raw_directory: str | Path,
    project_root: str | Path,
    addresses_per_batch: int,
    workers: int,
    resume: bool,
    progress_every: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if addresses_per_batch < 1 or workers < 1:
        raise ValueError("Batch size and worker count must be positive")
    raw_path = Path(raw_directory)
    raw_path.mkdir(parents=True, exist_ok=True)
    chunks = [
        universe.iloc[start : start + addresses_per_batch].copy()
        for start in range(0, len(universe), addresses_per_batch)
    ]

    def fetch(
        index: int, expected: pd.DataFrame
    ) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        path = raw_path / f"addresses_{index:04d}.jsonl.gz"
        if resume and path.exists():
            records = _validate_code_chunk(_read_jsonl_gzip(path), expected)
        else:
            calls: list[tuple[str, list[Any]]] = []
            call_keys: list[tuple[str, str]] = []
            for row in expected.itertuples(index=False):
                calls.append(("eth_getCode", [row.address, hex(int(row.first_observed_block))]))
                call_keys.append((row.address, "first"))
                if int(row.last_observed_block) != int(row.first_observed_block):
                    calls.append(("eth_getCode", [row.address, hex(int(row.last_observed_block))]))
                    call_keys.append((row.address, "last"))
            results = client.batch_call(calls)
            by_address: dict[str, dict[str, str]] = {}
            for (address, snapshot), result in zip(call_keys, results, strict=True):
                by_address.setdefault(address, {})[snapshot] = _validate_code(result)
            records = []
            for row in expected.itertuples(index=False):
                first_code = by_address[row.address]["first"]
                last_code = by_address[row.address].get("last", first_code)
                records.append(
                    {
                        "address": row.address,
                        "first_observed_block": int(row.first_observed_block),
                        "first_runtime_code": first_code,
                        "last_observed_block": int(row.last_observed_block),
                        "last_runtime_code": last_code,
                    }
                )
            _write_deterministic_jsonl_gzip(path, records)
            records = _validate_code_chunk(_read_jsonl_gzip(path), expected)
        metadata = {
            "batch_index": index,
            "address_count": len(records),
            "rpc_call_count": sum(
                1 + int(record["last_observed_block"] != record["first_observed_block"])
                for record in records
            ),
            "first_address": records[0]["address"],
            "last_address": records[-1]["address"],
            # A validated resumed file has the same acquisition provenance as a fresh
            # response.  Keeping this value stable makes release metadata independent
            # of whether an interrupted extraction resumed from its local cache.
            "source": "primary_rpc",
            "path": project_relative_path(path, project_root),
            "compressed_file_sha256": sha256_file(path),
            "canonical_records_sha256": canonical_records_sha256(records),
            "compressed_bytes": path.stat().st_size,
        }
        return index, records, metadata

    completed: dict[int, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch, index, chunk): index for index, chunk in enumerate(chunks)
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            index, records, metadata = future.result()
            completed[index] = (records, metadata)
            if progress_every and (
                completed_count % progress_every == 0 or completed_count == len(chunks)
            ):
                print(f"real_v3 code snapshots: {completed_count}/{len(chunks)} batches complete")

    all_records: list[dict[str, Any]] = []
    batch_records: list[dict[str, Any]] = []
    for index in range(len(chunks)):
        records, metadata = completed[index]
        all_records.extend(records)
        batch_records.append(metadata)
    if len(all_records) != len(universe):
        raise ValueError("Code snapshot count does not match address universe")
    return all_records, batch_records


def _runtime_code_facts(code: str) -> tuple[bool, int, str]:
    validated = _validate_code(code)
    if validated == "0x":
        return False, 0, ""
    payload = bytes.fromhex(validated[2:])
    return True, len(payload), hashlib.sha256(payload).hexdigest()


def classify_code_snapshots(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        first_present, first_bytes, first_hash = _runtime_code_facts(
            record["first_runtime_code"]
        )
        last_present, last_bytes, last_hash = _runtime_code_facts(record["last_runtime_code"])
        if not first_present and not last_present:
            presence_class = "no_runtime_code_observed"
            address_type = "code_absent_at_observed_bounds"
            family = ""
        elif first_present and last_present and first_hash == last_hash:
            presence_class = "stable_runtime_code"
            address_type = "smart_contract"
            family = f"runtime_sha256:{first_hash}"
        elif not first_present and last_present:
            presence_class = "runtime_code_appeared"
            address_type = "smart_contract_dynamic"
            family = f"dynamic_address:{record['address']}"
        elif first_present and not last_present:
            presence_class = "runtime_code_disappeared"
            address_type = "smart_contract_dynamic"
            family = f"dynamic_address:{record['address']}"
        else:
            presence_class = "runtime_code_changed"
            address_type = "smart_contract_dynamic"
            family = f"dynamic_address:{record['address']}"
        rows.append(
            {
                "address": record["address"],
                "address_type": address_type,
                "code_presence_class": presence_class,
                "first_runtime_code_present": first_present,
                "first_runtime_code_bytes": first_bytes,
                "first_runtime_code_sha256": first_hash,
                "last_runtime_code_present": last_present,
                "last_runtime_code_bytes": last_bytes,
                "last_runtime_code_sha256": last_hash,
                "contract_observed": first_present or last_present,
                "infrastructure_family_id": family,
            }
        )
    return pd.DataFrame(rows).sort_values("address").reset_index(drop=True)


def load_curated_labels(
    path: str | Path, *, release_version: str, chain_id: int
) -> pd.DataFrame:
    labels = pd.read_csv(path, keep_default_na=False, dtype=str)
    missing = REQUIRED_LABEL_COLUMNS.difference(labels.columns)
    if missing:
        raise ValueError(f"Curated labels are missing columns: {sorted(missing)}")
    labels = labels[
        (labels["release_version"] == release_version)
        & (pd.to_numeric(labels["chain_id"], errors="raise") == chain_id)
    ].copy()
    labels["address"] = labels["address"].map(normalize_address)
    labels["confidence"] = pd.to_numeric(labels["confidence"], errors="raise")
    labels["valid_from_block"] = pd.to_numeric(
        labels["valid_from_block"], errors="raise"
    ).astype("int64")
    labels["valid_to_block"] = pd.to_numeric(
        labels["valid_to_block"], errors="raise"
    ).astype("int64")
    if labels["address"].duplicated().any():
        raise ValueError("Curated release contains duplicate address rows")
    if ((labels["confidence"] < 0) | (labels["confidence"] > 1)).any():
        raise ValueError("Curated label confidence must lie in [0, 1]")
    if (labels["valid_to_block"] < labels["valid_from_block"]).any():
        raise ValueError("Curated label validity interval is reversed")
    allowed_scopes = {"economic_actor", "protocol_infrastructure", "asset_infrastructure"}
    if not set(labels["entity_scope"]).issubset(allowed_scopes):
        raise ValueError("Curated labels contain an unsupported entity_scope")
    if (~labels["source_url"].str.startswith("https://")).any():
        raise ValueError("Every curated label must cite an HTTPS source URL")
    return labels.sort_values("address").reset_index(drop=True)


def build_versioned_registry(
    universe: pd.DataFrame,
    code_facts: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    release_version: str,
    minimum_confidence: float,
) -> pd.DataFrame:
    registry = universe.merge(code_facts, on="address", how="left", validate="one_to_one")
    label_columns = [column for column in labels.columns if column not in {"chain_id"}]
    registry = registry.merge(
        labels[label_columns], on="address", how="left", validate="one_to_one"
    )
    for column in (
        "address_label",
        "entity_id",
        "entity_label",
        "entity_category",
        "entity_scope",
        "infrastructure_category",
        "source_id",
        "source_url",
        "source_revision",
        "source_path",
        "review_status",
        "notes",
    ):
        registry[column] = registry[column].fillna("").astype(str)
    registry["confidence"] = pd.to_numeric(registry["confidence"], errors="coerce").fillna(0.0)
    registry["valid_from_block"] = pd.to_numeric(
        registry["valid_from_block"], errors="coerce"
    ).fillna(registry["first_observed_block"])
    registry["valid_to_block"] = pd.to_numeric(
        registry["valid_to_block"], errors="coerce"
    ).fillna(registry["last_observed_block"])
    registry["valid_from_block"] = registry["valid_from_block"].astype("int64")
    registry["valid_to_block"] = registry["valid_to_block"].astype("int64")
    # The registry release applies to every row, including unresolved addresses.  The
    # input label file was already filtered to this release, so preserving blanks here
    # would make the registry version ambiguous for the unlabelled majority.
    registry["release_version"] = release_version
    registry["label_release_version"] = release_version
    registry["address_type_basis"] = "eth_getCode_at_first_and_last_observed_blocks"
    registry["address_type_confidence"] = 1.0
    registry["high_confidence_entity_label"] = (
        registry["entity_id"].ne("") & registry["confidence"].ge(minimum_confidence)
    )
    registry["economic_actor_resolved"] = (
        registry["high_confidence_entity_label"]
        & registry["entity_scope"].eq("economic_actor")
    )
    registry["curated_entity_key"] = registry["address"].map(lambda value: f"address:{value}")
    accepted = registry["high_confidence_entity_label"]
    registry.loc[accepted, "curated_entity_key"] = registry.loc[accepted, "entity_id"]
    registry["entity_resolution"] = "unresolved_address_fallback"
    registry.loc[accepted, "entity_resolution"] = "high_confidence_curated_label"

    labelled_without_code = registry["high_confidence_entity_label"] & ~registry[
        "contract_observed"
    ]
    if labelled_without_code.any():
        bad = registry.loc[labelled_without_code, "address"].tolist()
        raise ValueError(f"Curated contract labels have no runtime code evidence: {bad}")
    if registry["address"].duplicated().any() or len(registry) != len(universe):
        raise ValueError("Versioned registry does not preserve the address universe")
    return registry.sort_values(["chain_id", "address"]).reset_index(drop=True)


def _hhi_and_effective(keys: pd.Series) -> tuple[float, float]:
    counts = keys.value_counts(dropna=False)
    if counts.empty:
        return 0.0, 0.0
    shares = counts.astype(float) / float(counts.sum())
    hhi = float((shares**2).sum())
    return hhi, float(1.0 / hhi) if hhi > 0 else 0.0


def _measurement_row(group: pd.DataFrame) -> dict[str, Any]:
    address_hhi, effective_addresses = _hhi_and_effective(group["address"])
    curated_hhi, effective_curated = _hhi_and_effective(group["curated_entity_key"])
    extreme_keys = group["curated_entity_key"].copy()
    unresolved = ~group["high_confidence_entity_label"]
    extreme_keys.loc[unresolved] = "unresolved:all_addresses_collapsed"
    extreme_hhi, effective_extreme = _hhi_and_effective(extreme_keys)

    contracts = group[group["contract_observed"]]
    if contracts.empty:
        template_hhi = 0.0
        top_template_share = 0.0
    else:
        family_counts = (
            contracts["infrastructure_family_id"].replace("", pd.NA).dropna().value_counts()
        )
        family_shares = family_counts.astype(float) / float(family_counts.sum())
        template_hhi = float((family_shares**2).sum())
        top_template_share = float(family_shares.max())

    labelled = group["high_confidence_entity_label"]
    economic = group["economic_actor_resolved"]
    protocol = group["entity_scope"].eq("protocol_infrastructure")
    return {
        "event_incidence_count": int(len(group)),
        "active_addresses": int(group["address"].nunique()),
        "address_activity_hhi": address_hhi,
        "effective_active_addresses": effective_addresses,
        "curated_entity_activity_hhi_sensitivity": curated_hhi,
        "effective_curated_entities_sensitivity": effective_curated,
        "unresolved_all_one_hhi_extreme": extreme_hhi,
        "effective_entities_unresolved_all_one_extreme": effective_extreme,
        "contract_incidence_share": float(group["contract_observed"].mean()),
        "protocol_infrastructure_incidence_share": float(protocol.mean()),
        "high_confidence_entity_incidence_coverage": float(labelled.mean()),
        "economic_actor_incidence_coverage": float(economic.mean()),
        "contract_template_hhi_conditional": template_hhi,
        "top_contract_template_share_conditional": top_template_share,
    }


def build_measurement_panels(
    incidences: pd.DataFrame, registry: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fields = [
        "address",
        "contract_observed",
        "infrastructure_family_id",
        "curated_entity_key",
        "high_confidence_entity_label",
        "economic_actor_resolved",
        "entity_scope",
    ]
    merged = incidences.merge(
        registry[fields], on="address", how="left", validate="many_to_one"
    )
    if merged["contract_observed"].isna().any():
        raise ValueError("Measurement incidences are missing registry rows")

    def panel(grouping: list[str]) -> pd.DataFrame:
        rows = []
        for keys, group in merged.groupby(grouping, observed=True, sort=True):
            key_tuple = keys if isinstance(keys, tuple) else (keys,)
            rows.append(dict(zip(grouping, key_tuple, strict=True), **_measurement_row(group)))
        observed = pd.DataFrame(rows).set_index(grouping)
        weeks = range(int(merged["event_week"].min()), int(merged["event_week"].max()) + 1)
        if grouping == ["event_week", "action"]:
            complete_index = pd.MultiIndex.from_product(
                [weeks, sorted(merged["action"].unique())], names=grouping
            )
        else:
            complete_index = pd.Index(weeks, name="event_week")
        complete = observed.reindex(complete_index, fill_value=0).reset_index()
        integer_columns = ["event_incidence_count", "active_addresses"]
        for column in integer_columns:
            complete[column] = complete[column].astype("int64")
        return complete.sort_values(grouping).reset_index(drop=True)

    return panel(["event_week", "action"]), panel(["event_week"])


def build_address_type_summary(registry: pd.DataFrame) -> pd.DataFrame:
    total_addresses = len(registry)
    total_incidences = int(registry["event_incidence_count"].sum())
    rows = []
    for (address_type, presence), group in registry.groupby(
        ["address_type", "code_presence_class"], observed=True, sort=True
    ):
        incidences = int(group["event_incidence_count"].sum())
        rows.append(
            {
                "address_type": address_type,
                "code_presence_class": presence,
                "address_count": len(group),
                "address_share": float(len(group) / total_addresses),
                "event_incidence_count": incidences,
                "event_incidence_share": float(incidences / total_incidences),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["event_incidence_count", "address_count"], ascending=False
    ).reset_index(drop=True)


def build_infrastructure_family_summary(registry: pd.DataFrame) -> pd.DataFrame:
    contracts = registry[registry["contract_observed"]].copy()
    total_contract_incidences = int(contracts["event_incidence_count"].sum())
    total_incidences = int(registry["event_incidence_count"].sum())
    rows = []
    for family, group in contracts.groupby("infrastructure_family_id", observed=True, sort=True):
        incidences = int(group["event_incidence_count"].sum())
        rows.append(
            {
                "infrastructure_family_id": family,
                "address_count": len(group),
                "event_incidence_count": incidences,
                "share_of_contract_incidences": float(incidences / total_contract_incidences),
                "share_of_all_incidences": float(incidences / total_incidences),
                "curated_entity_ids": "|".join(
                    sorted(value for value in set(group["entity_id"]) if value)
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["event_incidence_count", "address_count", "infrastructure_family_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def deterministic_validation_sample(registry: pd.DataFrame, sample_count: int) -> pd.DataFrame:
    if sample_count < 4:
        raise ValueError("Validation sample count must be at least four")
    contract = registry[registry["contract_observed"]].nlargest(
        max(1, sample_count // 4), "event_incidence_count"
    )
    code_absent = registry[~registry["contract_observed"]].nlargest(
        max(1, sample_count // 4), "event_incidence_count"
    )
    selected = pd.concat([contract, code_absent]).drop_duplicates("address")
    remaining = registry[~registry["address"].isin(selected["address"])].copy()
    remaining["sample_rank"] = remaining["address"].map(
        lambda value: hashlib.sha256(f"real_v3_validation:{value}".encode()).hexdigest()
    )
    selected = pd.concat(
        [
            selected,
            remaining.sort_values("sample_rank").head(sample_count - len(selected)),
        ]
    ).head(sample_count)
    return selected.sort_values("address").reset_index(drop=True)


def cross_provider_code_checks(
    registry: pd.DataFrame,
    code_records: list[dict[str, Any]],
    *,
    client: RpcClient,
    sample_count: int,
) -> pd.DataFrame:
    sample = deterministic_validation_sample(registry, sample_count)
    primary = {record["address"]: record for record in code_records}
    calls: list[tuple[str, list[Any]]] = []
    keys: list[tuple[str, str, int]] = []
    for row in sample.itertuples(index=False):
        calls.append(("eth_getCode", [row.address, hex(int(row.first_observed_block))]))
        keys.append((row.address, "first", int(row.first_observed_block)))
        calls.append(("eth_getCode", [row.address, hex(int(row.last_observed_block))]))
        keys.append((row.address, "last", int(row.last_observed_block)))
    results = client.batch_call(calls)
    rows = []
    for (address, snapshot, block_number), result in zip(keys, results, strict=True):
        validation_code = _validate_code(result)
        primary_code = primary[address][f"{snapshot}_runtime_code"]
        _, primary_bytes, primary_hash = _runtime_code_facts(primary_code)
        _, validation_bytes, validation_hash = _runtime_code_facts(validation_code)
        rows.append(
            {
                "address": address,
                "snapshot": snapshot,
                "block_number": block_number,
                "primary_code_bytes": primary_bytes,
                "validation_code_bytes": validation_bytes,
                "primary_code_sha256": primary_hash,
                "validation_code_sha256": validation_hash,
                "exact_match": primary_code == validation_code,
            }
        )
    checks = pd.DataFrame(rows).sort_values(["address", "snapshot"]).reset_index(drop=True)
    if not checks["exact_match"].all():
        raise ValueError("Independent-provider code validation failed")
    return checks


def canonical_records_sha256(records: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(canonical_json_bytes(record))
        digest.update(b"\n")
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv_atomic(frame: pd.DataFrame, path: str | Path) -> None:
    destination = Path(path)
    _atomic_write_text(destination, frame.to_csv(index=False, lineterminator="\n"))


def write_gzip_csv_atomic(frame: pd.DataFrame, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with temporary.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename=destination.name.removesuffix(".gz"),
                fileobj=raw_handle,
                mode="wb",
                compresslevel=9,
                mtime=0,
            ) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text_handle:
                    frame.to_csv(text_handle, index=False, lineterminator="\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def gzip_payload_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_real_v3_entity_layer(
    config_path: str | Path,
    *,
    project_root: str | Path = ".",
    resume: bool = True,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    configuration_path = Path(config_path)
    if not configuration_path.is_absolute():
        configuration_path = project / configuration_path
    config = load_real_v3_config(configuration_path)
    release_version = str(config["release_version"])
    chain_id = int(config["chain"]["chain_id"])
    retrieval = config["retrieval"]
    gates = config["entity_gate"]

    input_path = project / config["input"]["processed_events"]
    labels_path = project / config["input"]["curated_labels"]
    source_catalog_path = project / config["input"]["source_catalog"]
    raw_directory = project / retrieval["raw_code_directory"]
    output_directory = project / config["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)

    events = pd.read_csv(input_path, dtype=str)
    universe, incidences = build_address_universe(events)
    if set(universe["chain_id"]) != {chain_id}:
        raise ValueError("Address universe does not match the configured chain")

    primary_config = config["chain"]["primary_rpc"]
    validation_config = config["chain"]["validation_rpc"]
    primary_url = os.getenv(primary_config["environment_variable"], primary_config["url"])
    validation_url = os.getenv(
        validation_config["environment_variable"], validation_config["url"]
    )
    primary = RpcClient(
        primary_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
    )
    validation = RpcClient(
        validation_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
    )
    if primary.chain_id() != chain_id or validation.chain_id() != chain_id:
        raise ValueError("A real_v3 RPC endpoint returned the wrong chain ID")

    code_records, batch_records = fetch_address_code_snapshots(
        universe,
        client=primary,
        raw_directory=raw_directory,
        project_root=project,
        addresses_per_batch=int(retrieval["addresses_per_batch"]),
        workers=int(retrieval["workers"]),
        resume=resume,
    )
    code_facts = classify_code_snapshots(code_records)
    labels = load_curated_labels(
        labels_path, release_version=release_version, chain_id=chain_id
    )
    registry = build_versioned_registry(
        universe,
        code_facts,
        labels,
        release_version=release_version,
        minimum_confidence=float(gates["minimum_confidence"]),
    )
    weekly_action, weekly = build_measurement_panels(incidences, registry)
    address_types = build_address_type_summary(registry)
    infrastructure = build_infrastructure_family_summary(registry)
    provider_checks = cross_provider_code_checks(
        registry,
        code_records,
        client=validation,
        sample_count=int(retrieval["validation_sample_count"]),
    )

    total_incidences = int(registry["event_incidence_count"].sum())
    labelled_addresses = int(registry["high_confidence_entity_label"].sum())
    labelled_incidences = int(
        registry.loc[registry["high_confidence_entity_label"], "event_incidence_count"].sum()
    )
    economic_addresses = int(registry["economic_actor_resolved"].sum())
    economic_incidences = int(
        registry.loc[registry["economic_actor_resolved"], "event_incidence_count"].sum()
    )
    unique_coverage = float(labelled_addresses / len(registry))
    incidence_coverage = float(labelled_incidences / total_incidences)
    economic_incidence_coverage = float(economic_incidences / total_incidences)
    gate_pass = (
        unique_coverage >= float(gates["minimum_unique_address_coverage"])
        and incidence_coverage >= float(gates["minimum_incidence_coverage"])
        and economic_incidence_coverage
        >= float(gates["minimum_economic_actor_incidence_coverage"])
    )

    summary = {
        "schema_version": 1,
        "release_version": release_version,
        "status": "descriptive_contract_and_entity_annotation_layer",
        "chain_id": chain_id,
        "address_count": len(registry),
        "event_incidence_count": total_incidences,
        "role_incidence_count": int(registry["role_incidence_count"].sum()),
        "smart_contract_address_count": int(registry["contract_observed"].sum()),
        "code_absent_address_count": int((~registry["contract_observed"]).sum()),
        "contract_event_incidence_count": int(
            registry.loc[registry["contract_observed"], "event_incidence_count"].sum()
        ),
        "contract_event_incidence_share": float(
            registry.loc[registry["contract_observed"], "event_incidence_count"].sum()
            / total_incidences
        ),
        "curated_label_address_count": labelled_addresses,
        "curated_label_event_incidence_count": labelled_incidences,
        "curated_label_unique_address_coverage": unique_coverage,
        "curated_label_event_incidence_coverage": incidence_coverage,
        "economic_actor_address_count": economic_addresses,
        "economic_actor_event_incidence_count": economic_incidences,
        "economic_actor_event_incidence_coverage": economic_incidence_coverage,
        "infrastructure_family_count": int(len(infrastructure)),
        "weekly_action_panel_rows": len(weekly_action),
        "weekly_panel_rows": len(weekly),
        "rpc_batch_count": len(batch_records),
        "validation_check_count": len(provider_checks),
        "validation_checks_exact": bool(provider_checks["exact_match"].all()),
        "entity_gate": {
            "minimum_confidence": float(gates["minimum_confidence"]),
            "minimum_unique_address_coverage": float(
                gates["minimum_unique_address_coverage"]
            ),
            "minimum_incidence_coverage": float(gates["minimum_incidence_coverage"]),
            "minimum_economic_actor_incidence_coverage": float(
                gates["minimum_economic_actor_incidence_coverage"]
            ),
            "passed": gate_pass,
        },
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
        "limitations": [
            "No-runtime-code observations are not interpreted as natural persons.",
            "Shared runtime bytecode is an infrastructure template, not common ownership.",
            "Only primary-source curated labels may merge addresses into an entity.",
            "Unresolved addresses remain separate in the curated sensitivity estimate.",
            "The all-unresolved-one scenario is a mechanical concentration extreme, "
            "not an estimate.",
            "This release does not promote entity-sensitivity outcomes to primary results.",
        ],
    }

    registry_path = output_directory / "address_registry.csv.gz"
    weekly_action_path = output_directory / "weekly_action_entity_sensitivity.csv"
    weekly_path = output_directory / "weekly_entity_sensitivity.csv"
    address_type_path = output_directory / "address_type_summary.csv"
    infrastructure_path = output_directory / "infrastructure_family_summary.csv"
    provider_checks_path = output_directory / "cross_provider_code_checks.csv"
    batches_path = output_directory / "code_retrieval_batches.csv"
    sample_path = output_directory / "registry_review_sample.csv"
    summary_path = output_directory / "summary.json"
    manifest_path = output_directory / "manifest.json"

    write_gzip_csv_atomic(registry, registry_path)
    write_csv_atomic(weekly_action, weekly_action_path)
    write_csv_atomic(weekly, weekly_path)
    write_csv_atomic(address_types, address_type_path)
    write_csv_atomic(infrastructure, infrastructure_path)
    write_csv_atomic(provider_checks, provider_checks_path)
    write_csv_atomic(pd.DataFrame(batch_records), batches_path)
    write_csv_atomic(
        registry.nlargest(50, "event_incidence_count").drop(
            columns=["first_runtime_code_sha256", "last_runtime_code_sha256"], errors="ignore"
        ),
        sample_path,
    )
    _atomic_write_text(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")

    tracked = [
        registry_path,
        weekly_action_path,
        weekly_path,
        address_type_path,
        infrastructure_path,
        provider_checks_path,
        batches_path,
        sample_path,
        summary_path,
    ]
    manifest = {
        "schema_version": 1,
        "pipeline": "real_v3_ethereum_address_role_entity_layer",
        "generated_at": utc_now_iso(),
        "source_revision": source_revision(project),
        "path_policy": "repository_relative_posix",
        "release_version": release_version,
        "configuration": {
            "path": project_relative_path(configuration_path, project),
            "sha256": sha256_file(configuration_path),
        },
        "inputs": {
            project_relative_path(input_path, project): sha256_file(input_path),
            project_relative_path(labels_path, project): sha256_file(labels_path),
            project_relative_path(source_catalog_path, project): sha256_file(
                source_catalog_path
            ),
        },
        "code": {
            relative_path: sha256_file(project / relative_path)
            for relative_path in (
                "src/aave_bns/evm_rpc.py",
                "src/aave_bns/real_v3_entities.py",
                "scripts/run_real_v3_entities.py",
            )
        },
        "providers": {
            "primary": {
                "source_id": primary_config["source_id"],
                "endpoint": safe_rpc_endpoint(primary_url),
                "stats": primary.stats.to_dict(),
            },
            "validation": {
                "source_id": validation_config["source_id"],
                "endpoint": safe_rpc_endpoint(validation_url),
                "stats": validation.stats.to_dict(),
            },
        },
        "raw_code_snapshot_canonical_sha256": canonical_records_sha256(code_records),
        "registry_canonical_csv_sha256": gzip_payload_sha256(registry_path),
        "raw_batches": batch_records,
        "artifacts": {
            project_relative_path(path, project): sha256_file(path) for path in tracked
        },
        "entity_gate_passed": gate_pass,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }
    _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"summary": summary, "manifest": manifest}
