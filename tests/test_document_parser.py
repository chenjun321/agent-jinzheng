import json

from app.document.parser import DocumentParser, make_doc_id


class DummySettings:
    values = {
        "document.chunking.merge_cross_page_continuations": True,
        "document.text_cleanup.enabled": True,
        "document.pdf.table_ocr.enabled": True,
        "document.pdf.table_keywords": ["表", "检查项目", "键宽"],
    }

    @property
    def effective_processed_dir(self):
        return "data/processed"

    def cfg(self, path: str, default=None):
        return self.values.get(path, default)


def test_make_doc_id_is_stable_for_same_file(tmp_path):
    pdf = tmp_path / "GBT 1568-2008 键 技术条件.pdf"
    pdf.write_bytes(b"same pdf bytes")

    assert make_doc_id(pdf) == make_doc_id(pdf)


def test_make_doc_id_changes_when_file_content_changes(tmp_path):
    pdf = tmp_path / "GBT 1568-2008 键 技术条件.pdf"
    pdf.write_bytes(b"old pdf bytes")
    old_doc_id = make_doc_id(pdf)

    pdf.write_bytes(b"new pdf bytes")

    assert make_doc_id(pdf) != old_doc_id


def test_merges_clause_continuation_across_pages():
    parser = DocumentParser(store=None, settings=DummySettings())
    chunks = [
        {
            "id": "doc_p1_c1",
            "chunk_type": "clause",
            "title": "4.3 尺寸检查",
            "text": "4.3 尺寸检查\n键的检查项目见表 1",
            "page_start": 1,
            "page_end": 1,
            "metadata_json": json.dumps({"clause_id": "4.3"}),
        },
        {
            "id": "doc_p2_c1",
            "chunk_type": "page",
            "title": "第 2 页片段 1",
            "text": "GB/T 1568 2008\n合格质量水平 AQL\n检查项目",
            "page_start": 2,
            "page_end": 2,
            "metadata_json": json.dumps({"clause_id": ""}),
        },
    ]

    merged = parser._merge_cross_page_chunks(chunks)

    assert len(merged) == 1
    assert merged[0]["page_start"] == 1
    assert merged[0]["page_end"] == 2
    assert "合格质量水平 AQL" in merged[0]["text"]
    assert "GB/T 1568 2008" not in merged[0]["text"]
    assert json.loads(merged[0]["metadata_json"])["cross_page_continuation"] is True


def test_rows_to_facts_uses_header_columns_generically():
    parser = DocumentParser(store=None, settings=DummySettings())
    rows = [
        ["检查项目", "平键", "半圆键", "楔键"],
        ["键宽 b", "1.0", "1.0", "1.5"],
    ]

    assert parser._rows_to_facts(rows) == "键宽 b: 平键=1.0; 半圆键=1.0; 楔键=1.5"


def test_layout_ocr_rows_keep_cells_and_bboxes():
    parser = DocumentParser(store=None, settings=DummySettings())
    lines = parser._blocks_to_layout_lines(
        [
            {"text": "检查", "bbox": [10, 10, 30, 25], "confidence": 0.9},
            {"text": "项目", "bbox": [32, 10, 58, 25], "confidence": 0.9},
            {"text": "平键", "bbox": [140, 10, 170, 25], "confidence": 0.8},
            {"text": "键宽", "bbox": [10, 45, 45, 60], "confidence": 0.85},
            {"text": "1.0", "bbox": [140, 45, 165, 60], "confidence": 0.95},
        ]
    )

    rows, cells = parser._layout_lines_to_rows(lines)

    assert rows == [["检查项目", "平键"], ["键宽", "1.0"]]
    assert cells[0][0]["bbox"] == [10, 10, 58, 25]
    assert cells[1][1]["confidence"] == 0.95


def test_text_keyword_table_marks_extraction_method(tmp_path):
    parser = DocumentParser(store=None, settings=DummySettings())
    table = parser._extract_table_from_text_keywords(
        "doc",
        {
            "page_no": 2,
            "text": "表 1 检查项目\n检查项目  平键\n键宽 b  1.0",
            "ocr_confidence": 0.7,
        },
        tmp_path,
    )

    assert table["extraction_method"] == "text_keywords"
    assert table["rows"][1] == ["检查项目", "平键"]
