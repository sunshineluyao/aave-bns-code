import gzip
import json
from pathlib import Path

from aave_bns.real_v2_ethereum import (
    _read_jsonl_gzip,
    _write_deterministic_jsonl_gzip,
    _write_processed_events,
)


def test_deterministic_jsonl_writer_produces_complete_gzip(tmp_path: Path):
    path = tmp_path / "chunks" / "one.jsonl.gz"
    records = [{"blockNumber": "0x1", "topics": ["0xabc"]}]
    _write_deterministic_jsonl_gzip(path, records)
    assert _read_jsonl_gzip(path) == records
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline()) == records[0]
        assert handle.readline() == ""
    first_bytes = path.read_bytes()
    _write_deterministic_jsonl_gzip(path, records)
    assert path.read_bytes() == first_bytes
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_processed_writer_produces_complete_gzip(tmp_path: Path):
    path = tmp_path / "processed" / "events.csv.gz"
    records = [{"action": "supply", "event_week": 0}]
    _write_processed_events(path, records)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert handle.read().splitlines() == ["action,event_week", "supply,0"]
    first_bytes = path.read_bytes()
    _write_processed_events(path, records)
    assert path.read_bytes() == first_bytes
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
