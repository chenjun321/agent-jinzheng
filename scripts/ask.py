import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("VECTOR_BACKEND", "local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import get_agent, get_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--doc-id", default="")
    args = parser.parse_args()
    doc_id = args.doc_id
    if not doc_id:
        docs = get_store().get_documents()
        if not docs:
            raise SystemExit("No document found. Run scripts/ingest.py first.")
        doc_id = docs[0]["id"]
    result = get_agent().ask(doc_id, args.question)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
