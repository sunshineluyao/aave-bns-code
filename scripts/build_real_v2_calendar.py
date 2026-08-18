from __future__ import annotations

import argparse
from pathlib import Path

from aave_bns.real_v2 import (
    build_event_week_calendar,
    load_real_v2_config,
    validate_against_event_ledger,
    write_event_week_calendar,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the locked real_v2 event-week calendar")
    parser.add_argument("--root", default=".", help="Repository root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    config = load_real_v2_config(root / "configs/real_v2.yaml")
    validate_against_event_ledger(config, root / config["source_event_ledger"])
    destination = root / "data/metadata/real_v2_event_week_calendar.csv"
    write_event_week_calendar(build_event_week_calendar(config), destination)
    print(destination)


if __name__ == "__main__":
    main()
