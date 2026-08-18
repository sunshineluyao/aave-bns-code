from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

EXPECTED_EVENT_TOPICS = {
    "borrow": "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0",
    "liquidation": "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286",
    "repay": "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051",
    "supply": "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61",
    "withdraw": "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7",
}
ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_multichain_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or int(config.get("schema_version", 0)) != 1:
        raise ValueError("real_v6 multichain configuration requires schema_version 1")

    design = config["design"]
    minimum_week = int(design["minimum_event_week"])
    maximum_week = int(design["maximum_event_week"])
    if (minimum_week, maximum_week) != (-16, 16):
        raise ValueError("real_v6 keeps the locked symmetric event window [-16,+16]")
    if int(design["minimum_clean_pre_weeks"]) < 2:
        raise ValueError("minimum_clean_pre_weeks must permit a pre-trend diagnostic")
    if bool(design["causal_estimates_allowed"]):
        raise ValueError(
            "The Phase-1 support/preflight configuration cannot allow causal estimates"
        )

    cohorts = config["cohorts"]
    if len(cohorts) != 6:
        raise ValueError("real_v6 support audit requires the six locked GHO cohorts")
    if len({row["cohort_id"] for row in cohorts}) != len(cohorts):
        raise ValueError("cohort_id values must be unique")
    if len({int(row["chain_id"]) for row in cohorts}) != len(cohorts):
        raise ValueError("chain_id values must be unique")
    for row in cohorts:
        activation = parse_utc(str(row["activation_utc"]))
        anticipation = parse_utc(str(row["anticipation_utc"]))
        market = parse_utc(str(row["market_available_by_utc"]))
        if anticipation >= activation:
            raise ValueError(f"{row['cohort_id']}: anticipation must precede activation")
        if bool(row["market_bundled_with_treatment"]) != (market == activation):
            raise ValueError(
                f"{row['cohort_id']}: bundled flag must match the declared market clock"
            )

    configured_topics = {
        str(row["action"]): str(row["topic0"]).lower() for row in config["events"]
    }
    if configured_topics != EXPECTED_EVENT_TOPICS:
        raise ValueError("Configured Aave Pool topics do not match the locked V3 interface")

    acquisition = config["acquisition"]
    minimum_bulk_width = int(acquisition["minimum_viable_log_probe_width"])
    minimum_crosscheck_width = int(acquisition["minimum_crosscheck_log_probe_width"])
    if not 1 <= minimum_crosscheck_width <= minimum_bulk_width:
        raise ValueError(
            "log probe widths must satisfy 1 <= crosscheck minimum <= bulk minimum"
        )
    chains = acquisition["chains"]
    if list(acquisition["order"]) != ["base", "avalanche", "gnosis", "mantle"]:
        raise ValueError("Full acquisition must remain sequential: Base, Avalanche, Gnosis, Mantle")
    if set(chains) != set(acquisition["order"]):
        raise ValueError("Acquisition chain definitions and order differ")
    cohort_map = {str(row["cohort_id"]): row for row in cohorts}
    for slug, row in chains.items():
        cohort = cohort_map[str(row["cohort_id"])]
        if cohort["chain_slug"] != slug or cohort["acquisition_status"] == "released":
            raise ValueError(f"{slug}: acquisition mapping does not identify a pending cohort")
        if not ADDRESS_PATTERN.fullmatch(str(row["pool_address"])):
            raise ValueError(f"{slug}: invalid Pool address")
        if not ADDRESS_PATTERN.fullmatch(str(row["gho_address"])):
            raise ValueError(f"{slug}: invalid GHO address")
        if not SHA_PATTERN.fullmatch(str(row["registry_blob_sha1"])):
            raise ValueError(f"{slug}: invalid address-book blob SHA-1")
        if not str(row["primary_rpc_environment_variable"]).endswith("_RPC_URL"):
            raise ValueError(f"{slug}: primary RPC must be supplied as a URL secret")
        _validate_url(str(row["validation_rpc_url"]), label=f"{slug} validation RPC")
        if row.get("log_crosscheck_rpc_url"):
            _validate_url(
                str(row["log_crosscheck_rpc_url"]),
                label=f"{slug} log cross-check RPC",
            )
            if not str(row.get("log_crosscheck_rpc_source_id", "")):
                raise ValueError(f"{slug}: log cross-check RPC requires a source ID")
    return config


def validate_against_locked_sources(config: dict[str, Any], root: str | Path = ".") -> None:
    project = Path(root)
    with (project / config["source_cohort_config"]).open(encoding="utf-8") as stream:
        locked_config = yaml.safe_load(stream)
    locked_cohorts = {row["cohort_id"]: row for row in locked_config["cohorts"]}

    with (project / config["source_event_ledger"]).open(encoding="utf-8", newline="") as stream:
        events = {row["event_id"]: row for row in csv.DictReader(stream)}

    for cohort in config["cohorts"]:
        cohort_id = str(cohort["cohort_id"])
        locked = locked_cohorts[cohort_id]
        for field in ("chain", "chain_id", "activation_block", "activation_utc"):
            if str(cohort[field]) != str(locked[field]):
                raise ValueError(f"{cohort_id}: {field} differs from configs/real_v2.yaml")

        activation_event = events[str(cohort["activation_event_id"])]
        if activation_event["primary_treatment"] != "Yes":
            raise ValueError(f"{cohort_id}: activation event is not a primary treatment")
        if str(cohort["activation_block"]) != activation_event["block_number"]:
            raise ValueError(f"{cohort_id}: activation block differs from the event ledger")
        if str(cohort["activation_utc"]) != activation_event["event_time_utc"]:
            raise ValueError(f"{cohort_id}: activation time differs from the event ledger")

        anticipation_event = events[str(cohort["anticipation_event_id"])]
        declared_day = parse_utc(str(cohort["anticipation_utc"])).date().isoformat()
        if not anticipation_event["event_time_utc"].startswith(declared_day):
            raise ValueError(f"{cohort_id}: anticipation day differs from the event ledger")
        if str(cohort["market_event_id"]) not in events:
            raise ValueError(f"{cohort_id}: market event is absent from the event ledger")


def _week_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    minimum = int(config["design"]["minimum_event_week"])
    maximum = int(config["design"]["maximum_event_week"])
    rows: list[dict[str, Any]] = []
    for cohort in config["cohorts"]:
        activation = parse_utc(str(cohort["activation_utc"]))
        anticipation = parse_utc(str(cohort["anticipation_utc"]))
        for event_week in range(minimum, maximum + 1):
            start = activation + timedelta(weeks=event_week)
            end = start + timedelta(weeks=1)
            clean_pre = event_week < 0 and end <= anticipation
            post = event_week >= 0
            rows.append(
                {
                    "cohort": cohort,
                    "event_week": event_week,
                    "start": start,
                    "end": end,
                    "clean_pre": clean_pre,
                    "post": post,
                    "required": clean_pre or post,
                }
            )
    return rows


def build_support_audit(config: dict[str, Any]) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    week_rows = _week_rows(config)
    cohorts = config["cohorts"]
    donor_rows: list[dict[str, Any]] = []
    week_support_rows: list[dict[str, Any]] = []
    minimum_donors = int(config["design"]["minimum_donors_per_required_week"])

    for week in week_rows:
        target = week["cohort"]
        eligible_donors: list[str] = []
        for donor in cohorts:
            if donor["cohort_id"] == target["cohort_id"]:
                continue
            market_active = parse_utc(str(donor["market_available_by_utc"])) <= week["start"]
            not_yet_anticipated = parse_utc(str(donor["anticipation_utc"])) >= week["end"]
            not_yet_treated = parse_utc(str(donor["activation_utc"])) >= week["end"]
            eligible = market_active and not_yet_anticipated and not_yet_treated
            reasons = []
            if not market_active:
                reasons.append("market-not-yet-active")
            if not not_yet_anticipated:
                reasons.append("anticipation-started")
            if not not_yet_treated:
                reasons.append("already-treated")
            if eligible:
                eligible_donors.append(str(donor["cohort_id"]))
            donor_rows.append(
                {
                    "target_cohort_id": target["cohort_id"],
                    "target_event_week": week["event_week"],
                    "calendar_week_start_utc": iso_utc(week["start"]),
                    "calendar_week_end_utc_exclusive": iso_utc(week["end"]),
                    "donor_cohort_id": donor["cohort_id"],
                    "donor_chain": donor["chain"],
                    "donor_market_active_for_full_week": _bool(market_active),
                    "donor_not_yet_anticipated_for_full_week": _bool(not_yet_anticipated),
                    "donor_not_yet_treated_for_full_week": _bool(not_yet_treated),
                    "eligible_donor": _bool(eligible),
                    "exclusion_reason": ";".join(reasons),
                }
            )
        week_support_rows.append(
            {
                "target_cohort_id": target["cohort_id"],
                "target_chain": target["chain"],
                "event_week": week["event_week"],
                "calendar_week_start_utc": iso_utc(week["start"]),
                "calendar_week_end_utc_exclusive": iso_utc(week["end"]),
                "target_clean_pre_week": _bool(week["clean_pre"]),
                "target_post_week": _bool(week["post"]),
                "required_for_activation_did_gate": _bool(week["required"]),
                "eligible_donor_count": len(eligible_donors),
                "eligible_donor_cohort_ids": ";".join(sorted(eligible_donors)),
                "donor_support_gate": _bool(
                    not week["required"] or len(eligible_donors) >= minimum_donors
                ),
            }
        )

    cohort_rows: list[dict[str, Any]] = []
    eligible_cohorts: list[str] = []
    minimum_clean_pre = int(config["design"]["minimum_clean_pre_weeks"])
    for cohort in cohorts:
        cohort_id = str(cohort["cohort_id"])
        target_weeks = [row for row in week_support_rows if row["target_cohort_id"] == cohort_id]
        clean_pre = [row for row in target_weeks if row["target_clean_pre_week"] == "true"]
        contaminated_pre = [
            row
            for row in target_weeks
            if int(row["event_week"]) < 0 and row["target_clean_pre_week"] == "false"
        ]
        required = [
            row
            for row in target_weeks
            if row["required_for_activation_did_gate"] == "true"
        ]
        supported = [row for row in required if row["donor_support_gate"] == "true"]
        minimum_observed_donors = min(
            (int(row["eligible_donor_count"]) for row in required), default=0
        )
        activation = parse_utc(str(cohort["activation_utc"]))
        window_start = activation + timedelta(
            weeks=int(config["design"]["minimum_event_week"])
        )
        market_preexisting = parse_utc(str(cohort["market_available_by_utc"])) <= window_start
        pretrend_gate = len(clean_pre) >= minimum_clean_pre
        donor_gate = len(supported) == len(required) and minimum_observed_donors >= minimum_donors
        bundled = bool(cohort["market_bundled_with_treatment"])
        activation_gate = market_preexisting and pretrend_gate and donor_gate and not bundled
        reasons = []
        if not market_preexisting:
            reasons.append("market-not-active-at-window-start")
        if not pretrend_gate:
            reasons.append("insufficient-clean-pre-weeks-after-anticipation")
        if not donor_gate:
            reasons.append("incomplete-not-yet-anticipated-donor-support")
        if bundled:
            reasons.append("aave-market-and-gho-treatment-bundled")
        if activation_gate:
            eligible_cohorts.append(cohort_id)
        cohort_rows.append(
            {
                "cohort_id": cohort_id,
                "chain": cohort["chain"],
                "activation_utc": cohort["activation_utc"],
                "anticipation_utc": cohort["anticipation_utc"],
                "market_available_by_utc": cohort["market_available_by_utc"],
                "market_preexisting_at_window_start": _bool(market_preexisting),
                "market_bundled_with_treatment": _bool(bundled),
                "clean_pre_week_count": len(clean_pre),
                "anticipation_contaminated_pre_week_count": len(contaminated_pre),
                "required_week_count": len(required),
                "required_weeks_with_donor_support": len(supported),
                "minimum_eligible_donors_in_required_weeks": minimum_observed_donors,
                "pretrend_support_gate": _bool(pretrend_gate),
                "donor_support_gate": _bool(donor_gate),
                "activation_did_design_gate": _bool(activation_gate),
                "failure_reasons": ";".join(reasons),
                "acquisition_status": cohort["acquisition_status"],
            }
        )

    summary = {
        "schema_version": 1,
        "release_version": config["release_version"],
        "status": "design_support_audit_not_estimation",
        "donor_rule": config["design"]["donor_rule"],
        "minimum_clean_pre_weeks": minimum_clean_pre,
        "minimum_donors_per_required_week": minimum_donors,
        "eligible_activation_did_cohorts": eligible_cohorts,
        "blocked_activation_did_cohorts": [
            row["cohort_id"] for row in cohort_rows if row["activation_did_design_gate"] == "false"
        ],
        "pending_acquisition_order": list(config["acquisition"]["order"]),
        "causal_estimate_produced": False,
        "interpretation": (
            "Passing this audit establishes calendar and donor support only. It does not establish "
            "parallel trends, produce a treatment effect, or convert addresses into economic "
            "actors."
        ),
    }
    cohort_support_by_id = {row["cohort_id"]: row for row in cohort_rows}
    acquisition_rows = []
    for sequence, slug in enumerate(config["acquisition"]["order"], start=1):
        chain = config["acquisition"]["chains"][slug]
        cohort = next(row for row in cohorts if row["cohort_id"] == chain["cohort_id"])
        activation = parse_utc(str(cohort["activation_utc"]))
        support = cohort_support_by_id[str(cohort["cohort_id"])]
        acquisition_rows.append(
            {
                "sequence": sequence,
                "chain_slug": slug,
                "chain": cohort["chain"],
                "chain_id": cohort["chain_id"],
                "cohort_id": cohort["cohort_id"],
                "window_start_utc": iso_utc(
                    activation + timedelta(weeks=int(config["design"]["minimum_event_week"]))
                ),
                "window_end_utc_exclusive": iso_utc(
                    activation
                    + timedelta(weeks=int(config["design"]["maximum_event_week"]) + 1)
                ),
                "activation_block": cohort["activation_block"],
                "activation_utc": cohort["activation_utc"],
                "pool_address": str(chain["pool_address"]).lower(),
                "gho_address": str(chain["gho_address"]).lower(),
                "primary_rpc_secret_name": chain["primary_rpc_environment_variable"],
                "activation_did_design_gate": support["activation_did_design_gate"],
                "default_analysis_role": (
                    "causal-diagnostics-pending-data-and-assumptions"
                    if support["activation_did_design_gate"] == "true"
                    else "descriptive-and-network-comparison"
                ),
            }
        )
    return {
        "cohort_support": cohort_rows,
        "cohort_week_support": week_support_rows,
        "donor_support": donor_rows,
        "acquisition_windows": acquisition_rows,
        "summary": summary,
    }


def write_support_audit(
    config: dict[str, Any], output_directory: str | Path
) -> dict[str, Path]:
    audit = build_support_audit(config)
    output = Path(output_directory)
    cohort_path = _write_csv(
        output / "cohort_support.csv",
        audit["cohort_support"],
        [
            "cohort_id",
            "chain",
            "activation_utc",
            "anticipation_utc",
            "market_available_by_utc",
            "market_preexisting_at_window_start",
            "market_bundled_with_treatment",
            "clean_pre_week_count",
            "anticipation_contaminated_pre_week_count",
            "required_week_count",
            "required_weeks_with_donor_support",
            "minimum_eligible_donors_in_required_weeks",
            "pretrend_support_gate",
            "donor_support_gate",
            "activation_did_design_gate",
            "failure_reasons",
            "acquisition_status",
        ],
    )
    week_path = _write_csv(
        output / "cohort_week_support.csv",
        audit["cohort_week_support"],
        [
            "target_cohort_id",
            "target_chain",
            "event_week",
            "calendar_week_start_utc",
            "calendar_week_end_utc_exclusive",
            "target_clean_pre_week",
            "target_post_week",
            "required_for_activation_did_gate",
            "eligible_donor_count",
            "eligible_donor_cohort_ids",
            "donor_support_gate",
        ],
    )
    donor_path = _write_csv(
        output / "donor_support.csv",
        audit["donor_support"],
        [
            "target_cohort_id",
            "target_event_week",
            "calendar_week_start_utc",
            "calendar_week_end_utc_exclusive",
            "donor_cohort_id",
            "donor_chain",
            "donor_market_active_for_full_week",
            "donor_not_yet_anticipated_for_full_week",
            "donor_not_yet_treated_for_full_week",
            "eligible_donor",
            "exclusion_reason",
        ],
    )
    acquisition_path = _write_csv(
        output / "acquisition_windows.csv",
        audit["acquisition_windows"],
        [
            "sequence",
            "chain_slug",
            "chain",
            "chain_id",
            "cohort_id",
            "window_start_utc",
            "window_end_utc_exclusive",
            "activation_block",
            "activation_utc",
            "pool_address",
            "gho_address",
            "primary_rpc_secret_name",
            "activation_did_design_gate",
            "default_analysis_role",
        ],
    )
    summary_path = _write_json(output / "summary.json", audit["summary"])
    return {
        "cohort_support": cohort_path,
        "cohort_week_support": week_path,
        "donor_support": donor_path,
        "acquisition_windows": acquisition_path,
        "summary": summary_path,
    }


def _validate_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be a complete HTTP(S) URL")
    return value


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _redacted_error(error: Exception, urls: list[str]) -> str:
    message = f"{type(error).__name__}: {error}"
    for url in urls:
        message = message.replace(url, _safe_endpoint(url))
    return message[:500]


def _default_client_factory(url: str, timeout_seconds: float, maximum_attempts: int) -> Any:
    from .evm_rpc import RpcClient

    return RpcClient(
        url,
        timeout_seconds=timeout_seconds,
        maximum_attempts=maximum_attempts,
    )


def _hex_int(value: str | int) -> int:
    return int(value, 16) if isinstance(value, str) else int(value)


def _probe_log_widths(
    client: Any,
    *,
    pool_address: str,
    topic_filters: list[list[Any]],
    widths: list[int],
    activation_block: int,
    mode: str,
    urls_for_redaction: list[str],
) -> tuple[int, int, list[dict[str, str]]]:
    """Return the largest width supported by every filter in one query mode."""
    diagnostics: list[dict[str, str]] = []
    for width in widths:
        log_count = 0
        width_supported = True
        for topic_index, topics in enumerate(topic_filters):
            try:
                logs = client.logs(
                    {
                        "address": pool_address,
                        "topics": topics,
                        "fromBlock": hex(activation_block),
                        "toBlock": hex(activation_block + int(width) - 1),
                    }
                )
                log_count += len(logs)
            except Exception as error:  # provider errors are evidence, not secret-bearing output
                width_supported = False
                diagnostics.append(
                    {
                        "check": (
                            f"eth_getLogs_{mode}_{width}_blocks_filter_{topic_index + 1}"
                        ),
                        "error": _redacted_error(error, urls_for_redaction),
                    }
                )
                break
        if width_supported:
            return int(width), log_count, diagnostics
    return 0, 0, diagnostics


def _probe_client(
    client: Any,
    *,
    cohort: dict[str, Any],
    chain: dict[str, Any],
    event_topics: list[str],
    widths: list[int],
    urls_for_redaction: list[str],
) -> dict[str, Any]:
    expected_chain_id = int(cohort["chain_id"])
    activation_block = int(cohort["activation_block"])
    expected_timestamp = int(parse_utc(str(cohort["activation_utc"])).timestamp())
    result: dict[str, Any] = {
        "chain_id_match": False,
        "activation_block_available": False,
        "activation_timestamp_match": False,
        "pool_code_present": False,
        "gho_code_present": False,
        "maximum_supported_log_probe_width": 0,
        "maximum_supported_or_topic_log_probe_width": 0,
        "maximum_supported_single_topic_log_probe_width": 0,
        "supported_log_query_mode": "none",
        "diagnostics": [],
    }
    try:
        observed_chain_id = int(client.chain_id())
        result["observed_chain_id"] = observed_chain_id
        result["chain_id_match"] = observed_chain_id == expected_chain_id
        if not result["chain_id_match"]:
            return result

        block = client.block(activation_block)
        result["activation_block_available"] = True
        result["activation_block_hash"] = str(block["hash"]).lower()
        result["activation_block_timestamp"] = _hex_int(block["timestamp"])
        result["activation_timestamp_match"] = (
            result["activation_block_timestamp"] == expected_timestamp
        )

        pool_code = client.code(str(chain["pool_address"]), activation_block)
        gho_code = client.code(str(chain["gho_address"]), activation_block)
        result["pool_code_present"] = pool_code != "0x"
        result["gho_code_present"] = gho_code != "0x"
        result["pool_code_sha256"] = hashlib.sha256(pool_code.encode()).hexdigest()
        result["gho_code_sha256"] = hashlib.sha256(gho_code.encode()).hexdigest()

        or_width, or_count, or_diagnostics = _probe_log_widths(
            client,
            pool_address=str(chain["pool_address"]),
            topic_filters=[[event_topics]],
            widths=widths,
            activation_block=activation_block,
            mode="or_topics",
            urls_for_redaction=urls_for_redaction,
        )
        result["maximum_supported_or_topic_log_probe_width"] = or_width
        result["diagnostics"].extend(or_diagnostics)
        if or_width:
            result["maximum_supported_log_probe_width"] = or_width
            result["log_probe_count"] = or_count
            result["supported_log_query_mode"] = "or-topics"
        else:
            single_width, single_count, single_diagnostics = _probe_log_widths(
                client,
                pool_address=str(chain["pool_address"]),
                topic_filters=[[topic] for topic in event_topics],
                widths=widths,
                activation_block=activation_block,
                mode="single_topic",
                urls_for_redaction=urls_for_redaction,
            )
            result["maximum_supported_single_topic_log_probe_width"] = single_width
            result["diagnostics"].extend(single_diagnostics)
            result["maximum_supported_log_probe_width"] = single_width
            if single_width:
                result["log_probe_count"] = single_count
                result["supported_log_query_mode"] = "single-topic-per-request"
    except Exception as error:
        result["diagnostics"].append(
            {"check": "rpc_core_preflight", "error": _redacted_error(error, urls_for_redaction)}
        )
    return result


def run_chain_preflight(
    config: dict[str, Any],
    chain_slug: str,
    output_directory: str | Path,
    *,
    rpc_url: str | None = None,
    public_rpc_url: str | None = None,
    client_factory: Callable[[str, float, int], Any] = _default_client_factory,
) -> dict[str, Path]:
    acquisition = config["acquisition"]
    if chain_slug not in acquisition["chains"]:
        raise ValueError(f"Unknown acquisition chain: {chain_slug}")
    chain = acquisition["chains"][chain_slug]
    cohort = next(
        row for row in config["cohorts"] if row["cohort_id"] == chain["cohort_id"]
    )
    environment_variable = str(chain["primary_rpc_environment_variable"])
    configured_primary = (rpc_url or os.getenv(environment_variable, "")).strip()
    public_url = _validate_url(
        public_rpc_url or str(chain["validation_rpc_url"]), label="public validation RPC"
    )
    log_crosscheck_url = (
        _validate_url(
            str(chain["log_crosscheck_rpc_url"]), label="log cross-check RPC"
        )
        if chain.get("log_crosscheck_rpc_url")
        else ""
    )
    primary_url = (
        _validate_url(configured_primary, label=environment_variable)
        if configured_primary
        else public_url
    )
    endpoints_independent = configured_primary != "" and (
        _safe_endpoint(primary_url) != _safe_endpoint(public_url)
    )
    timeout_seconds = float(acquisition["timeout_seconds"])
    maximum_attempts = int(acquisition["maximum_attempts"])
    widths = [int(value) for value in acquisition["preflight_log_probe_widths"]]
    event_topics = [str(row["topic0"]).lower() for row in config["events"]]
    urls = [value for value in (primary_url, public_url, log_crosscheck_url) if value]

    primary = client_factory(primary_url, timeout_seconds, maximum_attempts)
    primary_result = _probe_client(
        primary,
        cohort=cohort,
        chain=chain,
        event_topics=event_topics,
        widths=widths,
        urls_for_redaction=urls,
    )
    if endpoints_independent:
        public = client_factory(public_url, timeout_seconds, maximum_attempts)
        public_result = _probe_client(
            public,
            cohort=cohort,
            chain=chain,
            event_topics=event_topics,
            widths=widths,
            urls_for_redaction=urls,
        )
    else:
        public_result = dict(primary_result)

    log_crosscheck_result: dict[str, Any] | None = None
    log_crosscheck_endpoints_independent = False
    if log_crosscheck_url:
        safe_crosscheck = _safe_endpoint(log_crosscheck_url)
        log_crosscheck_endpoints_independent = safe_crosscheck not in {
            _safe_endpoint(primary_url),
            _safe_endpoint(public_url),
        }
        log_crosscheck = client_factory(
            log_crosscheck_url, timeout_seconds, maximum_attempts
        )
        log_crosscheck_result = _probe_client(
            log_crosscheck,
            cohort=cohort,
            chain=chain,
            event_topics=event_topics,
            widths=widths,
            urls_for_redaction=urls,
        )

    minimum_width = int(acquisition["minimum_viable_log_probe_width"])
    minimum_crosscheck_width = int(acquisition["minimum_crosscheck_log_probe_width"])

    def core_checks_passed(result: dict[str, Any]) -> bool:
        return all(
            bool(result[key])
            for key in (
                "chain_id_match",
                "activation_block_available",
                "activation_timestamp_match",
                "pool_code_present",
                "gho_code_present",
            )
        )

    primary_core_passed = core_checks_passed(primary_result)
    public_core_passed = core_checks_passed(public_result)
    log_crosscheck_core_passed = (
        core_checks_passed(log_crosscheck_result)
        if log_crosscheck_result is not None
        else False
    )
    bulk_role = ""
    if int(primary_result["maximum_supported_or_topic_log_probe_width"]) >= minimum_width:
        bulk_role = "primary"
    elif int(public_result["maximum_supported_or_topic_log_probe_width"]) >= minimum_width:
        bulk_role = "public"

    crosscheck_role = ""
    crosscheck_mode = ""
    if bulk_role:
        candidate_role = "public" if bulk_role == "primary" else "primary"
        candidate = public_result if candidate_role == "public" else primary_result
        if (
            int(candidate["maximum_supported_or_topic_log_probe_width"])
            >= minimum_crosscheck_width
        ):
            crosscheck_role = candidate_role
            crosscheck_mode = "or-topics"
        elif (
            int(candidate["maximum_supported_single_topic_log_probe_width"])
            >= minimum_crosscheck_width
        ):
            crosscheck_role = candidate_role
            crosscheck_mode = "single-topic-per-request"
        elif log_crosscheck_result is not None and log_crosscheck_endpoints_independent:
            if (
                int(
                    log_crosscheck_result[
                        "maximum_supported_or_topic_log_probe_width"
                    ]
                )
                >= minimum_crosscheck_width
            ):
                crosscheck_role = "log_crosscheck"
                crosscheck_mode = "or-topics"
            elif (
                int(
                    log_crosscheck_result[
                        "maximum_supported_single_topic_log_probe_width"
                    ]
                )
                >= minimum_crosscheck_width
            ):
                crosscheck_role = "log_crosscheck"
                crosscheck_mode = "single-topic-per-request"

    match_fields = (
        "observed_chain_id",
        "activation_block_hash",
        "activation_block_timestamp",
        "pool_code_sha256",
        "gho_code_sha256",
    )
    independent_match = endpoints_independent and all(
        primary_result.get(key) == public_result.get(key) for key in match_fields
    )
    log_crosscheck_match = (
        log_crosscheck_result is not None
        and log_crosscheck_endpoints_independent
        and all(
            primary_result.get(key) == log_crosscheck_result.get(key)
            for key in match_fields
        )
    )
    selected_crosscheck_core_passed = (
        log_crosscheck_core_passed
        if crosscheck_role == "log_crosscheck"
        else public_core_passed
        if crosscheck_role == "public"
        else primary_core_passed
        if crosscheck_role == "primary"
        else not endpoints_independent
    )
    selected_crosscheck_match = (
        log_crosscheck_match if crosscheck_role == "log_crosscheck" else independent_match
    )

    hard_validation_passed = (
        primary_core_passed
        and public_core_passed
        and bool(bulk_role)
        and (bool(crosscheck_role) or not endpoints_independent)
        and selected_crosscheck_core_passed
    )
    acquisition_ready = hard_validation_passed and selected_crosscheck_match
    summary = {
        "schema_version": 1,
        "release_version": config["release_version"],
        "status": "acquisition-ready" if acquisition_ready else "preflight-gate-pending",
        "chain": cohort["chain"],
        "chain_slug": chain_slug,
        "chain_id": int(cohort["chain_id"]),
        "cohort_id": cohort["cohort_id"],
        "activation_block": int(cohort["activation_block"]),
        "activation_utc": cohort["activation_utc"],
        "pool_address": str(chain["pool_address"]).lower(),
        "gho_address": str(chain["gho_address"]).lower(),
        "registry_commit": config["address_book"]["commit"],
        "registry_path": chain["registry_path"],
        "registry_blob_sha1": chain["registry_blob_sha1"],
        "primary_secret_name": environment_variable,
        "primary_secret_configured": configured_primary != "",
        "primary_endpoint": _safe_endpoint(primary_url),
        "public_endpoint": _safe_endpoint(public_url),
        "log_crosscheck_endpoint": (
            _safe_endpoint(log_crosscheck_url) if log_crosscheck_url else ""
        ),
        "log_crosscheck_source_id": str(
            chain.get("log_crosscheck_rpc_source_id", "")
        ),
        "endpoints_independent": endpoints_independent,
        "log_crosscheck_endpoints_independent": log_crosscheck_endpoints_independent,
        "hard_public_or_primary_validation_passed": hard_validation_passed,
        "primary_core_validation_passed": primary_core_passed,
        "public_core_validation_passed": public_core_passed,
        "log_crosscheck_core_validation_passed": log_crosscheck_core_passed,
        "bulk_log_provider_role": bulk_role,
        "bulk_log_query_mode": "or-topics" if bulk_role else "",
        "crosscheck_log_provider_role": crosscheck_role,
        "crosscheck_log_query_mode": crosscheck_mode,
        "independent_provider_match": independent_match,
        "log_crosscheck_provider_match": log_crosscheck_match,
        "acquisition_ready": acquisition_ready,
        "minimum_viable_log_probe_width": minimum_width,
        "minimum_crosscheck_log_probe_width": minimum_crosscheck_width,
        "primary_checks": primary_result,
        "public_checks": public_result,
        "log_crosscheck_checks": log_crosscheck_result,
        "causal_estimate_produced": False,
    }
    output = Path(output_directory) / chain_slug
    summary_path = _write_json(output / "summary.json", summary)
    check_rows = []
    provider_results = [("primary", primary_result), ("public", public_result)]
    if log_crosscheck_result is not None:
        provider_results.append(("log_crosscheck", log_crosscheck_result))
    for role, result in provider_results:
        check_rows.append(
            {
                "chain_slug": chain_slug,
                "provider_role": role,
                "chain_id_match": _bool(bool(result["chain_id_match"])),
                "activation_timestamp_match": _bool(bool(result["activation_timestamp_match"])),
                "pool_code_present": _bool(bool(result["pool_code_present"])),
                "gho_code_present": _bool(bool(result["gho_code_present"])),
                "maximum_supported_log_probe_width": result[
                    "maximum_supported_log_probe_width"
                ],
                "maximum_supported_or_topic_log_probe_width": result[
                    "maximum_supported_or_topic_log_probe_width"
                ],
                "maximum_supported_single_topic_log_probe_width": result[
                    "maximum_supported_single_topic_log_probe_width"
                ],
                "supported_log_query_mode": result["supported_log_query_mode"],
                "diagnostic_count": len(result["diagnostics"]),
            }
        )
    checks_path = _write_csv(
        output / "rpc_checks.csv",
        check_rows,
        [
            "chain_slug",
            "provider_role",
            "chain_id_match",
            "activation_timestamp_match",
            "pool_code_present",
            "gho_code_present",
            "maximum_supported_log_probe_width",
            "maximum_supported_or_topic_log_probe_width",
            "maximum_supported_single_topic_log_probe_width",
            "supported_log_query_mode",
            "diagnostic_count",
        ],
    )
    return {"summary": summary_path, "checks": checks_path}
