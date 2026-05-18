# 测试与评估方案

## 1. 评估目标

测试不只验证接口能跑，还要覆盖扫描 PDF 问答的关键风险：

- PDF 类型是否正确识别。
- OCR 是否产生可检查结果。
- 正文、条款、表格是否能进入知识库。
- 检索结果是否包含正确页码和片段。
- 表格问题是否能优先命中表格。
- 无答案问题是否拒答。
- 答案是否有来源引用。
- 自检结果是否合理。

## 2. 默认评估问题

至少准备 5 个问题：

```json
[
  {
    "id": "q1_scope",
    "question": "这个标准适用于什么范围？",
    "type": "clause",
    "expect_answerable": true
  },
  {
    "id": "q2_requirement",
    "question": "键的技术要求有哪些？",
    "type": "clause",
    "expect_answerable": true
  },
  {
    "id": "q3_table",
    "question": "表格中键的尺寸或公差是如何规定的？",
    "type": "table",
    "expect_answerable": true
  },
  {
    "id": "q4_inspection",
    "question": "文档中对检验或验收有什么要求？",
    "type": "clause",
    "expect_answerable": true
  },
  {
    "id": "q5_no_answer",
    "question": "这份标准是否规定了汽车发动机维修流程？",
    "type": "out_of_scope",
    "expect_answerable": false
  }
]
```

后续会根据 OCR 实际结果校准问题和期望页码。

## 3. 自动评估指标

第一版使用轻量规则指标：

- `has_answer`: 是否生成答案。
- `has_citation`: 是否包含至少一个引用。
- `retrieval_score`: 最高检索分数。
- `expected_refusal`: 无答案问题是否拒答。
- `grounded`: 自检是否认为有依据。
- `page_returned`: 是否返回页码。

评估输出示例：

```text
case_id           type          pass  grounded  citation  action
q1_scope          clause        true  true      true      answer
q2_requirement    clause        true  true      true      answer
q3_table          table         true  true      true      answer
q4_inspection     clause        true  true      true      answer
q5_no_answer      out_of_scope  true  false     false     refuse
```

## 4. 单元测试计划

```text
tests/test_detector.py
  测试 PDF 类型识别。

tests/test_chunking.py
  测试条款、页面、表格 chunk 生成。

tests/test_retrieval.py
  测试检索能返回分数、页码、片段。

tests/test_agent.py
  测试有答案问题和无答案问题的 Agent 响应。
```

## 5. 人工检查清单

演示前人工检查：

- `data/processed/pages.json` 是否包含 OCR 文本。
- `data/processed/tables.json` 是否能看到表格结构。
- `data/processed/chunks.json` 是否包含页码和 chunk 类型。
- 5 个默认问题是否都能返回结构化结果。
- 无答案问题是否拒答。
- README 中是否说明了未完成项和限制。

