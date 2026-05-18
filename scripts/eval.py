import json
import os
import sys
from pathlib import Path

os.environ.setdefault("VECTOR_BACKEND", "local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import get_agent, get_store


def main() -> None:
    docs = get_store().get_documents()
    if not docs:
        raise SystemExit("No document found. Run scripts/ingest.py first.")
    doc_id = docs[0]["id"]
    questions = json.loads(Path("demo/questions/default_questions.json").read_text(encoding="utf-8"))
    results = []
    for case in questions:
        result = get_agent().ask(doc_id, case["question"])
        passed = (result["self_check"]["action"] != "refuse") == bool(case["expect_answerable"])
        results.append(
            {
                "id": case["id"],
                "type": case["type"],
                "passed": passed,
                "action": result["self_check"]["action"],
                "grounded": result["self_check"]["grounded"],
                "citations": len(result["citations"]),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
