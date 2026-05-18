# 开发计划

## 阶段 1：技术文档与项目骨架

目标：

- 明确整体技术路线。
- 建立 README 和设计文档。
- 确定模块边界和数据结构。

状态：

- [x] README。
- [x] 总体技术方案。
- [x] 模块设计。
- [x] 数据结构设计。
- [x] 测试与评估方案。
- [x] 演示与提交说明。
- [x] AI 使用说明。

## 阶段 2：最小可运行解析链路

目标：

- PDF 类型检测。
- 页面渲染。
- OCR 解析。
- 页面结果落盘。

计划产物：

- `scripts/ingest.py`
- `app/document/detector.py`
- `app/document/ocr.py`
- `data/processed/pages.json`

## 阶段 3：知识库构建

目标：

- 条款识别。
- 表格初步提取。
- chunk 构建。
- 本地索引。

计划产物：

- `app/rag/chunking.py`
- `app/rag/index.py`
- `data/processed/chunks.json`
- `data/processed/tables.json`
- `data/processed/index.pkl`

## 阶段 4：Agent 问答闭环

目标：

- 问题分类。
- 检索证据。
- 生成答案。
- 来源引用。
- 自检拒答。

计划产物：

- `scripts/ask.py`
- `app/agent/workflow.py`
- `app/agent/self_check.py`

## 阶段 5：评估与演示

目标：

- 默认 5 个问题。
- 自动评估脚本。
- 截图或演示视频材料。
- README 完整化。

计划产物：

- `scripts/eval.py`
- `demo/questions/default_questions.json`
- `docs/EVALUATION.md` 更新。
- `docs/DEMO_AND_SUBMISSION.md` 更新。

## 阶段 6：可选增强

可选内容：

- FastAPI 服务。
- OpenAI-compatible LLM 接入。
- Embedding 检索。
- Reranker。
- 更强表格识别。
- 多业务场景配置。

