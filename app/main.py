import shutil
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.core.config import get_settings
from app.services import get_agent, get_parser, get_rag_service, get_store, get_vector_store


app = FastAPI(title="Agent Jinzheng")
settings = get_settings()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    target = settings.upload_path / file.filename
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    meta = get_parser().ingest(str(target))
    index_info = get_rag_service().index_document(meta["id"])
    return {"document": meta, "index": index_info}


@app.post("/api/ingest")
def ingest_existing(path: str = Form(...)):
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    meta = get_parser().ingest(str(pdf_path))
    index_info = get_rag_service().index_document(meta["id"])
    return {"document": meta, "index": index_info}


@app.get("/api/documents")
def list_documents():
    return {"documents": get_store().get_documents()}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    doc = get_store().get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    report_path = settings.processed_path / doc_id / "parse_report.json"
    parse_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return {
        "document": doc,
        "pages": get_store().get_pages(doc_id),
        "tables": get_store().get_tables(doc_id),
        "chunks": get_store().get_chunks(doc_id),
        "parse_report": parse_report,
    }


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    doc = get_store().get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    get_vector_store().delete_doc_vectors(doc_id)
    get_store().delete_document(doc_id)
    processed_dir = settings.processed_path / doc_id
    if processed_dir.exists():
        shutil.rmtree(processed_dir)
    file_path = Path(doc["file_path"])
    if file_path.exists() and settings.upload_path in file_path.parents:
        file_path.unlink()
    uploaded_copy = settings.upload_path / doc["file_name"]
    if uploaded_copy.exists():
        uploaded_copy.unlink()
    return {"deleted": True, "doc_id": doc_id}


@app.post("/api/ask")
def ask(payload: dict):
    doc_id = payload.get("doc_id")
    question = payload.get("question")
    if not doc_id or not question:
        raise HTTPException(status_code=400, detail="doc_id and question are required")
    if not get_store().get_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    default_top_k = int(settings.cfg("rag.final_top_k", 5))
    return get_agent().ask(
        doc_id,
        question,
        top_k=int(payload.get("top_k", default_top_k)),
        session_id=payload.get("session_id"),
    )


@app.post("/api/feedback")
def feedback(payload: dict):
    qa_log_id = payload.get("qa_log_id")
    value = payload.get("feedback")
    note = payload.get("note", "")
    if value not in {"resolved", "unresolved"}:
        raise HTTPException(status_code=400, detail="feedback must be resolved or unresolved")
    if not qa_log_id:
        raise HTTPException(status_code=400, detail="qa_log_id is required")
    updated = get_store().update_feedback(qa_log_id, value, note)
    if not updated:
        raise HTTPException(status_code=404, detail="QA log not found")
    return {"updated": True, "qa_log_id": qa_log_id, "feedback": value}


@app.get("/api/feedback/unresolved")
def list_unresolved_feedback(limit: int = 20):
    safe_limit = max(1, min(int(limit), 100))
    return {
        "summary": get_store().get_feedback_summary(),
        "items": get_store().get_feedback_items("unresolved", safe_limit),
    }


@app.get("/api/feedback/summary")
def feedback_summary():
    return get_store().get_feedback_summary()


@app.get("/api/qa-logs/{qa_log_id}")
def get_qa_log(qa_log_id: str):
    item = get_store().get_qa_log(qa_log_id)
    if not item:
        raise HTTPException(status_code=404, detail="QA log not found")
    return item
