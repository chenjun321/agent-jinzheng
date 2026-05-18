import hashlib
import html as html_lib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from app.document.text_normalizer import TextNormalizer
from app.storage.sqlite_store import SQLiteStore


CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.{0,80})")


def make_doc_id(file_path: Path) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", file_path.stem).strip("_").lower()
    safe = safe or "document"
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]
    return f"{safe}_{digest}"


class DocumentParser:
    def __init__(self, store: SQLiteStore, settings):
        self.store = store
        self.settings = settings
        self.text_normalizer = TextNormalizer(settings)
        self.processed_dir = Path(settings.effective_processed_dir)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def ingest(self, file_path: str, doc_id: str | None = None) -> dict:
        pdf_path = Path(file_path)
        doc_id = doc_id or make_doc_id(pdf_path)
        out_dir = self.processed_dir / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        original_path = out_dir / f"original{pdf_path.suffix.lower()}"
        shutil.copy2(pdf_path, original_path)

        pdf = fitz.open(pdf_path)
        max_pages = int(self.settings.cfg("document.pdf.max_pages", 300))
        if len(pdf) > max_pages:
            raise ValueError(f"PDF 页数 {len(pdf)} 超过配置上限 {max_pages}")
        text_pages = [page.get_text("text").strip() for page in pdf]
        text_chars = sum(len(text) for text in text_pages)
        min_chars = int(self.settings.cfg("document.pdf.text_pdf_min_chars_per_page", 20))
        pdf_type = "text" if text_chars >= max(80, len(pdf) * min_chars) else "scanned"

        pages = []
        raw_pages = []
        for index, page in enumerate(pdf, start=1):
            text = text_pages[index - 1]
            blocks = []
            confidence = 1.0 if text else 0.0
            if not text:
                text, blocks, confidence = self._ocr_page(page, index, out_dir)
            original_text = text
            normalized = self.text_normalizer.normalize_page_text(text)
            text = normalized.text
            page_item = {
                "id": f"{doc_id}_p{index}",
                "doc_id": doc_id,
                "page_no": index,
                "text": text,
                "ocr_confidence": confidence,
                "raw_json_path": str(out_dir / "pages.json"),
            }
            pages.append(page_item)
            raw_pages.append(
                {
                    **page_item,
                    "original_text": original_text,
                    "text_normalization": normalized.stats,
                    "blocks": blocks,
                }
            )

        tables = self._extract_tables(doc_id, raw_pages, out_dir)
        chunks = self._build_chunks(doc_id, raw_pages, tables)
        parse_report = self._build_parse_report(doc_id, raw_pages, tables, chunks, pdf_type)

        meta = {
            "id": doc_id,
            "file_name": pdf_path.name,
            "file_path": str(original_path),
            "pdf_type": pdf_type,
            "page_count": len(pdf),
            "parse_status": "needs_review" if parse_report["self_check"]["requires_review"] else "parsed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(out_dir / "document_meta.json", meta)
        self._write_json(out_dir / "pages.json", raw_pages)
        self._write_json(out_dir / "tables.json", tables)
        self._write_json(out_dir / "chunks.json", chunks)
        self._write_json(out_dir / "parse_report.json", parse_report)
        (out_dir / "pages.txt").write_text(self._pages_to_text(raw_pages), encoding="utf-8")

        self.store.upsert_document(meta)
        self.store.replace_pages(doc_id, pages)
        self.store.replace_tables(doc_id, tables)
        self.store.replace_chunks(doc_id, chunks)

        return {**meta, "pages": len(pages), "tables": len(tables), "chunks": len(chunks)}

    def _ocr_page(self, page: fitz.Page, page_no: int, out_dir: Path) -> tuple[str, list[dict], float]:
        zoom = float(self.settings.cfg("document.pdf.render_zoom", 2.0))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image_path = out_dir / f"page_{page_no}.png"
        if bool(self.settings.cfg("document.pdf.save_page_images", True)):
            pix.save(image_path)
        else:
            pix.save(image_path)
        image = Image.open(image_path)
        tessdata_dir = Path(self.settings.cfg("document.pdf.tessdata_dir", ".tessdata")).resolve()
        has_local_chinese = (tessdata_dir / "chi_sim.traineddata").exists()
        configured_lang = self.settings.cfg("document.pdf.ocr_lang", "chi_sim")
        lang = configured_lang if has_local_chinese else "eng"
        config = f'--tessdata-dir "{tessdata_dir}"' if has_local_chinese else ""
        try:
            data = pytesseract.image_to_data(image, lang=lang, config=config, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractError:
            data = pytesseract.image_to_data(image, lang="eng", output_type=pytesseract.Output.DICT)

        blocks = []
        texts = []
        confs = []
        for i, word in enumerate(data.get("text", [])):
            word = (word or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except ValueError:
                conf = -1.0
            if conf >= 0:
                confs.append(conf / 100.0)
            left = int(data["left"][i])
            top = int(data["top"][i])
            width = int(data["width"][i])
            height = int(data["height"][i])
            blocks.append(
                {
                    "block_id": f"p{page_no}_b{i}",
                    "text": word,
                    "bbox": [left, top, left + width, top + height],
                    "confidence": max(conf / 100.0, 0.0),
                }
            )
            texts.append(word)
        confidence = sum(confs) / len(confs) if confs else 0.0
        return "\n".join(self._merge_words_to_lines(blocks)), blocks, confidence

    def _merge_words_to_lines(self, blocks: list[dict]) -> list[str]:
        lines: dict[int, list[dict]] = {}
        for block in blocks:
            y = block["bbox"][1] // 18
            lines.setdefault(y, []).append(block)
        merged = []
        for _, items in sorted(lines.items()):
            items.sort(key=lambda item: item["bbox"][0])
            merged.append(" ".join(item["text"] for item in items))
        return merged

    def _extract_tables(self, doc_id: str, pages: list[dict], out_dir: Path) -> list[dict]:
        tables = []
        for page in pages:
            lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
            table_lines = [
                line
                for line in lines
                if re.search("|".join(map(re.escape, self.settings.cfg("document.pdf.table_keywords", ["表", "公差", "尺寸", "mm"]))), line, re.IGNORECASE)
            ]
            if len(table_lines) < 2:
                continue
            title = next((line for line in table_lines if re.search(r"表\s*\d+", line)), f"第 {page['page_no']} 页表格候选")
            rows = [[cell for cell in re.split(r"\s{2,}|\t|\|", line) if cell] for line in table_lines[:12]]
            markdown = self._rows_to_markdown(rows)
            html = self._rows_to_html(rows)
            facts = self._rows_to_facts(rows)
            tables.append(
                {
                    "id": f"{doc_id}_p{page['page_no']}_t1",
                    "doc_id": doc_id,
                    "page_no": page["page_no"],
                    "title": title,
                    "markdown": markdown,
                    "html": html,
                    "facts": facts,
                    "raw_json_path": str(out_dir / "tables.json"),
                    "confidence": page.get("ocr_confidence", 0.0),
                    "rows": rows,
                }
            )
        return tables

    def _rows_to_facts(self, rows: list[list[str]]) -> str:
        if len(rows) < 2 or len(rows[0]) < 2:
            return ""
        headers = rows[0]
        facts = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            item = row[0]
            values = []
            for index, value in enumerate(row[1:], start=1):
                if index < len(headers) and value:
                    values.append(f"{headers[index]}={value}")
            if not values:
                continue
            facts.append(f"{item}: {'; '.join(values)}")
        return "\n".join(facts)

    def _rows_to_markdown(self, rows: list[list[str]]) -> str:
        if not rows:
            return ""
        max_cols = max(len(row) for row in rows)
        padded = [row + [""] * (max_cols - len(row)) for row in rows]
        header = "| " + " | ".join(padded[0]) + " |"
        sep = "| " + " | ".join("---" for _ in range(max_cols)) + " |"
        body = ["| " + " | ".join(row) + " |" for row in padded[1:]]
        return "\n".join([header, sep, *body])

    def _rows_to_html(self, rows: list[list[str]]) -> str:
        if not rows:
            return ""
        max_cols = max(len(row) for row in rows)
        padded = [row + [""] * (max_cols - len(row)) for row in rows]

        def cells_to_html(row: list[str], tag: str) -> str:
            cells = "".join(f"<{tag}>{html_lib.escape(cell)}</{tag}>" for cell in row)
            return f"<tr>{cells}</tr>"

        head = cells_to_html(padded[0], "th")
        body = "\n".join(cells_to_html(row, "td") for row in padded[1:])
        return f"<table>\n<thead>\n{head}\n</thead>\n<tbody>\n{body}\n</tbody>\n</table>"

    def _build_chunks(self, doc_id: str, pages: list[dict], tables: list[dict]) -> list[dict]:
        chunks = []
        text_chunks = []
        for page in pages:
            text = page["text"].strip()
            if not text:
                continue
            page_chunks = self._split_page_text(text)
            for idx, item in enumerate(page_chunks, start=1):
                text_chunks.append(
                    {
                        "id": f"{doc_id}_p{page['page_no']}_c{idx}",
                        "doc_id": doc_id,
                        "chunk_type": "clause" if item["clause_id"] else "page",
                        "title": item["title"] or f"第 {page['page_no']} 页片段 {idx}",
                        "text": item["text"],
                        "page_start": page["page_no"],
                        "page_end": page["page_no"],
                        "milvus_pk": f"{doc_id}_p{page['page_no']}_c{idx}",
                        "metadata_json": json.dumps(
                            {"clause_id": item["clause_id"], "ocr_confidence": page.get("ocr_confidence", 0.0)},
                            ensure_ascii=False,
                        ),
                    }
                )
        chunks.extend(self._merge_cross_page_chunks(text_chunks))
        for table in tables:
            chunks.append(
                {
                    "id": table["id"],
                    "doc_id": doc_id,
                    "chunk_type": "table",
                    "title": table["title"],
                    "text": (
                        f"{table['title']}\n"
                        f"{table.get('facts') or ''}\n\n"
                        "HTML表格:\n"
                        f"{table.get('html') or ''}\n\n"
                        "Markdown备份:\n"
                        f"{table['markdown']}"
                    ),
                    "page_start": table["page_no"],
                    "page_end": table["page_no"],
                    "milvus_pk": table["id"],
                    "metadata_json": json.dumps(
                        {"table_id": table["id"], "confidence": table["confidence"], "content_format": "facts+html+markdown"},
                        ensure_ascii=False,
                    ),
                }
            )
        return chunks

    def _merge_cross_page_chunks(self, chunks: list[dict]) -> list[dict]:
        if not bool(self.settings.cfg("document.chunking.merge_cross_page_continuations", True)):
            return chunks
        merged: list[dict] = []
        for chunk in chunks:
            if merged and self._is_cross_page_continuation(merged[-1], chunk):
                previous = merged[-1]
                previous["text"] = f"{previous['text'].rstrip()}\n{self._strip_repeated_page_header(chunk['text']).lstrip()}"
                previous["page_end"] = chunk["page_end"]
                metadata = json.loads(previous["metadata_json"] or "{}")
                metadata["cross_page_continuation"] = True
                metadata["continued_to_page"] = chunk["page_end"]
                previous["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
                continue
            merged.append(chunk)
        return merged

    def _is_cross_page_continuation(self, previous: dict, current: dict) -> bool:
        if previous["page_end"] + 1 != current["page_start"]:
            return False
        if previous["chunk_type"] != "clause" or current["chunk_type"] != "page":
            return False
        current_text = self._strip_repeated_page_header(current["text"]).strip()
        if not current_text:
            return False
        first_line = current_text.splitlines()[0].strip()
        if CLAUSE_RE.match(first_line):
            return False
        if self._ends_with_terminal_punctuation(previous["text"]):
            return False
        return True

    def _strip_repeated_page_header(self, text: str) -> str:
        lines = [line for line in text.splitlines()]
        while lines and self._is_page_running_header(lines[0]):
            lines.pop(0)
        return "\n".join(lines)

    def _is_page_running_header(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return True
        if stripped in {"一", "-", "—"}:
            return True
        return bool(re.match(r"^GB/T\s*1568[-\s]?2008$", stripped, re.IGNORECASE))

    def _ends_with_terminal_punctuation(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        return bool(re.search(r"[。.!?？！;；]$", stripped))

    def _split_page_text(self, text: str) -> list[dict]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []
        chunks = []
        current = {"clause_id": "", "title": "", "lines": []}
        for line in lines:
            match = CLAUSE_RE.match(line)
            if match and current["lines"]:
                chunks.append(current)
                current = {"clause_id": match.group(1), "title": line[:80], "lines": [line]}
            else:
                if match and not current["lines"]:
                    current["clause_id"] = match.group(1)
                    current["title"] = line[:80]
                current["lines"].append(line)
        if current["lines"]:
            chunks.append(current)

        packed = []
        for item in chunks:
            text_value = "\n".join(item["lines"])
            chunk_size = int(self.settings.cfg("document.chunking.chunk_size", 1000))
            overlap = int(self.settings.cfg("document.chunking.chunk_overlap", 120))
            if len(text_value) <= chunk_size:
                packed.append({"clause_id": item["clause_id"], "title": item["title"], "text": text_value})
                continue
            step = max(1, chunk_size - overlap)
            for i in range(0, len(text_value), step):
                packed.append(
                    {
                        "clause_id": item["clause_id"],
                        "title": item["title"],
                        "text": text_value[i : i + chunk_size],
                    }
                )
        return packed

    def _write_json(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _pages_to_text(self, pages: list[dict]) -> str:
        parts = []
        for page in pages:
            parts.append(f"===== PAGE {page['page_no']} | confidence={page.get('ocr_confidence', 0):.3f} =====")
            parts.append(page.get("text") or "")
        return "\n\n".join(parts)

    def _build_parse_report(self, doc_id: str, pages: list[dict], tables: list[dict], chunks: list[dict], pdf_type: str) -> dict:
        low_threshold = float(self.settings.cfg("document.pdf.low_ocr_confidence", 0.55))
        empty_min_chars = int(self.settings.cfg("document.pdf.empty_page_min_chars", 20))
        confidences = [float(page.get("ocr_confidence") or 0.0) for page in pages]
        cleanup_summary = self._build_cleanup_summary(pages)
        low_pages = [
            {"page_no": page["page_no"], "ocr_confidence": page.get("ocr_confidence", 0.0)}
            for page in pages
            if float(page.get("ocr_confidence") or 0.0) < low_threshold
        ]
        empty_pages = [
            page["page_no"]
            for page in pages
            if len((page.get("text") or "").strip()) < empty_min_chars
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        requires_review = bool(low_pages or empty_pages or not chunks)
        issues = []
        if low_pages:
            issues.append("部分页面 OCR 置信度偏低")
        if empty_pages:
            issues.append("存在疑似空白或解析失败页面")
        if not tables:
            issues.append("未识别到表格候选")
        if not chunks:
            issues.append("未生成可检索 chunk")
        return {
            "doc_id": doc_id,
            "pdf_type": pdf_type,
            "page_count": len(pages),
            "avg_ocr_confidence": round(avg_confidence, 4),
            "low_confidence_pages": low_pages,
            "empty_pages": empty_pages,
            "table_count": len(tables),
            "chunk_count": len(chunks),
            "text_cleanup": cleanup_summary,
            "self_check": {
                "requires_review": requires_review,
                "risk": "medium" if requires_review else "low",
                "issues": issues,
                "recommendation": "建议人工复核 OCR 和表格结果" if requires_review else "解析质量通过基础自检",
            },
        }

    def _build_cleanup_summary(self, pages: list[dict]) -> dict:
        summary: dict[str, int | bool] = {
            "enabled": bool(self.settings.cfg("document.text_cleanup.enabled", True)),
            "cjk_space_fixes": 0,
            "standard_number_fixes": 0,
            "punctuation_space_fixes": 0,
            "quote_noise_fixes": 0,
            "wrapped_line_merges": 0,
            "orphan_line_merges": 0,
        }
        for page in pages:
            stats = page.get("text_normalization") or {}
            for key in (
                "cjk_space_fixes",
                "standard_number_fixes",
                "punctuation_space_fixes",
                "quote_noise_fixes",
                "wrapped_line_merges",
                "orphan_line_merges",
            ):
                summary[key] = int(summary.get(key, 0)) + int(stats.get(key, 0) or 0)
        return summary
