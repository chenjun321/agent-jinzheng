# Agent Jinzheng

面向扫描版 PDF 标准文件的智能文档问答 Agent 原型。

本项目用于完成 `Agent 开发工程师（大模型方向）` 技术笔试作业，目标是围绕扫描版 `GBT 1568-2008 键 技术条件.pdf` 实现一个可运行、可解释、可测试、可迁移的文档理解与检索问答系统。

## 1. 项目目标

系统围绕扫描版 PDF 完成以下闭环：

- 判断 PDF 类型，并选择文本抽取或 OCR 解析策略。
- 提取正文、条款编号、表格信息和页码来源。
- 构建可检索知识库。
- 接收用户问题，检索相关证据。
- 基于证据生成答案，并返回来源页码和片段。
- 对答案做基本自检，判断是否有依据、是否可能幻觉、是否需要拒答。
- 提供测试与评估方法，说明如何迁移到金融、合规、合同、产品手册等场景。

## 2. 当前设计文档

- [总体技术方案](docs/DESIGN.md)
- [配置化设计](docs/CONFIGURATION.md)
- [模块设计](docs/MODULES.md)
- [数据结构设计](docs/DATA_SCHEMA.md)
- [测试与评估方案](docs/EVALUATION.md)
- [演示与提交说明](docs/DEMO_AND_SUBMISSION.md)
- [AI 使用说明](docs/AI_USAGE.md)
- [开发计划](docs/ROADMAP.md)

## 3. 预期项目结构

```text
agent-jinzheng/
  README.md
  docs/
    DESIGN.md
    MODULES.md
    DATA_SCHEMA.md
    EVALUATION.md
    DEMO_AND_SUBMISSION.md
    AI_USAGE.md
    ROADMAP.md

  app/
    main.py
    core/
    document/
    rag/
    agent/
    schemas/

  scripts/
    ingest.py
    ask.py
    eval.py
    run_api.sh

  data/
    input/
    processed/

  demo/
    questions/
    screenshots/

  tests/
```

## 4. 第一版交付边界

第一版优先保证本地闭环能跑通：

- 简单前端页面，支持上传 PDF、解析文档、查看解析内容、发起 QA。
- 前端文档列表，支持删除已处理文档。
- 本地 PDF 解析。
- 扫描 PDF OCR。
- 原始 PDF、OCR 正文、表格、chunk 和解析自检报告落盘。
- Milvus Lite 本地向量索引。
- SQLite 本地元数据和问答记录。
- 删除文档时同步清理 SQLite、Milvus/local vectors 和本地解析目录。
- OCR 后处理清洗，修复中文异常空格、标准号/日期格式、短行断裂和常见 OCR 符号噪声，再抽表、切 chunk 和入库。
- 混合检索、多召回、去重、规则 rerank。
- 多问题拆解、置信度展示、无答案拒答、用户反馈按钮、session 记录。
- 命令行问答。
- 来源引用。
- 无答案拒答。
- 基础评估脚本。

阿里百炼 / 通义千问作为默认大模型服务；同时保留本地 fallback，避免评审环境缺少 API Key 时完全无法运行。

## 5. 快速启动规划

推荐使用 Python 3.11。当前 Milvus Lite 本地版本固定为 `2.5.1`，已在本机验证可运行。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

下载中文 OCR 模型：

```bash
bash scripts/download_tessdata.sh
```

解析、索引和命令行问答：

```bash
python scripts/ingest.py "GBT 1568-2008 键 技术条件.pdf"
python scripts/ask.py "键的技术要求有哪些？"
python scripts/eval.py
```

API 模式：

```bash
bash scripts/run_api.sh
```

访问：

```text
http://127.0.0.1:8000/docs
```

前端页面：

```text
http://127.0.0.1:8000/
```

如果 8000 端口已被占用，可以手动改用 8001：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

## 6. 配置规划

核心策略优先放在 YAML 中：

```text
config/default.yaml
```

`.env` 只放本地密钥、路径和环境相关覆盖项：

```env
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_CHAT_MODEL=qwen-plus
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
DASHSCOPE_EMBEDDING_DIM=1024

SQLITE_DB_PATH=data/processed/app.db
MILVUS_LITE_URI=data/processed/milvus_jinzheng.db
```

说明：

- 配置 `DASHSCOPE_API_KEY` 后，会优先使用阿里百炼 Chat 和 Embedding。
- 未配置 API Key 时，系统使用本地 hash embedding 和模板化答案，仍可完整跑通上传、解析、RAG、QA 和评估。
- RAG 初召回数量、去重阈值、rerank、拒答阈值、模型 token、OCR 参数都在 `config/default.yaml` 里调整。
- OCR 后处理开关和规则参数在 `document.text_cleanup` 中调整；默认会保留原始 OCR 文本到 `pages.json` 的 `original_text`，便于对比清洗效果。

## 7. 重要原则

- 不手工录入 PDF 全文绕过解析过程。
- 不硬编码演示问题答案。
- 所有答案必须尽量绑定证据片段和页码。
- 没有可靠证据时应拒答或提示依据不足。
- OCR、表格识别、检索和生成结果都要可检查、可复现、可回归测试。

## 8. 评审问题回答

### 8.1 是否识别扫描 PDF、表格、OCR、检索、生成、自检等关键问题

能识别。本项目不是把 PDF 直接丢给大模型问答，而是把扫描版标准文件拆成多个工程问题处理：

- 扫描 PDF：先判断 PDF 是否有可靠文本层；如果文本层不足，则走页面渲染和 OCR 流程。
- OCR：保留 OCR 文本、坐标、置信度和页码，便于后续排查是识别问题、切块问题还是检索问题。
- 表格：表格不只作为普通正文处理，会单独抽取为 table chunk，并在表格类问题中提高召回权重。
- 检索：采用向量检索 + 关键词检索 + 条款编号匹配的混合召回，避免单一路径漏召回。
- 生成：答案只基于检索到的证据生成，并返回引用页码和证据片段。
- 自检：对答案是否有依据、引用是否支撑结论、是否需要拒答进行检查，降低幻觉风险。

### 8.2 是否设计合理的 Agent / RAG 流程，而不是简单套壳

是。设计上采用“解析、结构化、索引、规划、检索、生成、自检”的 Agent/RAG 流程：

```text
PDF
  -> 文档类型识别
  -> 文本抽取或 OCR
  -> 正文 / 条款 / 表格结构化
  -> chunk 构建
  -> SQLite 元数据 + Milvus Lite 向量索引
  -> QuestionPlanner 判断问题类型
  -> Retriever 混合检索与 rerank
  -> Answerer 基于证据回答
  -> SelfChecker 判断可信度、引用和拒答
```

这样做的原因是扫描件问答的主要风险不在“能否调用大模型”，而在 OCR 质量、表格还原、证据召回、引用可信度和无答案拒答。Agent 节点拆开后，每一步都有中间结果可检查，也便于替换 OCR、向量库、reranker 或模型服务。

### 8.3 是否设计有效测试，覆盖正文、表格、无答案、模糊问题、OCR 错误和回归风险

是。测试分为自动评估、单元测试和人工检查三层：

- 正文问题：例如“这个标准适用于什么范围？”，验证条款正文是否能被召回并带页码回答。
- 表格问题：例如“表格中键的尺寸或公差是如何规定的？”，验证 table chunk 是否进入索引并被优先召回。
- 无答案问题：例如“这份标准是否规定了汽车发动机维修流程？”，验证系统是否拒答，而不是编造。
- 模糊问题：对宽泛问题先检索多条证据，必要时提示依据不足或要求澄清。
- OCR 错误：检查 OCR 置信度、低质量页面、异常字符比例和关键条款缺失，定位解析问题。
- 回归风险：通过 `scripts/eval.py` 固定评估集，比较每次修改后的引用、拒答、检索分数和 grounded 结果。

对应测试文件规划包括 `tests/test_detector.py`、`tests/test_chunking.py`、`tests/test_retrieval.py` 和 `tests/test_agent.py`。详细方案见 [测试与评估方案](docs/EVALUATION.md)。

### 8.4 方案如何迁移到金融、合规、客户交付等不同业务场景

迁移方式是保留通用 Agent/RAG 框架，替换业务配置、解析规则、测试集和交付模板：

- 金融场景：重点增强表格、数值、口径、日期和单位抽取；对财报、授信材料、尽调报告提高表格 chunk 权重，并对金额和指标做一致性校验。
- 合规场景：重点增强条款编号、制度层级、适用范围、禁止性要求和引用链；回答必须强制带出处，低置信度时拒答。
- 客户交付场景：重点增强多文档管理、权限过滤、客户专属配置、日志留痕和可复现报告；交付时提供解析报告、评估报告和已知限制说明。
- 产品手册 / 标准文档：重点增强章节导航、术语解释、操作步骤和故障问答。

技术上通过 `config/default.yaml` 管理 OCR、chunk、检索、rerank、拒答阈值、模型和 UI 开关；后续可新增 `config/finance.yaml`、`config/compliance.yaml` 等场景配置，而不是重写主流程。

### 8.5 是否善用 AI / Agent 工具提升效率，并能校验 AI 输出、对结果负责

是。AI 工具主要用于方案拆解、代码骨架、测试用例设计、边界情况梳理和文档初稿，提高实现效率。但项目明确不把 AI 输出直接当作最终事实：

- AI 可以辅助生成实现草稿，但需要通过本地运行、测试和评估脚本验证。
- AI 生成的答案必须绑定检索证据和页码，不能脱离文档自由发挥。
- OCR 和表格结果需要落盘，方便人工抽查和问题复盘。
- 无 API Key 时提供本地 fallback，保证评审环境仍可复现核心流程。
- 不提交 API Key，不硬编码演示问题答案，不手工录入 PDF 全文绕过系统能力。

详细记录模板见 [AI 使用说明](docs/AI_USAGE.md)。

### 8.6 是否有清晰的工程交付习惯，包括异常处理、配置、日志、可复现启动和文档说明

有。工程交付按“可运行、可检查、可配置、可回归、可说明”组织：

- 异常处理：PDF 解析失败、OCR 失败、无 API Key、向量检索为空、无答案等情况都应返回明确状态，而不是静默失败。
- 配置：模型、OCR、RAG、Agent 阈值、路径和 UI 开关集中在 `config/default.yaml` 与 `.env`，避免散落硬编码。
- 日志与落盘：原始 PDF、OCR JSON、表格 JSON、chunk、解析报告、问答记录和评估结果都可保存，方便定位问题。
- 可复现启动：README 提供虚拟环境、依赖安装、OCR 模型下载、ingest、ask、eval 和 API 启动命令。
- 文档说明：`docs/` 中拆分总体方案、模块设计、数据结构、配置、评估、AI 使用和提交说明，便于评审和后续维护。
- 本地 fallback：缺少在线模型密钥时仍能跑通解析、索引、关键词检索、模板回答和评估，降低交付环境不确定性。
