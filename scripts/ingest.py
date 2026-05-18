import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import get_parser, get_rag_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    args = parser.parse_args()
    meta = get_parser().ingest(args.pdf)
    index = get_rag_service().index_document(meta["id"])
    print(json.dumps({"document": meta, "index": index}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
