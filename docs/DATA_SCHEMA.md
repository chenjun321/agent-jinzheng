# 数据结构设计

## 1. PDF 检测结果

```json
{
  "doc_id": "gbt_1568_2008",
  "file_name": "GBT 1568-2008 键 技术条件.pdf",
  "page_count": 12,
  "pdf_type": "scanned",
  "text_layer_available": false,
  "strategy": "ocr",
  "created_at": "2026-05-18T12:00:00"
}
```

## 1.1 SQLite 表规划

```sql
documents(
  id text primary key,
  file_name text not null,
  file_path text not null,
  pdf_type text not null,
  page_count integer not null,
  parse_status text not null,
  created_at text not null
)

pages(
  id text primary key,
  doc_id text not null,
  page_no integer not null,
  text text,
  ocr_confidence real,
  raw_json_path text
)

tables(
  id text primary key,
  doc_id text not null,
  page_no integer not null,
  title text,
  markdown text,
  html text,
  raw_json_path text,
  confidence real
)

chunks(
  id text primary key,
  doc_id text not null,
  chunk_type text not null,
  title text,
  text text not null,
  page_start integer not null,
  page_end integer not null,
  milvus_pk text,
  metadata_json text
)

qa_logs(
  id text primary key,
  doc_id text not null,
  question text not null,
  answer text not null,
  citations_json text,
  self_check_json text,
  created_at text not null
)
```

## 1.2 Milvus Lite Collection 规划

Collection:

```text
doc_chunks
```

字段：

```text
id: varchar primary key
doc_id: varchar
chunk_id: varchar
chunk_type: varchar
page_start: int
page_end: int
embedding: float_vector, dim=1024
```

Milvus Lite 只负责向量召回；完整文本和复杂元数据仍以 SQLite / JSON 为准。

## 2. 页面 OCR 结果

```json
{
  "doc_id": "gbt_1568_2008",
  "page": 3,
  "width": 1240,
  "height": 1754,
  "text": "5 技术要求 ...",
  "blocks": [
    {
      "block_id": "p3_b001",
      "text": "5 技术要求",
      "bbox": [102, 180, 330, 218],
      "confidence": 0.96
    }
  ]
}
```

## 3. 条款结构

```json
{
  "clause_id": "5.2",
  "title": "5.2 尺寸和公差",
  "text": "键的尺寸和公差应符合...",
  "page_start": 5,
  "page_end": 6,
  "source_blocks": ["p5_b010", "p5_b011", "p6_b002"]
}
```

## 4. 表格结构

```json
{
  "table_id": "p5_t001",
  "doc_id": "gbt_1568_2008",
  "page": 5,
  "title": "表 1 键的尺寸和公差",
  "markdown": "| 项目 | 要求 |\n| --- | --- |\n| ... | ... |",
  "html": "<table><thead><tr><th>项目</th><th>要求</th></tr></thead><tbody>...</tbody></table>",
  "rows": [
    ["项目", "要求"],
    ["...", "..."]
  ],
  "confidence": 0.82,
  "source_blocks": ["p5_b020", "p5_b021"]
}
```

表格结构保留 `rows`、`markdown` 与 `html` 三种表达。`html` 会进入 RAG 证据和 LLM 上下文，尽量保留行列结构；`markdown` 作为兼容和人工预览备份；`rows` 供后续结构化校验、单元格级检索或导出使用。表格 chunk 的 `text` 会包含标题、HTML 表格和 Markdown 备份。

## 5. Chunk 结构

```json
{
  "chunk_id": "gbt_1568_2008_p5_clause_5_2",
  "doc_id": "gbt_1568_2008",
  "type": "clause",
  "title": "5.2 尺寸和公差",
  "text": "键的尺寸和公差应符合...",
  "page_start": 5,
  "page_end": 6,
  "metadata": {
    "clause_id": "5.2",
    "ocr_confidence": 0.91
  }
}
```

## 6. 检索证据

```json
{
  "chunk_id": "gbt_1568_2008_p5_clause_5_2",
  "type": "clause",
  "score": 0.73,
  "page_start": 5,
  "page_end": 6,
  "snippet": "键的尺寸和公差应符合..."
}
```

## 7. 问答响应

```json
{
  "question": "键的尺寸和公差要求是什么？",
  "answer": "根据文档第 5-6 页相关条款，...",
  "citations": [
    {
      "page_start": 5,
      "page_end": 6,
      "chunk_id": "gbt_1568_2008_p5_clause_5_2",
      "snippet": "键的尺寸和公差应符合..."
    }
  ],
  "self_check": {
    "grounded": true,
    "risk": "low",
    "action": "answer",
    "reason": "答案中的关键结论可由引用片段支持"
  }
}
```
