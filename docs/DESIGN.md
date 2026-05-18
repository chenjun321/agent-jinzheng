# 总体技术方案

## 1. 背景理解

本作业模拟真实客户交付中的扫描版 PDF 文档问答场景。输入文档是 `GBT 1568-2008 键 技术条件.pdf`，通常没有可靠文本层，包含中文正文、条款编号和表格内容。

系统不能只做一个简单的 PDF 聊天壳，而应覆盖：

- 扫描件识别。
- OCR 解析。
- 条款和表格结构化。
- 检索增强问答。
- 来源引用。
- 答案自检。
- 测试评估。
- 跨业务场景迁移。

## 2. 设计目标

最小可运行目标：

1. 能识别 PDF 是文本 PDF 还是扫描 PDF。
2. 能对扫描页执行 OCR。
3. 能把 OCR 结果组织为页面、段落、条款和表格。
4. 能建立本地可检索知识库。
5. 能对用户问题检索相关证据。
6. 能生成带引用的答案。
7. 能对无依据问题拒答。
8. 能通过脚本复现解析、问答和评估流程。

增强目标：

1. 支持 OpenAI-compatible 大模型生成答案。
2. 支持本地模板回答 fallback。
3. 支持表格优先检索。
4. 支持 OCR 置信度、检索分数和自检结果输出。
5. 支持不同业务场景的配置化迁移。

## 3. 总体架构

```text
Frontend / CLI
  -> Upload PDF / Ask Question
PDF
  -> Document Detector
  -> Parser Strategy
      -> Text Extractor
      -> OCR Extractor
  -> Layout Normalizer
  -> Clause Extractor
  -> Table Extractor
  -> Chunk Builder
  -> SQLite Metadata Store
  -> Milvus Lite Vector Index
  -> Question Planner
  -> Retriever
  -> Answer Generator
  -> Self Checker
  -> Answer + Citations + Risk
```

## 3.1 前端范围

第一版前端保持简单，但必须跑通基础功能：

- 上传 PDF。
- 点击解析文档。
- 展示 PDF 类型、页数、解析状态。
- 展示 OCR 正文片段。
- 展示表格解析结果。
- 输入问题并发起 QA。
- 展示答案、引用页码、证据片段和自检结果。
- 提供默认 5 个问题的快捷按钮。

实现方式：

```text
FastAPI + Jinja2 或静态 HTML + 原生 JavaScript
```

暂不引入 React / Vue，避免前端工程复杂度影响核心 Agent 交付。

## 4. Agent 工作流

Agent 不直接回答问题，而是按节点执行：

```text
QuestionPlanner
  判断问题类型：正文、条款、表格、摘要、无关问题。

Retriever
  根据问题类型选择检索策略，并返回 top-k 证据。

Answerer
  只基于证据生成答案。没有证据时不编造。

SelfChecker
  检查答案是否有引用、引用是否支持结论、是否需要拒答。
```

## 5. RAG 策略

第一版采用 Milvus Lite + 轻量关键词检索的混合方案：

- 阿里百炼 `text-embedding-v4` 生成向量。
- Milvus Lite 保存 chunk 向量和元数据过滤字段。
- 字符级 TF-IDF / BM25 作为 fallback 和补充。
- 支持正文 chunk 和表格 chunk 混合索引。
- 对表格问题提高表格 chunk 权重。
- 对条款编号问题优先匹配条款编号。

后续可扩展：

- Milvus Standalone / Distributed。
- Zilliz Cloud。
- Reranker。
- 多文档检索。
- 权限过滤。

## 6. OCR 与表格策略

扫描件处理流程：

```text
PDF page -> image -> OCR text boxes -> layout grouping -> page text / table candidates
```

OCR 引擎优先级：

1. PaddleOCR：中文效果较好，支持坐标和置信度。
2. Tesseract：作为可选 fallback。

表格第一版策略：

- 结合 OCR 文本框坐标。
- 按 y 坐标聚合行。
- 按 x 坐标聚合列。
- 输出 Markdown 表格和结构化 rows。

增强策略：

- OpenCV 检测横线和竖线。
- 将 OCR 文本框映射到单元格。
- 表格标题识别。
- 表格跨页处理。

## 7. 答案自检策略

自检输出包括：

- `grounded`: 答案是否有证据支持。
- `risk`: `low` / `medium` / `high`。
- `reason`: 风险原因。
- `action`: `answer` / `refuse` / `ask_clarification`。

拒答条件：

- 检索分数低于阈值。
- top-k 证据与问题关键词重合极少。
- 问题明显超出文档范围。
- 答案无法绑定任何页码或片段。

## 8. 本地可复现原则

为避免评审环境无法访问在线模型，第一版必须支持无 API Key 运行：

- OCR、SQLite 落盘、关键词检索和模板答案可独立运行。
- 阿里百炼 Chat 和 Embedding 是默认增强项。
- 所有中间结果落盘，方便检查问题来自 OCR、切块、检索还是生成。

## 8.1 数据存储设计

本地存储分三层：

```text
File System
  保存原始 PDF、页面图片、OCR JSON、表格 JSON、评估报告。

SQLite
  保存文档、页面、表格、chunk 元数据、问答日志、评估运行记录。

Milvus Lite
  保存 chunk embedding，用于向量召回。
```

这样设计的原因：

- Milvus Lite 与 Milvus 服务端 API 接近，方便后续迁移到生产向量库。
- SQLite 零配置，适合本地记录元数据和演示日志。
- 中间 JSON 文件方便人工检查 OCR 和表格问题。

## 8.2 模型服务设计

默认模型服务使用阿里百炼 OpenAI-compatible 接口：

```text
Chat Model: qwen-plus
Embedding Model: text-embedding-v4
Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
```

API Key 只从环境变量读取，不写入代码、文档或 Git。

## 9. 设计取舍

当前阶段优先级：

1. 可运行闭环。
2. 可解释结果。
3. 可评估和可复现。
4. 表格能力做到基本可用。
5. 大模型增强放在后面。

不追求：

- 完整商业级 OCR。
- 复杂前端。
- 大规模向量数据库。
- 所有表格百分百还原。

## 10. 配置化原则

本项目后续按可复用框架演进，核心策略不直接写死在代码里：

- 解析策略放在 `config/default.yaml` 的 `document` 下。
- RAG 多召回、去重、rerank 放在 `rag` 下。
- 模型 token、temperature、模型名放在 `models` 下。
- Agent 拒答阈值、多问题拆解、few-shot 放在 `agent` 下。
- 前端展示开关放在 `ui` 下。

`.env` 只保留 API Key、路径和部署环境相关覆盖项。
