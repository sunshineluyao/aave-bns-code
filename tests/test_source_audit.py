import csv
from pathlib import Path


def read_csv(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_event_ledger_is_complete_and_treatment_strict():
    rows = read_csv("data/metadata/event_source_audit.csv")
    assert len(rows) == 31
    assert len({row["event_id"] for row in rows}) == 31
    assert all(row["source_url"].startswith("https://") for row in rows)
    primary = [row for row in rows if row["primary_treatment"] == "Yes"]
    assert len(primary) == 6
    assert all(row["evidence_tier"] == "A+" for row in primary)
    assert all("activation" in row["source_type"].lower() for row in primary)


def test_source_catalog_separates_evidence_and_delivery():
    rows = read_csv("data/metadata/source_catalog.csv")
    assert len(rows) == 21
    assert len({row["source_id"] for row in rows}) == 21
    assert all(row["evidence_grade"] for row in rows)
    assert all(row["delivery_grade"] for row in rows)
    assert all(row["public_url"].startswith("https://") for row in rows)


def test_ethereum_event_decoder_has_a_pinned_official_interface():
    rows = read_csv("data/metadata/source_catalog.csv")
    interface = next(row for row in rows if row["source_id"] == "aave_v3_core_interface")
    assert "/blob/782f51917056a53a2c228701058a6c3fb233684a/" in interface["public_url"]
    assert interface["delivery_grade"] == "A"
