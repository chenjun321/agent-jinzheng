import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists documents(
                  id text primary key,
                  file_name text not null,
                  file_path text not null,
                  pdf_type text not null,
                  page_count integer not null,
                  parse_status text not null,
                  created_at text not null
                );

                create table if not exists pages(
                  id text primary key,
                  doc_id text not null,
                  page_no integer not null,
                  text text,
                  ocr_confidence real,
                  raw_json_path text
                );

                create table if not exists tables(
                  id text primary key,
                  doc_id text not null,
                  page_no integer not null,
                  title text,
                  markdown text,
                  html text,
                  raw_json_path text,
                  confidence real
                );

                create table if not exists chunks(
                  id text primary key,
                  doc_id text not null,
                  chunk_type text not null,
                  title text,
                  text text not null,
                  page_start integer not null,
                  page_end integer not null,
                  milvus_pk text,
                  metadata_json text
                );

                create table if not exists qa_logs(
                  id text primary key,
                  doc_id text not null,
                  session_id text,
                  question text not null,
                  answer text not null,
                  citations_json text,
                  self_check_json text,
                  feedback text,
                  feedback_note text,
                  created_at text not null
                );

                create table if not exists chat_sessions(
                  id text primary key,
                  doc_id text,
                  status text not null,
                  started_at text not null,
                  last_active_at text not null,
                  archived_at text
                );

                create table if not exists chat_messages(
                  id text primary key,
                  session_id text not null,
                  doc_id text,
                  role text not null,
                  content text not null,
                  payload_json text,
                  created_at text not null
                );
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("pragma table_info(qa_logs)").fetchall()}
        migrations = {
            "session_id": "alter table qa_logs add column session_id text",
            "feedback": "alter table qa_logs add column feedback text",
            "feedback_note": "alter table qa_logs add column feedback_note text",
        }
        for column, sql in migrations.items():
            if column not in existing:
                conn.execute(sql)
        table_columns = {row["name"] for row in conn.execute("pragma table_info(tables)").fetchall()}
        if "html" not in table_columns:
            conn.execute("alter table tables add column html text")

    def upsert_document(self, doc: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into documents(id, file_name, file_path, pdf_type, page_count, parse_status, created_at)
                values(:id, :file_name, :file_path, :pdf_type, :page_count, :parse_status, :created_at)
                on conflict(id) do update set
                  file_name=excluded.file_name,
                  file_path=excluded.file_path,
                  pdf_type=excluded.pdf_type,
                  page_count=excluded.page_count,
                  parse_status=excluded.parse_status
                """,
                doc,
            )

    def replace_pages(self, doc_id: str, pages: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("delete from pages where doc_id = ?", (doc_id,))
            conn.executemany(
                """
                insert into pages(id, doc_id, page_no, text, ocr_confidence, raw_json_path)
                values(:id, :doc_id, :page_no, :text, :ocr_confidence, :raw_json_path)
                """,
                pages,
            )

    def replace_tables(self, doc_id: str, tables: list[dict[str, Any]]) -> None:
        payloads = [{**table, "html": table.get("html", "")} for table in tables]
        with self.connect() as conn:
            conn.execute("delete from tables where doc_id = ?", (doc_id,))
            conn.executemany(
                """
                insert into tables(id, doc_id, page_no, title, markdown, html, raw_json_path, confidence)
                values(:id, :doc_id, :page_no, :title, :markdown, :html, :raw_json_path, :confidence)
                """,
                payloads,
            )

    def replace_chunks(self, doc_id: str, chunks: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("delete from chunks where doc_id = ?", (doc_id,))
            conn.executemany(
                """
                insert into chunks(id, doc_id, chunk_type, title, text, page_start, page_end, milvus_pk, metadata_json)
                values(:id, :doc_id, :chunk_type, :title, :text, :page_start, :page_end, :milvus_pk, :metadata_json)
                """,
                chunks,
            )

    def get_documents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                  d.*,
                  count(distinct c.id) as chunk_count,
                  count(distinct t.id) as table_count,
                  count(distinct p.id) as parsed_page_count
                from documents d
                left join chunks c on c.doc_id = d.id
                left join tables t on t.doc_id = d.id
                left join pages p on p.doc_id = d.id
                group by d.id
                order by d.created_at desc
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from documents where id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def get_pages(self, doc_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from pages where doc_id = ? order by page_no", (doc_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_tables(self, doc_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from tables where doc_id = ? order by page_no", (doc_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("select * from chunks where doc_id = ?", (doc_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.connect() as conn:
            rows = conn.execute(f"select * from chunks where id in ({placeholders})", chunk_ids).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[item] for item in chunk_ids if item in by_id]

    def insert_qa_log(self, item: dict[str, Any]) -> None:
        payload = {
            **item,
            "session_id": item.get("session_id"),
            "citations_json": json.dumps(item["citations"], ensure_ascii=False),
            "self_check_json": json.dumps(item["self_check"], ensure_ascii=False),
            "feedback": item.get("feedback"),
            "feedback_note": item.get("feedback_note"),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into qa_logs(id, doc_id, session_id, question, answer, citations_json, self_check_json, feedback, feedback_note, created_at)
                values(:id, :doc_id, :session_id, :question, :answer, :citations_json, :self_check_json, :feedback, :feedback_note, :created_at)
                """,
                payload,
            )
        return item["id"]

    def delete_document(self, doc_id: str) -> None:
        with self.connect() as conn:
            for table in ("pages", "tables", "chunks", "qa_logs", "chat_messages", "chat_sessions"):
                conn.execute(f"delete from {table} where doc_id = ?", (doc_id,))
            conn.execute("delete from documents where id = ?", (doc_id,))

    def update_feedback(self, qa_log_id: str, feedback: str, note: str = "") -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "update qa_logs set feedback = ?, feedback_note = ? where id = ?",
                (feedback, note, qa_log_id),
            )
            return cur.rowcount > 0

    def get_feedback_items(self, feedback: str = "unresolved", limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                  q.id,
                  q.doc_id,
                  d.file_name,
                  q.session_id,
                  q.question,
                  q.answer,
                  q.self_check_json,
                  q.feedback,
                  q.feedback_note,
                  q.created_at
                from qa_logs q
                left join documents d on d.id = q.doc_id
                where q.feedback = ?
                order by q.created_at desc
                limit ?
                """,
                (feedback, limit),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["self_check"] = json.loads(item.pop("self_check_json") or "{}")
            except json.JSONDecodeError:
                item["self_check"] = {}
            items.append(item)
        return items

    def get_feedback_summary(self) -> dict[str, int]:
        with self.connect() as conn:
            total = conn.execute("select count(*) from qa_logs").fetchone()[0]
            unresolved = conn.execute("select count(*) from qa_logs where feedback = 'unresolved'").fetchone()[0]
            resolved = conn.execute("select count(*) from qa_logs where feedback = 'resolved'").fetchone()[0]
            unmarked = conn.execute("select count(*) from qa_logs where feedback is null or feedback = ''").fetchone()[0]
        return {
            "total_qa": int(total),
            "unresolved": int(unresolved),
            "resolved": int(resolved),
            "unmarked": int(unmarked),
        }

    def get_qa_log(self, qa_log_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select
                  q.*,
                  d.file_name
                from qa_logs q
                left join documents d on d.id = q.doc_id
                where q.id = ?
                """,
                (qa_log_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        citations_json = item.pop("citations_json", "") or "[]"
        self_check_json = item.pop("self_check_json", "") or "{}"
        try:
            item["citations"] = json.loads(citations_json)
        except json.JSONDecodeError:
            item["citations"] = []
        try:
            item["self_check"] = json.loads(self_check_json)
        except json.JSONDecodeError:
            item["self_check"] = {}
        return item

    def upsert_session(self, session: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into chat_sessions(id, doc_id, status, started_at, last_active_at, archived_at)
                values(:id, :doc_id, :status, :started_at, :last_active_at, :archived_at)
                on conflict(id) do update set
                  doc_id=excluded.doc_id,
                  status=excluded.status,
                  last_active_at=excluded.last_active_at,
                  archived_at=excluded.archived_at
                """,
                session,
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("select * from chat_sessions where id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def archive_session(self, session_id: str, archived_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update chat_sessions set status = 'archived', archived_at = ? where id = ?",
                (archived_at, session_id),
            )

    def insert_chat_message(self, message: dict[str, Any]) -> None:
        payload = {
            **message,
            "payload_json": json.dumps(message.get("payload") or {}, ensure_ascii=False),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into chat_messages(id, session_id, doc_id, role, content, payload_json, created_at)
                values(:id, :session_id, :doc_id, :role, :content, :payload_json, :created_at)
                """,
                payload,
            )
