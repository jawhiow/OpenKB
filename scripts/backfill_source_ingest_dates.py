from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openkb.source_relations import backfill_source_ingest_dates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill missing OpenKB source ingestion timestamps into .openkb/hashes.json."
    )
    parser.add_argument("kb_dir", help="OpenKB knowledge base directory.")
    args = parser.parse_args()

    result = backfill_source_ingest_dates(Path(args.kb_dir).resolve())
    print(
        "source ingest dates: "
        f"updated={result['updated']} "
        f"skipped={result['skipped']} "
        f"missing={result['missing']} "
        f"total={result['total']}"
    )


if __name__ == "__main__":
    main()
