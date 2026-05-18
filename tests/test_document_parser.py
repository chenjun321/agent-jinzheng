import json

from app.document.parser import DocumentParser, make_doc_id


class DummySettings:
    values = {
        "document.chunking.merge_cross_page_continuations": True,
        "document.text_cleanup.enabled": True,
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
