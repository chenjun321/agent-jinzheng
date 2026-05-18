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
            layout_tables = []
            if bool(self.settings.cfg("document.pdf.table_ocr.enabled", True)):
                layout_tables = self._extract_tables_from_layout_ocr(doc_id, page, out_dir)
            if layout_tables:
                tables.extend(layout_tables)
                continue
            text_table = self._extract_table_from_text_keywords(doc_id, page, out_dir)
            if text_table:
                tables.append(text_table)
        return tables

    def _extract_table_from_text_keywords(self, doc_id: str, page: dict, out_dir: Path) -> dict | None:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        keyword_pattern = self._table_keyword_pattern()
        table_lines = [line for line in lines if re.search(keyword_pattern, line, re.IGNORECASE)]
        if len(table_lines) < 2:
            return None
        title = next((line for line in table_lines if re.search(r"表\s*\d+", line)), f"第 {page['page_no']} 页表格候选")
        rows = [[cell for cell in re.split(r"\s{2,}|\t|\|", line) if cell] for line in table_lines[:12]]
        if not self._looks_like_text_table_rows(rows):
            return None
        return self._build_table_item(
            doc_id=doc_id,
            page=page,
            out_dir=out_dir,
            table_index=1,
            title=title,
            rows=rows,
            confidence=page.get("ocr_confidence", 0.0),
            extraction_method="text_keywords",
            bbox=[],
            cells=[],
        )

    def _extract_tables_from_layout_ocr(self, doc_id: str, page: dict, out_dir: Path) -> list[dict]:
        lines = self._blocks_to_layout_lines(page.get("blocks") or [])
        if not lines:
            return []

        image_path = out_dir / f"page_{page['page_no']}.png"
        candidates = self._find_table_regions(lines, image_path)
        tables = []
        for table_index, candidate in enumerate(candidates, start=1):
            bbox = candidate["bbox"]
            grid = self._detect_table_grid(image_path, bbox) if image_path.exists() else {}
            ocr_blocks = self._ocr_table_region(image_path, bbox, page["page_no"]) if image_path.exists() else []
            rows, cells = self._best_table_rows(candidate["lines"], ocr_blocks, grid)
            if not self._looks_like_structured_table(rows):
                continue
            title = self._table_title_from_page_text(page, f"第 {page['page_no']} 页表格候选")
            confidence = self._average_cell_confidence(cells) or page.get("ocr_confidence", 0.0)
            tables.append(
                self._build_table_item(
                    doc_id=doc_id,
                    page=page,
                    out_dir=out_dir,
                    table_index=table_index,
                    title=title,
                    rows=rows,
                    confidence=confidence,
                    extraction_method="layout_ocr",
                    bbox=bbox,
                    cells=cells,
                    grid=grid,
                    source_image_path=str(image_path),
                )
            )
        return tables

    def _best_table_rows(
        self,
        candidate_lines: list[dict],
        ocr_blocks: list[dict],
        grid: dict | None = None,
    ) -> tuple[list[list[str]], list[list[dict]]]:
        original_rows, original_cells = self._layout_lines_to_rows(candidate_lines, grid)
        if not ocr_blocks:
            return original_rows, original_cells
        ocr_rows, ocr_cells = self._layout_lines_to_rows(self._blocks_to_layout_lines(ocr_blocks), grid)
        if not self._looks_like_structured_table(ocr_rows):
            return original_rows, original_cells
        if self._numeric_cell_count(ocr_rows) > self._numeric_cell_count(original_rows) and self._average_cell_confidence(ocr_cells) >= 0.4:
            return ocr_rows, ocr_cells
        original_score = self._table_ocr_score(original_rows, original_cells)
        ocr_score = self._table_ocr_score(ocr_rows, ocr_cells)
        if ocr_score >= original_score:
            return ocr_rows, ocr_cells
        return original_rows, original_cells

    def _table_ocr_score(self, rows: list[list[str]], cells: list[list[dict]]) -> float:
        if not rows:
            return 0.0
        confidence = self._average_cell_confidence(cells)
        row_score = min(len(rows) / 8, 1.0) * 0.15
        column_score = min(max(len(row) for row in rows) / 4, 1.0) * 0.15
        return confidence * 0.7 + row_score + column_score

    def _numeric_cell_count(self, rows: list[list[str]]) -> int:
        return sum(1 for row in rows for cell in row if re.search(r"\d+(?:\.\d+)?", cell or ""))

    def _build_table_item(
        self,
        doc_id: str,
        page: dict,
        out_dir: Path,
        table_index: int,
        title: str,
        rows: list[list[str]],
        confidence: float,
        extraction_method: str,
        bbox: list[int],
        cells: list[list[dict]],
        grid: dict | None = None,
        source_image_path: str = "",
    ) -> dict:
        markdown = self._rows_to_markdown(rows)
        html = self._rows_to_html(rows)
        facts = self._rows_to_facts(rows)
        table = {
            "id": f"{doc_id}_p{page['page_no']}_t{table_index}",
            "doc_id": doc_id,
            "page_no": page["page_no"],
            "title": title,
            "markdown": markdown,
            "html": html,
            "facts": facts,
            "raw_json_path": str(out_dir / "tables.json"),
            "confidence": confidence,
            "rows": rows,
            "extraction_method": extraction_method,
        }
        if bbox:
            table["bbox"] = bbox
        if cells:
            table["cells"] = cells
        if grid:
            table["grid"] = grid
        if source_image_path:
            table["source_image_path"] = source_image_path
        return table

    def _ocr_table_region(self, image_path: Path, bbox: list[int], page_no: int) -> list[dict]:
        image = Image.open(image_path)
        padding = int(self.settings.cfg("document.pdf.table_ocr.crop_padding", 12))
        left = max(0, bbox[0] - padding)
        top = max(0, bbox[1] - padding)
        right = min(image.width, bbox[2] + padding)
        bottom = min(image.height, bbox[3] + padding)
        crop = image.crop((left, top, right, bottom))
        upscale = float(self.settings.cfg("document.pdf.table_ocr.upscale", 1.5))
        if upscale > 1:
            crop = crop.resize((int(crop.width * upscale), int(crop.height * upscale)))

        tessdata_dir = Path(self.settings.cfg("document.pdf.tessdata_dir", ".tessdata")).resolve()
        has_local_chinese = (tessdata_dir / "chi_sim.traineddata").exists()
        configured_lang = self.settings.cfg("document.pdf.ocr_lang", "chi_sim")
        lang = configured_lang if has_local_chinese else "eng"
        config_parts = ["--psm", str(self.settings.cfg("document.pdf.table_ocr.psm", 6))]
        if has_local_chinese:
            config_parts.extend(["--tessdata-dir", f'"{tessdata_dir}"'])
        config = " ".join(config_parts)
        try:
            data = pytesseract.image_to_data(crop, lang=lang, config=config, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractError:
            data = pytesseract.image_to_data(crop, lang="eng", config="--psm 6", output_type=pytesseract.Output.DICT)

        blocks = []
        for i, word in enumerate(data.get("text", [])):
            word = (word or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except ValueError:
                conf = -1.0
            x1 = int(left + int(data["left"][i]) / upscale)
            y1 = int(top + int(data["top"][i]) / upscale)
            x2 = int(left + (int(data["left"][i]) + int(data["width"][i])) / upscale)
            y2 = int(top + (int(data["top"][i]) + int(data["height"][i])) / upscale)
            blocks.append(
                {
                    "block_id": f"p{page_no}_table_b{i}",
                    "text": word,
                    "bbox": [x1, y1, x2, y2],
                    "confidence": max(conf / 100.0, 0.0),
                }
            )
        return blocks

    def _blocks_to_layout_lines(self, blocks: list[dict]) -> list[dict]:
        clean_blocks = [block for block in blocks if (block.get("text") or "").strip()]
        if not clean_blocks:
            return []
        heights = [max(1, block["bbox"][3] - block["bbox"][1]) for block in clean_blocks]
        median_height = sorted(heights)[len(heights) // 2]
        tolerance = max(10, int(median_height * 0.75))
        grouped: list[list[dict]] = []
        centers: list[float] = []
        for block in sorted(clean_blocks, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2, item["bbox"][0])):
            center_y = (block["bbox"][1] + block["bbox"][3]) / 2
            if grouped and abs(center_y - centers[-1]) <= tolerance:
                grouped[-1].append(block)
                centers[-1] = sum((item["bbox"][1] + item["bbox"][3]) / 2 for item in grouped[-1]) / len(grouped[-1])
            else:
                grouped.append([block])
                centers.append(center_y)

        lines = []
        for items in grouped:
            items.sort(key=lambda item: item["bbox"][0])
            bbox = self._union_bbox([item["bbox"] for item in items])
            text = " ".join(item["text"] for item in items).strip()
            lines.append({"text": text, "bbox": bbox, "blocks": items})
        return lines

    def _find_table_regions(self, lines: list[dict], image_path: Path | None = None) -> list[dict]:
        regions = []
        keyword_pattern = self._table_keyword_pattern(extra_keywords=["AQL", "合格质量水平", "检查项目"])
        for start, line in enumerate(lines):
            if not re.search(keyword_pattern, line["text"], re.IGNORECASE):
                continue
            region_lines = []
            for index in range(start, len(lines)):
                current = lines[index]
                if index > start and CLAUSE_RE.match(current["text"]):
                    break
                if region_lines and current["bbox"][1] - region_lines[-1]["bbox"][3] > 80:
                    break
                region_lines.append(current)
            if len(region_lines) < 3:
                continue
            bbox = self._union_bbox([item["bbox"] for item in region_lines])
            if not self._region_has_table_shape(region_lines, bbox):
                continue
            if image_path and image_path.exists() and not self._region_has_table_grid(image_path, bbox):
                continue
            if any(self._bbox_overlap_ratio(bbox, region["bbox"]) > 0.7 for region in regions):
                continue
            regions.append({"bbox": bbox, "lines": region_lines})
        return regions

    def _region_has_table_shape(self, lines: list[dict], bbox: list[int]) -> bool:
        multi_cell_lines = 0
        x_spread = bbox[2] - bbox[0]
        for line in lines:
            row_cells = self._line_blocks_to_cells(line["blocks"])
            if len(row_cells) >= 2:
                multi_cell_lines += 1
        return len(lines) >= 3 and (multi_cell_lines >= 1 or x_spread >= 350)

    def _region_has_table_grid(self, image_path: Path, bbox: list[int]) -> bool:
        grid = self._detect_table_grid(image_path, bbox)
        return int(grid.get("detected_horizontal_lines", 0)) >= 2 and int(grid.get("detected_vertical_lines", 0)) >= 2

    def _detect_table_grid(self, image_path: Path, bbox: list[int]) -> dict:
        image = Image.open(image_path).convert("L")
        padding = int(self.settings.cfg("document.pdf.table_ocr.crop_padding", 12))
        left = max(0, bbox[0] - padding)
        top = max(0, bbox[1] - padding)
        right = min(image.width, bbox[2] + padding)
        bottom = min(image.height, bbox[3] + padding)
        crop = image.crop((left, top, right, bottom))
        width, height = crop.size
        if width < 80 or height < 60:
            return {"horizontal_lines": [], "vertical_lines": []}
        pixels = crop.load()
        dark_threshold = int(self.settings.cfg("document.pdf.table_ocr.grid_dark_threshold", 180))
        min_horizontal_ratio = float(self.settings.cfg("document.pdf.table_ocr.grid_horizontal_ratio", 0.45))
        min_vertical_ratio = float(self.settings.cfg("document.pdf.table_ocr.grid_vertical_ratio", 0.45))

        horizontal_hits = [
            y
            for y in range(height)
            if sum(1 for x in range(width) if pixels[x, y] < dark_threshold) >= width * min_horizontal_ratio
        ]
        vertical_hits = [
            x
            for x in range(width)
            if sum(1 for y in range(height) if pixels[x, y] < dark_threshold) >= height * min_vertical_ratio
        ]
        detected_horizontal = [top + pos for pos in self._consecutive_group_centers(horizontal_hits)]
        detected_vertical = [left + pos for pos in self._consecutive_group_centers(vertical_hits)]
        horizontal_lines = self._with_outer_boundaries(
            detected_horizontal,
            bbox[1],
            bbox[3],
        )
        vertical_lines = self._with_outer_boundaries(
            detected_vertical,
            bbox[0],
            bbox[2],
        )
        return {
            "horizontal_lines": horizontal_lines,
            "vertical_lines": vertical_lines,
            "detected_horizontal_lines": len(detected_horizontal),
            "detected_vertical_lines": len(detected_vertical),
        }

    def _layout_lines_to_rows(self, lines: list[dict], grid: dict | None = None) -> tuple[list[list[str]], list[list[dict]]]:
        rows = []
        cells = []
        vertical_lines = (grid or {}).get("vertical_lines") or []
        column_count = max(0, len(vertical_lines) - 1)
        for row_index, line in enumerate(lines):
            row_cells = self._line_blocks_to_cells(line["blocks"])
            if not row_cells:
                continue
            row_texts = [""] * column_count if column_count else [cell["text"] for cell in row_cells]
            rows.append(row_texts)
            row_json = []
            for fallback_col, cell in enumerate(row_cells):
                col_index = self._grid_column_index(cell["bbox"], vertical_lines) if column_count else fallback_col
                if column_count and 0 <= col_index < column_count:
                    row_texts[col_index] = f"{row_texts[col_index]}{cell['text']}".strip()
                row_json.append(
                    {
                        "row": row_index,
                        "col": col_index,
                        "text": cell["text"],
                        "bbox": cell["bbox"],
                        "confidence": cell["confidence"],
                    }
                )
            cells.append(row_json)
        return rows, cells

    def _line_blocks_to_cells(self, blocks: list[dict]) -> list[dict]:
        if not blocks:
            return []
        items = sorted(blocks, key=lambda item: item["bbox"][0])
        heights = [max(1, item["bbox"][3] - item["bbox"][1]) for item in items]
        median_height = sorted(heights)[len(heights) // 2]
        gap_threshold = max(18, int(median_height * 1.4))
        groups: list[list[dict]] = [[items[0]]]
        for item in items[1:]:
            gap = item["bbox"][0] - groups[-1][-1]["bbox"][2]
            if gap > gap_threshold:
                groups.append([item])
            else:
                groups[-1].append(item)

        cells = []
        for group in groups:
            text = "".join(item["text"] for item in group).strip()
            if not text:
                continue
            confidences = [float(item.get("confidence") or 0.0) for item in group]
            cells.append(
                {
                    "text": text,
                    "bbox": self._union_bbox([item["bbox"] for item in group]),
                    "confidence": sum(confidences) / len(confidences) if confidences else 0.0,
                }
            )
        return cells

    def _looks_like_structured_table(self, rows: list[list[str]]) -> bool:
        if len(rows) < 3:
            return False
        max_cols = max(len(row) for row in rows)
        table_words = "".join("".join(row) for row in rows)
        has_table_terms = bool(re.search(r"表|AQL|合格质量水平|检查项目|公差|尺寸|键宽|键高|键长|直径|斜度", table_words, re.IGNORECASE))
        return max_cols >= 2 or has_table_terms

    def _looks_like_text_table_rows(self, rows: list[list[str]]) -> bool:
        if len(rows) < 2:
            return False
        multi_column_rows = sum(1 for row in rows if len(row) >= 2)
        return multi_column_rows >= 2

    def _table_title_from_page_text(self, page: dict, default: str) -> str:
        for line in (page.get("text") or "").splitlines():
            stripped = line.strip()
            if re.search(r"表\s*\d+", stripped):
                return stripped[:120]
        return default

    def _average_cell_confidence(self, cells: list[list[dict]]) -> float:
        confidences = [float(cell.get("confidence") or 0.0) for row in cells for cell in row]
        return sum(confidences) / len(confidences) if confidences else 0.0

    def _table_keyword_pattern(self, extra_keywords: list[str] | None = None) -> str:
        keywords = list(self.settings.cfg("document.pdf.table_keywords", ["表", "公差", "尺寸", "mm"]))
        if extra_keywords:
            keywords.extend(extra_keywords)
        return "|".join(map(re.escape, keywords))

    def _union_bbox(self, boxes: list[list[int]]) -> list[int]:
        return [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]

    def _bbox_overlap_ratio(self, first: list[int], second: list[int]) -> float:
        x1 = max(first[0], second[0])
        y1 = max(first[1], second[1])
        x2 = min(first[2], second[2])
        y2 = min(first[3], second[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        overlap = (x2 - x1) * (y2 - y1)
        first_area = max(1, (first[2] - first[0]) * (first[3] - first[1]))
        second_area = max(1, (second[2] - second[0]) * (second[3] - second[1]))
        return overlap / min(first_area, second_area)

    def _count_consecutive_groups(self, values: list[int]) -> int:
        if not values:
            return 0
        groups = 1
        previous = values[0]
        for value in values[1:]:
            if value > previous + 1:
                groups += 1
            previous = value
        return groups

    def _consecutive_group_centers(self, values: list[int]) -> list[int]:
        if not values:
            return []
        groups: list[list[int]] = [[values[0]]]
        for value in values[1:]:
            if value > groups[-1][-1] + 1:
                groups.append([value])
            else:
                groups[-1].append(value)
        return [round(sum(group) / len(group)) for group in groups]

    def _with_outer_boundaries(self, lines: list[int], start: int, end: int) -> list[int]:
        tolerance = 8
        bounded = sorted(line for line in lines if start - tolerance <= line <= end + tolerance)
        if not bounded or abs(bounded[0] - start) > tolerance:
            bounded.insert(0, start)
        if abs(bounded[-1] - end) > tolerance:
            bounded.append(end)
        return sorted(dict.fromkeys(bounded))

    def _grid_column_index(self, bbox: list[int], vertical_lines: list[int]) -> int:
        if len(vertical_lines) < 2:
            return 0
        center_x = (bbox[0] + bbox[2]) / 2
        for index in range(len(vertical_lines) - 1):
            if vertical_lines[index] <= center_x <= vertical_lines[index + 1]:
                return index
        if center_x < vertical_lines[0]:
            return 0
        return len(vertical_lines) - 2

    def _rows_to_facts(self, rows: list[list[str]]) -> str:
        aql_facts = self._rows_to_aql_facts(rows)
        if aql_facts:
            return aql_facts
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

    def _rows_to_aql_facts(self, rows: list[list[str]]) -> str:
        joined = "".join("".join(row) for row in rows)
        if "AQL" not in joined and "合格质量水平" not in joined:
            return ""
        if not rows or max(len(row) for row in rows) < 4:
            return ""

        column_names = self._infer_aql_column_names(rows)
        item_aliases = {
            "平行度": "键宽平行度",
            "键宽": "键宽 b",
            "键高": "键高 h",
            "键长": "键长 L",
            "直径": "直径 d",
            "斜度": "1:100 斜度",
        }
        facts = []
        for row in rows:
            if len(row) < 2:
                continue
            item = self._normalize_table_text(row[0])
            if not item or any(skip in item for skip in ("检查项目", "合格质量水平", "普通", "导向", "薄型", "半圆键")):
                continue
            values = []
            for index, value in enumerate(row[1:], start=1):
                normalized_value = self._normalize_table_text(value)
                if not normalized_value:
                    continue
                column_name = column_names[index - 1] if index - 1 < len(column_names) else f"第{index}列"
                values.append(f"{column_name}={normalized_value}")
            if not values:
                continue
            for key, alias in item_aliases.items():
                if key in item:
                    item = alias
                    break
            facts.append(f"{item}: {'; '.join(values)}")
        return "\n".join(facts)

    def _infer_aql_column_names(self, rows: list[list[str]]) -> list[str]:
        table_text = "".join("".join(row) for row in rows)
        if "平" in table_text and "半圆" in table_text and ("楔" in table_text or "株" in table_text):
            return ["平键", "半圆键", "楔键"]
        return ["平键", "半圆键", "楔键"]

    def _normalize_table_text(self, text: str) -> str:
        text = (text or "").strip()
        text = text.replace("工", "L")
        text = text.replace("心", "d")
        text = text.replace("刍", "")
        text = text.replace("|", "")
        text = re.sub(r"\s+", "", text)
        text = text.replace("1:100斜度", "1:100 斜度")
        return text

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
