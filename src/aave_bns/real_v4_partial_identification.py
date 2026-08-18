from __future__ import annotations

import gzip
import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .provenance import sha256_file
from .transform import normalize_address

REQUIRED_EVENT_COLUMNS = {
    "chain_id",
    "action",
    "block_number",
    "tx_hash",
    "log_index",
    "event_week",
    "beneficiary_address",
}

REGISTRY_COLUMNS = (
    "address",
    "contract_observed",
    "high_confidence_entity_label",
    "economic_actor_resolved",
    "entity_scope",
)
REQUIRED_REGISTRY_COLUMNS = set(REGISTRY_COLUMNS)

REQUIRED_CONSTRAINT_COLUMNS = {
    "constraint_id",
    "release_version",
    "chain_id",
    "left_address",
    "right_address",
    "relation",
    "entity_scope",
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

EXPECTED_ACTIONS = ("borrow", "liquidation", "repay", "supply", "withdraw")
INFRASTRUCTURE_SCOPES = {"protocol_infrastructure", "asset_infrastructure"}
BOUND_ASSUMPTIONS = ("event_split", "stable_address", "evidence")


def project_relative_path(path: str | Path, project_root: str | Path) -> str:
    project = Path(project_root).resolve()
    candidate = Path(path).resolve()
    try:
        return candidate.relative_to(project).as_posix()
    except ValueError as error:
        raise ValueError(f"Artifact path is outside the project root: {candidate}") from error


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(value, encoding="utf-8")
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
                with io.TextIOWrapper(
                    zipped, encoding="utf-8", newline=""
                ) as text_handle:
                    frame.to_csv(text_handle, index=False, lineterminator="\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def load_real_v4_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("real_v4 configuration must be a YAML object")
    if int(config.get("schema_version", 0)) != 1:
        raise ValueError("Unsupported real_v4 configuration schema")
    return config


def validate_event_timing(
    event_source_audit_path: str | Path,
    event_week_calendar_path: str | Path,
    *,
    event_id: str,
    activation_block: int,
    activation_utc: str,
) -> None:
    audit = pd.read_csv(event_source_audit_path, dtype=str, keep_default_na=False)
    if "event_id" not in audit or "event_time_utc" not in audit or "block_number" not in audit:
        raise ValueError("Event-source audit lacks timing columns")
    selected = audit[audit["event_id"] == event_id]
    if len(selected) != 1:
        raise ValueError("Event-source audit must contain exactly one treatment event")
    audit_row = selected.iloc[0]
    if str(audit_row["event_time_utc"]) != activation_utc or int(
        audit_row["block_number"]
    ) != activation_block:
        raise ValueError("Configured treatment timing differs from the event-source audit")

    calendar = pd.read_csv(event_week_calendar_path, dtype=str, keep_default_na=False)
    required = {
        "event_id",
        "cohort_id",
        "event_week",
        "activation_block",
        "activation_utc",
    }
    if not required.issubset(calendar.columns):
        raise ValueError("Event-week calendar lacks treatment timing columns")
    week_zero = calendar[
        (calendar["event_id"] == event_id)
        & (pd.to_numeric(calendar["event_week"], errors="raise") == 0)
    ]
    if len(week_zero) != 1:
        raise ValueError(f"Event-week calendar must contain one week-zero row for {event_id}")
    calendar_row = week_zero.iloc[0]
    if int(calendar_row["activation_block"]) != activation_block or str(
        calendar_row["activation_utc"]
    ) != activation_utc:
        raise ValueError("Configured treatment timing differs from the event-week calendar")


def _as_bool(value: object) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def build_beneficiary_event_panel(
    events: pd.DataFrame,
    *,
    chain_id: int,
    minimum_event_week: int,
    maximum_event_week: int,
) -> pd.DataFrame:
    missing = REQUIRED_EVENT_COLUMNS.difference(events.columns)
    if missing:
        raise ValueError(f"Missing real_v4 beneficiary-input columns: {sorted(missing)}")
    frame = events.copy()
    frame["chain_id"] = pd.to_numeric(frame["chain_id"], errors="raise").astype("int64")
    if set(frame["chain_id"]) != {chain_id}:
        raise ValueError("Processed events do not match the configured chain")
    frame["block_number"] = pd.to_numeric(
        frame["block_number"], errors="raise"
    ).astype("int64")
    frame["log_index"] = pd.to_numeric(frame["log_index"], errors="raise").astype(
        "int64"
    )
    frame["event_week"] = pd.to_numeric(frame["event_week"], errors="raise").astype(
        "int64"
    )
    frame["action"] = frame["action"].astype(str).str.lower().str.strip()
    if set(frame["action"]) != set(EXPECTED_ACTIONS):
        raise ValueError("Processed events do not contain the five locked Aave actions")
    if frame[["tx_hash", "log_index"]].duplicated().any():
        raise ValueError("Processed events contain duplicate transaction-log keys")
    if frame["beneficiary_address"].isna().any() or frame[
        "beneficiary_address"
    ].astype(str).str.strip().eq("").any():
        raise ValueError("Every locked Aave action must have a beneficiary address")
    frame["beneficiary_address"] = frame["beneficiary_address"].map(normalize_address)
    if int(frame["event_week"].min()) != minimum_event_week or int(
        frame["event_week"].max()
    ) != maximum_event_week:
        raise ValueError("Processed event-week coverage differs from the locked window")
    frame = frame.sort_values(
        ["block_number", "tx_hash", "log_index"], kind="mergesort"
    ).reset_index(drop=True)
    frame.insert(0, "event_ordinal", range(1, len(frame) + 1))
    return frame[
        [
            "event_ordinal",
            "block_number",
            "event_week",
            "action",
            "beneficiary_address",
        ]
    ]


def load_address_registry(path: str | Path) -> pd.DataFrame:
    registry = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = REQUIRED_REGISTRY_COLUMNS.difference(registry.columns)
    if missing:
        raise ValueError(f"real_v3 registry is missing columns: {sorted(missing)}")
    registry = registry[list(REGISTRY_COLUMNS)].copy()
    registry["address"] = registry["address"].map(normalize_address)
    if registry["address"].duplicated().any():
        raise ValueError("real_v3 registry contains duplicate addresses")
    for column in (
        "contract_observed",
        "high_confidence_entity_label",
        "economic_actor_resolved",
    ):
        registry[column] = registry[column].map(_as_bool)
    registry["curated_infrastructure"] = (
        registry["high_confidence_entity_label"]
        & registry["entity_scope"].isin(INFRASTRUCTURE_SCOPES)
    )
    return registry.sort_values("address").reset_index(drop=True)


def load_actor_constraints(
    path: str | Path,
    *,
    release_version: str,
    chain_id: int,
    minimum_confidence: float,
    allowed_relation: str,
    required_entity_scope: str,
    required_review_status: str,
    registry_addresses: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    constraints = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = REQUIRED_CONSTRAINT_COLUMNS.difference(constraints.columns)
    if missing:
        raise ValueError(f"Actor-constraint registry is missing columns: {sorted(missing)}")
    constraints = constraints[
        (constraints["release_version"] == release_version)
        & (pd.to_numeric(constraints["chain_id"], errors="raise") == chain_id)
    ].copy()
    if constraints.empty:
        constraints["confidence"] = pd.Series(dtype="float64")
        constraints["valid_from_block"] = pd.Series(dtype="int64")
        constraints["valid_to_block"] = pd.Series(dtype="int64")
        constraints["accepted"] = pd.Series(dtype="bool")
        return constraints, constraints.copy()
    if constraints["constraint_id"].duplicated().any():
        raise ValueError("Actor constraints contain duplicate IDs")
    for column in ("left_address", "right_address"):
        constraints[column] = constraints[column].map(normalize_address)
    if (constraints["left_address"] == constraints["right_address"]).any():
        raise ValueError("A must-link constraint cannot link an address to itself")
    unknown = (
        set(constraints["left_address"])
        | set(constraints["right_address"])
    ).difference(registry_addresses)
    if unknown:
        raise ValueError(f"Actor constraints reference unknown addresses: {sorted(unknown)}")
    constraints["confidence"] = pd.to_numeric(
        constraints["confidence"], errors="raise"
    )
    constraints["valid_from_block"] = pd.to_numeric(
        constraints["valid_from_block"], errors="raise"
    ).astype("int64")
    constraints["valid_to_block"] = pd.to_numeric(
        constraints["valid_to_block"], errors="raise"
    ).astype("int64")
    if ((constraints["confidence"] < 0) | (constraints["confidence"] > 1)).any():
        raise ValueError("Constraint confidence must lie in [0, 1]")
    if (constraints["valid_to_block"] < constraints["valid_from_block"]).any():
        raise ValueError("Actor constraint has a reversed validity interval")
    if (~constraints["source_url"].str.startswith("https://")).any():
        raise ValueError("Every actor constraint must cite an HTTPS primary source")
    if (~constraints["source_revision"].str.fullmatch(r"[0-9a-f]{40}")).any():
        raise ValueError("Every actor constraint must pin a 40-character source revision")
    constraints["accepted"] = (
        constraints["relation"].eq(allowed_relation)
        & constraints["entity_scope"].eq(required_entity_scope)
        & constraints["review_status"].eq(required_review_status)
        & constraints["confidence"].ge(minimum_confidence)
    )
    rejected = constraints[~constraints["accepted"]]
    if not rejected.empty:
        raise ValueError(
            "Every row in the locked actor-constraint release must pass the evidence gate"
        )
    return constraints.sort_values("constraint_id").reset_index(drop=True), constraints[
        constraints["accepted"]
    ].sort_values("constraint_id").reset_index(drop=True)


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        lower, upper = sorted((left_root, right_root))
        self.parent[upper] = lower


def _distribution_metrics(weights: pd.Series) -> dict[str, float]:
    numeric = pd.to_numeric(weights, errors="raise").astype(float)
    total = float(numeric.sum())
    if total <= 0:
        return {"hhi": 0.0, "effective": 0.0, "entropy": 0.0, "top1": 0.0, "top5": 0.0}
    shares = (numeric / total).sort_values(ascending=False)
    hhi = float((shares**2).sum())
    entropy = float(-(shares * shares.map(math.log)).sum())
    return {
        "hhi": hhi,
        "effective": float(1.0 / hhi),
        "entropy": entropy,
        "top1": float(shares.iloc[0]),
        "top5": float(shares.head(5).sum()),
    }


def _component_weights(
    address_weights: pd.Series,
    constraints: pd.DataFrame,
) -> pd.Series:
    addresses = list(address_weights.index)
    union_find = _UnionFind(addresses)
    address_set = set(addresses)
    for row in constraints.itertuples(index=False):
        if row.left_address in address_set and row.right_address in address_set:
            union_find.union(row.left_address, row.right_address)
    components: dict[str, float] = {}
    for address, weight in address_weights.items():
        root = union_find.find(str(address))
        components[root] = components.get(root, 0.0) + float(weight)
    return pd.Series(components, dtype="float64")


def _bound_row(group: pd.DataFrame, constraints: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        raise ValueError("Cannot compute bounds for an empty group")
    event_count = len(group)
    address_weights = group["beneficiary_address"].value_counts(sort=False)
    address_metrics = _distribution_metrics(address_weights)
    minimum_block = int(group["block_number"].min())
    maximum_block = int(group["block_number"].max())
    active_constraints = constraints[
        (constraints["valid_from_block"] <= minimum_block)
        & (constraints["valid_to_block"] >= maximum_block)
    ]
    component_metrics = _distribution_metrics(
        _component_weights(address_weights, active_constraints)
    )
    row: dict[str, Any] = {
        "cell_observed": True,
        "event_count": event_count,
        "beneficiary_address_count": int(len(address_weights)),
        "minimum_block": minimum_block,
        "maximum_block": maximum_block,
        "contract_beneficiary_share": float(group["contract_observed"].mean()),
        "curated_infrastructure_beneficiary_share": float(
            group["curated_infrastructure"].mean()
        ),
        "economic_actor_beneficiary_coverage": float(
            group["economic_actor_resolved"].mean()
        ),
        "accepted_must_link_constraint_count": int(len(active_constraints)),
        "address_proxy_hhi": address_metrics["hhi"],
        "address_proxy_effective_number": address_metrics["effective"],
        "address_proxy_entropy": address_metrics["entropy"],
        "address_proxy_top1_share": address_metrics["top1"],
        "address_proxy_top5_share": address_metrics["top5"],
    }
    event_metrics = {
        "hhi_lower": float(1.0 / event_count),
        "hhi_upper": 1.0,
        "effective_lower": 1.0,
        "effective_upper": float(event_count),
        "entropy_lower": 0.0,
        "entropy_upper": float(math.log(event_count)),
        "top1_lower": float(1.0 / event_count),
        "top1_upper": 1.0,
        "top5_lower": float(min(5, event_count) / event_count),
        "top5_upper": 1.0,
    }
    stable_metrics = {
        "hhi_lower": address_metrics["hhi"],
        "hhi_upper": 1.0,
        "effective_lower": 1.0,
        "effective_upper": address_metrics["effective"],
        "entropy_lower": 0.0,
        "entropy_upper": address_metrics["entropy"],
        "top1_lower": address_metrics["top1"],
        "top1_upper": 1.0,
        "top5_lower": address_metrics["top5"],
        "top5_upper": 1.0,
    }
    evidence_metrics = {
        "hhi_lower": component_metrics["hhi"],
        "hhi_upper": 1.0,
        "effective_lower": 1.0,
        "effective_upper": component_metrics["effective"],
        "entropy_lower": 0.0,
        "entropy_upper": component_metrics["entropy"],
        "top1_lower": component_metrics["top1"],
        "top1_upper": 1.0,
        "top5_lower": component_metrics["top5"],
        "top5_upper": 1.0,
    }
    for prefix, values in (
        ("event_split", event_metrics),
        ("stable_address", stable_metrics),
        ("evidence", evidence_metrics),
    ):
        for name, value in values.items():
            row[f"{prefix}_{name}"] = value
    return row


def _empty_bound_row() -> dict[str, Any]:
    row: dict[str, Any] = {
        "cell_observed": False,
        "event_count": 0,
        "beneficiary_address_count": 0,
        "minimum_block": 0,
        "maximum_block": 0,
        "contract_beneficiary_share": 0.0,
        "curated_infrastructure_beneficiary_share": 0.0,
        "economic_actor_beneficiary_coverage": 0.0,
        "accepted_must_link_constraint_count": 0,
        "address_proxy_hhi": 0.0,
        "address_proxy_effective_number": 0.0,
        "address_proxy_entropy": 0.0,
        "address_proxy_top1_share": 0.0,
        "address_proxy_top5_share": 0.0,
    }
    for prefix in BOUND_ASSUMPTIONS:
        for metric in ("hhi", "effective", "entropy", "top1", "top5"):
            row[f"{prefix}_{metric}_lower"] = 0.0
            row[f"{prefix}_{metric}_upper"] = 0.0
    return row


def attach_registry(
    beneficiary_panel: pd.DataFrame, registry: pd.DataFrame
) -> pd.DataFrame:
    merged = beneficiary_panel.merge(
        registry[
            [
                "address",
                "contract_observed",
                "curated_infrastructure",
                "economic_actor_resolved",
            ]
        ],
        left_on="beneficiary_address",
        right_on="address",
        how="left",
        validate="many_to_one",
    ).drop(columns=["address"])
    if merged["contract_observed"].isna().any():
        missing = sorted(
            set(
                merged.loc[
                    merged["contract_observed"].isna(), "beneficiary_address"
                ]
            )
        )
        raise ValueError(f"Beneficiary addresses are missing from real_v3: {missing}")
    return merged


def build_bound_panels(
    beneficiary_panel: pd.DataFrame,
    registry: pd.DataFrame,
    constraints: pd.DataFrame,
    periods: list[dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    merged = attach_registry(beneficiary_panel, registry)
    weekly_rows = []
    for event_week, group in merged.groupby("event_week", sort=True, observed=True):
        weekly_rows.append({"event_week": int(event_week), **_bound_row(group, constraints)})
    weekly = pd.DataFrame(weekly_rows).sort_values("event_week").reset_index(drop=True)

    action_rows = []
    actions = sorted(merged["action"].unique())
    weeks = range(int(merged["event_week"].min()), int(merged["event_week"].max()) + 1)
    for event_week in weeks:
        for action in actions:
            group = merged[
                (merged["event_week"] == event_week) & (merged["action"] == action)
            ]
            bounds = _empty_bound_row() if group.empty else _bound_row(group, constraints)
            action_rows.append(
                {
                    "event_week": int(event_week),
                    "action": action,
                    **bounds,
                }
            )
    weekly_action = pd.DataFrame(action_rows).sort_values(
        ["event_week", "action"]
    ).reset_index(drop=True)

    period_rows = []
    for period in periods:
        minimum = int(period["minimum_event_week"])
        maximum = int(period["maximum_event_week"])
        group = merged[
            (merged["event_week"] >= minimum) & (merged["event_week"] <= maximum)
        ]
        period_rows.append(
            {
                "period": str(period["name"]),
                "minimum_event_week": minimum,
                "maximum_event_week": maximum,
                **_bound_row(group, constraints),
            }
        )
    periods_frame = pd.DataFrame(period_rows)
    return weekly_action, weekly, periods_frame


def build_change_bounds(periods: pd.DataFrame) -> pd.DataFrame:
    indexed = periods.set_index("period")
    if not {"pre", "post"}.issubset(indexed.index):
        raise ValueError("Period configuration must contain pre and post rows")
    pre = indexed.loc["pre"]
    post = indexed.loc["post"]
    rows: list[dict[str, Any]] = []
    proxy_change = float(post["address_proxy_hhi"] - pre["address_proxy_hhi"])
    rows.append(
        {
            "comparison": "post_minus_pre",
            "metric": "hhi",
            "assumption": "address_proxy_point",
            "pre_lower": float(pre["address_proxy_hhi"]),
            "pre_upper": float(pre["address_proxy_hhi"]),
            "post_lower": float(post["address_proxy_hhi"]),
            "post_upper": float(post["address_proxy_hhi"]),
            "change_lower": proxy_change,
            "change_upper": proxy_change,
            "sign_identified": True,
            "direction": (
                "decrease"
                if proxy_change < 0
                else "increase"
                if proxy_change > 0
                else "zero"
            ),
            "economic_actor_conclusion_permitted": False,
        }
    )
    for assumption in BOUND_ASSUMPTIONS:
        prefix = "stable_address" if assumption == "stable_address" else assumption
        pre_lower = float(pre[f"{prefix}_hhi_lower"])
        pre_upper = float(pre[f"{prefix}_hhi_upper"])
        post_lower = float(post[f"{prefix}_hhi_lower"])
        post_upper = float(post[f"{prefix}_hhi_upper"])
        change_lower = post_lower - pre_upper
        change_upper = post_upper - pre_lower
        sign_identified = change_lower > 0 or change_upper < 0
        if change_lower > 0:
            direction = "increase"
        elif change_upper < 0:
            direction = "decrease"
        else:
            direction = "not_identified"
        rows.append(
            {
                "comparison": "post_minus_pre",
                "metric": "hhi",
                "assumption": assumption,
                "pre_lower": pre_lower,
                "pre_upper": pre_upper,
                "post_lower": post_lower,
                "post_upper": post_upper,
                "change_lower": change_lower,
                "change_upper": change_upper,
                "sign_identified": sign_identified,
                "direction": direction,
                "economic_actor_conclusion_permitted": sign_identified,
            }
        )
    return pd.DataFrame(rows)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_real_v4_partial_identification(
    config_path: str | Path,
    *,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    configuration_path = Path(config_path)
    if not configuration_path.is_absolute():
        configuration_path = project / configuration_path
    config = load_real_v4_config(configuration_path)
    release_version = str(config["release_version"])
    chain_id = int(config["chain"]["chain_id"])
    measurement = config["measurement"]
    gate = config["constraint_gate"]
    inputs = config["input"]

    processed_path = project / inputs["processed_events"]
    registry_path = project / inputs["address_registry"]
    constraints_path = project / inputs["actor_constraints"]
    source_catalog_path = project / inputs["source_catalog"]
    event_source_audit_path = project / inputs["event_source_audit"]
    event_week_calendar_path = project / inputs["event_week_calendar"]
    output = project / config["output_directory"]
    output.mkdir(parents=True, exist_ok=True)

    validate_event_timing(
        event_source_audit_path,
        event_week_calendar_path,
        event_id=str(config["event"]["event_id"]),
        activation_block=int(config["event"]["activation_block"]),
        activation_utc=str(config["event"]["activation_utc"]),
    )

    events = pd.read_csv(processed_path, dtype=str)
    beneficiary = build_beneficiary_event_panel(
        events,
        chain_id=chain_id,
        minimum_event_week=int(measurement["minimum_event_week"]),
        maximum_event_week=int(measurement["maximum_event_week"]),
    )
    registry = load_address_registry(registry_path)
    all_constraints, accepted_constraints = load_actor_constraints(
        constraints_path,
        release_version=release_version,
        chain_id=chain_id,
        minimum_confidence=float(gate["minimum_confidence"]),
        allowed_relation=str(gate["allowed_relation"]),
        required_entity_scope=str(gate["required_entity_scope"]),
        required_review_status=str(gate["required_review_status"]),
        registry_addresses=set(registry["address"]),
    )
    weekly_action, weekly, periods = build_bound_panels(
        beneficiary, registry, accepted_constraints, list(measurement["periods"])
    )
    changes = build_change_bounds(periods)

    beneficiary_path = output / "beneficiary_event_panel.csv.gz"
    weekly_action_path = output / "weekly_action_beneficiary_bounds.csv"
    weekly_path = output / "weekly_beneficiary_bounds.csv"
    period_path = output / "period_beneficiary_bounds.csv"
    change_path = output / "period_change_bounds.csv"
    constraint_path = output / "constraint_audit.csv"
    summary_path = output / "summary.json"
    manifest_path = output / "manifest.json"

    write_gzip_csv_atomic(beneficiary, beneficiary_path)
    write_csv_atomic(weekly_action, weekly_action_path)
    write_csv_atomic(weekly, weekly_path)
    write_csv_atomic(periods, period_path)
    write_csv_atomic(changes, change_path)
    write_csv_atomic(all_constraints, constraint_path)

    full = periods.set_index("period").loc["full"]
    pre = periods.set_index("period").loc["pre"]
    post = periods.set_index("period").loc["post"]
    stable_change = changes.set_index("assumption").loc["stable_address"]
    merged = attach_registry(beneficiary, registry)
    summary = {
        "schema_version": 1,
        "release_version": release_version,
        "status": "assumption_indexed_partial_identification",
        "chain_id": chain_id,
        "event_id": config["event"]["event_id"],
        "activation_block": int(config["event"]["activation_block"]),
        "activation_utc": str(config["event"]["activation_utc"]),
        "public_changelog_date": str(config["event"]["public_changelog_date"]),
        "observed_unit": str(measurement["observed_unit"]),
        "event_count": int(len(beneficiary)),
        "beneficiary_address_count": int(beneficiary["beneficiary_address"].nunique()),
        "contract_beneficiary_event_count": int(merged["contract_observed"].sum()),
        "contract_beneficiary_share": float(merged["contract_observed"].mean()),
        "curated_infrastructure_beneficiary_event_count": int(
            merged["curated_infrastructure"].sum()
        ),
        "curated_infrastructure_beneficiary_share": float(
            merged["curated_infrastructure"].mean()
        ),
        "economic_actor_beneficiary_coverage": float(
            merged["economic_actor_resolved"].mean()
        ),
        "actor_constraint_row_count": int(len(all_constraints)),
        "accepted_must_link_constraint_count": int(len(accepted_constraints)),
        "full_address_proxy_hhi": float(full["address_proxy_hhi"]),
        "full_address_proxy_effective_number": float(
            full["address_proxy_effective_number"]
        ),
        "full_event_split_hhi_lower": float(full["event_split_hhi_lower"]),
        "full_stable_address_hhi_lower": float(full["stable_address_hhi_lower"]),
        "full_actor_hhi_upper": 1.0,
        "pre_address_proxy_hhi": float(pre["address_proxy_hhi"]),
        "post_address_proxy_hhi": float(post["address_proxy_hhi"]),
        "address_proxy_hhi_change": float(
            post["address_proxy_hhi"] - pre["address_proxy_hhi"]
        ),
        "stable_address_actor_hhi_change_lower": float(
            stable_change["change_lower"]
        ),
        "stable_address_actor_hhi_change_upper": float(
            stable_change["change_upper"]
        ),
        "address_proxy_direction": "lower_concentration_post",
        "economic_actor_direction_identified": False,
        "identified_set_produced": True,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
        "weekly_panel_rows": int(len(weekly)),
        "weekly_action_panel_rows": int(len(weekly_action)),
        "period_panel_rows": int(len(periods)),
        "change_panel_rows": int(len(changes)),
        "limitations": [
            "A beneficiary address is an observed position-holder field, not a natural person.",
            "The event-split bounds allow one address to represent multiple underlying actors.",
            (
                "The stable-address bounds assume one controller per address within each "
                "reported group."
            ),
            "Only pinned primary-source economic-actor must-links may tighten evidence bounds.",
            "Protocol labels and equal runtime code never establish terminal-user ownership.",
            "Identified sets are not confidence intervals, point estimates, or causal effects.",
        ],
    }
    _atomic_json(summary_path, summary)

    tracked = [
        beneficiary_path,
        weekly_action_path,
        weekly_path,
        period_path,
        change_path,
        constraint_path,
        summary_path,
    ]
    input_records = {
        project_relative_path(processed_path, project): sha256_file(processed_path),
        project_relative_path(registry_path, project): sha256_file(registry_path),
        project_relative_path(constraints_path, project): sha256_file(constraints_path),
        project_relative_path(source_catalog_path, project): sha256_file(source_catalog_path),
        project_relative_path(event_source_audit_path, project): sha256_file(
            event_source_audit_path
        ),
        project_relative_path(event_week_calendar_path, project): sha256_file(
            event_week_calendar_path
        ),
    }
    manifest = {
        "schema_version": 1,
        "pipeline": "real_v4_ethereum_partial_identification",
        "release_version": release_version,
        "generation_policy": "deterministic_no_wall_clock",
        "path_policy": "repository_relative_posix",
        "configuration": {
            "path": project_relative_path(configuration_path, project),
            "sha256": sha256_file(configuration_path),
        },
        "inputs": input_records,
        "code": {
            relative: sha256_file(project / relative)
            for relative in (
                "src/aave_bns/real_v4_partial_identification.py",
                "scripts/run_real_v4_partial_identification.py",
            )
        },
        "artifacts": {
            project_relative_path(path, project): sha256_file(path) for path in tracked
        },
        "identified_set_produced": True,
        "economic_actor_direction_identified": False,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }
    _atomic_json(manifest_path, manifest)
    return {"summary": summary, "manifest": manifest}
