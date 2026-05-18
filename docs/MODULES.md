# 模块设计

## 1. app.core

负责基础工程能力：

- 配置读取。
- 日志初始化。
- 可选 LLM Client。
- 阿里百炼 Chat / Embedding Client。
- 通用异常和工具函数。

计划文件：

```text
app/core/config.py
app/core/logging.py
app/core/llm.py
app/core/embedding.py
```

## 2. app.document

负责 PDF 解析和结构化。

计划文件：

```text
app/document/detector.py
app/document/pdf_loader.py
app/document/ocr.py
app/document/layout.py
app/document/table_extractor.py
app/document/normalizer.py
```

职责：

- 判断 PDF 类型。
- 渲染扫描页。
- 执行 OCR。
- 保存每页 OCR 文本和坐标。
- 识别条款编号。
- 识别表格候选区域。
- 生成页面、条款、表格结构。

## 3. app.rag

负责知识库构建和检索。

计划文件：

```text
app/rag/schema.py
app/rag/chunking.py
app/rag/index.py
app/rag/retriever.py
app/rag/vector_store.py
```

职责：

- 将页面、条款、表格转换为 chunk。
- 保存 chunk 元数据。
- 调用阿里百炼 Embedding。
- 写入 Milvus Lite 本地向量库。
- 维护关键词检索 fallback。
- 根据问题类型执行检索。
- 返回证据片段、页码和分数。

## 3.1 app.storage

负责本地数据持久化。

计划文件：

```text
app/storage/sqlite_store.py
app/storage/file_store.py
```

职责：

- SQLite 保存文档、页面、表格、chunk、问答日志。
- 文件系统保存 OCR JSON、表格 JSON、评估报告和上传 PDF。
- 提供统一读写接口，避免业务模块直接散落写文件。

## 4. app.agent

负责问答 Agent 流程。

计划文件：

```text
app/agent/planner.py
app/agent/workflow.py
app/agent/answerer.py
app/agent/self_check.py
```

职责：

- 判断问题意图。
- 选择检索策略。
- 生成答案。
- 自检和拒答。
- 输出结构化结果。

## 5. app.schemas

负责 API 和内部数据模型。

计划文件：

```text
app/schemas/api.py
```

职责：

- 请求体。
- 响应体。
- 引用结构。
- 自检结构。

## 6. scripts

提供可复现命令行入口。

计划文件：

```text
scripts/ingest.py
scripts/ask.py
scripts/eval.py
scripts/run_api.sh
```

职责：

- 解析 PDF。
- 构建索引。
- 命令行提问。
- 运行评估。
- 启动 API。

## 7. tests

覆盖核心风险点。

计划测试：

- PDF 类型检测。
- OCR 结果结构。
- chunk 页码保留。
- 条款编号识别。
- 表格 chunk 检索。
- 无答案拒答。
- API 返回结构。

## 8. app.web

负责简单前端页面。

计划文件：

```text
app/web/routes.py
app/web/static/app.js
app/web/static/style.css
app/web/templates/index.html
```

页面能力：

- PDF 上传。
- 文档解析。
- OCR 正文展示。
- 表格结果展示。
- QA 输入框。
- 引用和自检展示。
- 默认问题快捷测试。
