from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import load_yaml


@dataclass(frozen=True)
class TreatmentCohort:
    cohort_id: str
    chain: str
    chain_id: int
    event_id: str
    activation_utc: datetime
    activation_block: int


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"UTC timestamp lacks a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def load_real_v2_config(path: str | Path = "configs/real_v2.yaml") -> dict[str, Any]:
    config = load_yaml(path)
    if int(config.get("schema_version", 0)) != 1:
        raise ValueError("real_v2 requires schema_version 1")
    analysis = config.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("real_v2 analysis configuration is missing")
    window = analysis.get("window")
    if not isinstance(window, dict):
        raise ValueError("real_v2 analysis.window is missing")
    if int(window.get("pre_weeks", 0)) < 1 or int(window.get("post_weeks", 0)) < 1:
        raise ValueError("real_v2 requires positive pre- and post-treatment windows")
    if window.get("include_event_week") is not True:
        raise ValueError("real_v2 must explicitly include event week zero")
    return config


def treatment_cohorts(config: dict[str, Any]) -> list[TreatmentCohort]:
    rows = config.get("cohorts")
    if not isinstance(rows, list) or not rows:
        raise ValueError("real_v2 cohorts are missing")
    cohorts = [
        TreatmentCohort(
            cohort_id=str(row["cohort_id"]),
            chain=str(row["chain"]),
            chain_id=int(row["chain_id"]),
            event_id=str(row["event_id"]),
            activation_utc=parse_utc(str(row["activation_utc"])),
            activation_block=int(row["activation_block"]),
        )
        for row in rows
    ]
    if len({row.cohort_id for row in cohorts}) != len(cohorts):
        raise ValueError("Duplicate real_v2 cohort_id")
    if len({row.chain_id for row in cohorts}) != len(cohorts):
        raise ValueError("Duplicate real_v2 chain_id")
    if any(row.activation_block <= 0 for row in cohorts):
        raise ValueError("Every cohort requires a positive activation block")
    return cohorts


def validate_against_event_ledger(
    config: dict[str, Any],
    ledger_path: str | Path,
) -> None:
    with Path(ledger_path).open(newline="", encoding="utf-8") as handle:
        ledger = list(csv.DictReader(handle))
    primary = {
        row["event_id"]: row
        for row in ledger
        if row["primary_treatment"].strip().lower() == "yes"
    }
    for cohort in treatment_cohorts(config):
        if cohort.event_id not in primary:
            raise ValueError(f"Missing primary ledger event: {cohort.event_id}")
        event = primary[cohort.event_id]
        if event["evidence_tier"] != "A+":
            raise ValueError(f"Primary treatment is not A+: {cohort.event_id}")
        if int(event["block_number"]) != cohort.activation_block:
            raise ValueError(f"Activation block mismatch: {cohort.event_id}")
        if parse_utc(event["event_time_utc"]) != cohort.activation_utc:
            raise ValueError(f"Activation time mismatch: {cohort.event_id}")


def build_event_week_calendar(config: dict[str, Any]) -> list[dict[str, str | int]]:
    window = config["analysis"]["window"]
    pre_weeks = int(window["pre_weeks"])
    post_weeks = int(window["post_weeks"])
    records: list[dict[str, str | int]] = []
    for cohort in treatment_cohorts(config):
        for event_week in range(-pre_weeks, post_weeks + 1):
            start = cohort.activation_utc + timedelta(weeks=event_week)
            end = start + timedelta(weeks=1)
            records.append(
                {
                    "cohort_id": cohort.cohort_id,
                    "chain": cohort.chain,
                    "chain_id": cohort.chain_id,
                    "event_id": cohort.event_id,
                    "activation_block": cohort.activation_block,
                    "activation_utc": cohort.activation_utc.isoformat().replace("+00:00", "Z"),
                    "event_week": event_week,
                    "window_start_utc": start.isoformat().replace("+00:00", "Z"),
                    "window_end_utc_exclusive": end.isoformat().replace("+00:00", "Z"),
                }
            )
    return records


def write_event_week_calendar(
    records: list[dict[str, str | int]],
    output_path: str | Path,
) -> Path:
    if not records:
        raise ValueError("Cannot write an empty event-week calendar")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return destination
