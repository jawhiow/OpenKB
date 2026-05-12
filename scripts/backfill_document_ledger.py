from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openkb.document_ledger import backfill_document_ledger


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill staged OpenKB document ledger records from hashes.json and wiki state."
    )
    parser.add_argument("kb_dir", help="OpenKB knowledge base directory.")
    args = parser.parse_args()

    result = backfill_document_ledger(Path(args.kb_dir).resolve())
    print(
        "document ledger: "
        f"added={result['added']} "
        f"updated={result['updated']} "
        f"unchanged={result['unchanged']} "
        f"total={result['total']}"
    )


if __name__ == "__main__":
    main()
