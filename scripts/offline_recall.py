import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("VECTOR_BACKEND", "local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import get_rag_service, get_store


def normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if not ch.isspace())


def summarize_evidence(evidence: list[dict]) -> list[dict]:
    rows = []
    for rank, item in enumerate(evidence, start=1):
        rows.append(
            {
                "rank": rank,
                "page": f"{item['page_start']}-{item['page_end']}",
                "type": item["type"],
                "score": item["score"],
                "rerank_score": item.get("rerank_score"),
                "title": item.get("title"),
                "snippet": item.get("snippet", "")[:260],
            }
        )
    return rows


def evaluate_case(case: dict, evidence: list[dict], out_of_scope_min_score: float) -> dict:
    evidence_text = normalize(
        "\n".join((item.get("title") or "") + " " + item.get("snippet", "") for item in evidence)
    )
    expected_keywords = case.get("expected_keywords", [])
    hit_keywords = [keyword for keyword in expected_keywords if normalize(keyword) in evidence_text]
    max_score = max((float(item["score"]) for item in evidence), default=0.0)

    if case.get("expect_answerable", True):
        passed = len(hit_keywords) == len(expected_keywords)
        reason = "all expected keywords found" if passed else "missing expected keywords"
    else:
        passed = max_score < out_of_scope_min_score
        reason = "top score below out-of-scope threshold" if passed else "top score may look answerable"

    return {
        "id": case["id"],
        "type": case["type"],
        "question": case["question"],
        "passed": passed,
        "reason": reason,
        "max_score": round(max_score, 4),
        "hit_ratio": f"{len(hit_keywords)}/{len(expected_keywords)}",
        "hit_keywords": hit_keywords,
        "missing_keywords": [keyword for keyword in expected_keywords if keyword not in hit_keywords],
        "top": summarize_evidence(evidence),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline RAG recall and print retrieved evidence.")
    parser.add_argument("--questions", default="demo/questions/default_questions.json")
    parser.add_argument("--doc-id", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of a compact report.")
    args = parser.parse_args()

    store = get_store()
    docs = store.get_documents()
    if not docs:
        raise SystemExit("No document found. Run scripts/ingest.py first.")
    doc_id = args.doc_id or docs[0]["id"]
    if not store.get_document(doc_id):
        raise SystemExit(f"Document not found: {doc_id}")

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    rag = get_rag_service()
    out_of_scope_min_score = float(rag.settings.cfg("agent.out_of_scope_min_score", 0.45))

    results = []
    for case in questions:
        evidence = rag.retrieve(doc_id, case["question"], top_k=args.top_k)
        results.append(evaluate_case(case, evidence, out_of_scope_min_score))

    if args.json:
        print(json.dumps({"doc_id": doc_id, "results": results}, ensure_ascii=False, indent=2))
        return

    passed = sum(1 for item in results if item["passed"])
    print(f"doc_id: {doc_id}")
    print(f"passed: {passed}/{len(results)}")
    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"\n[{status}] {item['id']} {item['hit_ratio']} max_score={item['max_score']}")
        if item["missing_keywords"]:
            print("missing:", ", ".join(item["missing_keywords"]))
        for top in item["top"][:3]:
            print(
                f"  #{top['rank']} p{top['page']} {top['type']} "
                f"score={top['score']} rerank={top['rerank_score']} {top['title']}"
            )
            print(f"     {top['snippet']}")


if __name__ == "__main__":
    main()
