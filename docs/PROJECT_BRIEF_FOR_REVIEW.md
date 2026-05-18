# 项目方阅读说明

仓库地址：https://github.com/chenjun321/agent-jinzheng

本文件用于给项目方快速理解这份作业：我如何解决题目中的关键疑问、这份代码的亮点在哪里、当前取舍是什么、后续还会如何提升。

## 1. 我如何解决题目中的关键疑问

### 1.1 扫描 PDF 怎么处理

题目要求围绕附件 PDF 完成闭环，而扫描版 PDF 的核心问题是：不能假设 PDF 里一定有可直接抽取的文本层。

本项目在 `app/document/parser.py` 中先读取每页文本层，并根据全 PDF 文本字符量和每页最低字符阈值判断 PDF 类型：

- 如果文本层足够，按文本 PDF 处理。
- 如果文本层不足，标记为 `scanned`，走页面渲染 + OCR。

OCR 链路会保存：

- 每页识别后的正文。
- OCR block 坐标。
- OCR 置信度。
- 页面图片。
- 原始 OCR 文本和清洗后文本。

这样后续如果问答结果不准，可以定位问题到底出在 OCR、清洗、切块、检索还是生成，而不是只看到一个黑盒答案。

### 1.2 OCR 错误怎么处理

扫描标准文件常见问题包括中文之间异常空格、标准号格式错乱、日期断裂、短行被错误换行、孤立引号噪声等。

本项目在 `app/document/text_normalizer.py` 中做了 OCR 后处理，并在解析报告中统计清洗行为：

- 修复中文字符之间的异常空格。
- 规范 `GB / T 1568 一 2008` 这类标准号为 `GB/T 1568-2008`。
- 修复 `2008-09-22 发 布` 这类日期和词语断裂。
- 合并短行、孤立行和跨行断裂。
- 去掉 OCR 产生的异常引号噪声。
- 保留原始文本 `original_text`，便于人工对比。

这不是为了“美化文本”，而是为了降低后续条款识别、表格识别、关键词检索和引用片段展示的失败率。

### 1.3 正文、条款编号、表格怎么提取

正文按页保存，同时用条款编号正则识别类似 `1`、`4.3`、`5.1.2` 这样的结构。切块策略是 `clause_first`：

- 能识别条款时，以条款为优先 chunk。
- 不能识别条款时，退化为页面 chunk。
- 对跨页未结束的条款，支持合并后一页延续内容。
- chunk 中保留 `page_start`、`page_end`、`clause_id`、`ocr_confidence`。

表格没有简单当普通文本处理，而是单独生成 table chunk。当前实现包含两条路径：

- 优先基于 OCR block 坐标找表格候选区域。
- 检测表格横线、竖线，推断网格。
- 对表格区域重新 OCR，提升表格区域的识别质量。
- 将表格行列转成 rows、Markdown、HTML 和 facts。
- 如果坐标表格识别失败，则退化为关键词表格候选提取。

表格 chunk 的文本包含 facts + HTML 表格 + Markdown 备份，既方便模型阅读，也方便前端展示和人工检查。

### 1.4 知识库和检索怎么做

本项目不是把 PDF 全文拼给大模型，而是先构建本地知识库：

- SQLite 保存文档、页面、表格、chunk、问答日志、会话和反馈。
- Milvus Lite 保存 chunk embedding。
- 如果 Milvus 或在线 embedding 不可用，会退化到本地向量文件和 hash embedding，保证评审环境仍然能跑。

检索采用混合召回：

- 向量召回：适合语义相近但字面不完全相同的问题。
- 关键词召回：适合标准、条款、尺寸、公差等术语问题。
- 条款编号 boost：问题中出现条款号时提高对应 chunk。
- 表格问题 boost：问题包含“表、尺寸、公差、数值、mm、毫米”等词时提高 table chunk。
- 去重：避免同一页、同一片段重复占满证据位。
- 规则 rerank：当前没有接入独立 rerank 模型，但用标题匹配、OCR 置信度和基础相关性做了轻量重排。

这样做的原因是：扫描件、标准文件和表格问题很容易因为单一路径召回失败。混合召回虽然没有商业 reranker 强，但比单纯 embedding 更稳，也更容易解释。

### 1.5 答案生成和自检怎么做

Agent 流程在 `app/agent/workflow.py` 中拆成几个步骤：

1. 接收问题。
2. 拆解多问题。
3. 调用 RAG 检索证据。
4. 生成前预检：判断是否有证据、分数是否达标、置信度是否达标、问题关键词是否被证据覆盖。
5. 如果证据不足，直接拒答。
6. 如果证据足够，再调用 LLM 或 fallback 模板生成答案。
7. 生成后自检：判断答案是否出现“无法确认、证据不足”等信号。
8. 返回答案、引用页码、证据片段、自检结果和问答日志 ID。

每个答案都会返回结构化字段：

- `answer`
- `citations`
- `evidence`
- `self_check`
- `qa_log_id`
- `session_id`

无答案问题不会硬编。例如问“这份标准是否规定了汽车发动机维修流程？”，如果检索分数和覆盖度不足，系统会返回“当前文档中没有找到足够依据回答该问题。”

### 1.6 测试和评估怎么做

本项目包含三层验证：

- 单元测试：覆盖 doc_id 稳定性、跨页条款合并、表格 facts、表格坐标单元格、OCR 文本清洗。
- 默认评估集：`demo/questions/default_questions.json` 中包含正文、条款、表格、检验要求、无答案问题。
- 评估脚本：`scripts/eval.py` 会运行默认问题，并检查是否符合 `expect_answerable`、是否有 citation、自检 action 是否正确。

重点不是只证明接口能返回 200，而是覆盖题目关心的风险：

- 正文问题能不能召回。
- 表格问题能不能命中 table chunk。
- 无答案问题能不能拒答。
- OCR 错误能不能被清洗和记录。
- 修改解析策略后能不能回归测试。

## 2. 项目核心亮点

### 2.1 它不是简单套壳，而是完整闭环

很多 Demo 会直接把 PDF 文本塞给大模型。本项目按真实交付链路拆成：

```text
PDF 类型判断
  -> OCR / 文本抽取
  -> OCR 清洗
  -> 条款和表格结构化
  -> chunk 构建
  -> SQLite + Milvus Lite 索引
  -> 混合召回
  -> 规则 rerank
  -> 答案生成
  -> 自检拒答
  -> QA 日志和人工反馈
```

每一步都有中间产物，方便复盘、替换和扩展。

### 2.2 离线召回 + 在线人工反馈

当前系统已经把问答日志、session 和 feedback 持久化到 SQLite：

- 用户每次问答都会保存问题、答案、引用、自检结果。
- 前端/API 支持把答案标记为 `resolved` 或 `unresolved`。
- `/api/feedback/unresolved` 可以查看未解决反馈。
- `/api/feedback/summary` 可以查看整体反馈统计。

这形成了一个最小的人机闭环：

- 离线侧：文档解析、chunk、embedding、索引、评估脚本。
- 在线侧：用户提问、返回证据、记录反馈。
- 运营侧：集中查看 unresolved 问题，反向改进 OCR、切块、检索、提示词或测试集。

这个设计比一次性 Demo 更接近真实项目，因为真实客户交付一定会遇到问不准、答不全、表格识别差等问题，必须能收集失败样本。

### 2.3 解析方向上有明确取舍

当前没有追求一口气做“完美 OCR + 完美表格识别”，而是优先做可运行、可检查、可回退的链路。

具体取舍：

- OCR 引擎选 Tesseract + 中文 tessdata，是为了本地可复现、依赖相对轻。
- 表格识别先做 OCR 坐标 + 网格检测 + 区域重识别，而不是一开始接复杂商业 OCR。
- 表格输出同时保留 rows、facts、HTML、Markdown，避免一种格式失败后没有退路。
- Rerank 暂时不用单独模型，先用规则 rerank，降低依赖和成本。
- 没有 API Key 时使用 hash embedding 和模板化答案，确保评审环境也能体验完整流程。

这套取舍的目标是让系统有工程弹性：先把闭环跑通，再替换更强的 OCR、reranker、向量库和 LLM。

### 2.4 对解析结果做规则化，而不是直接信 OCR

OCR 输出天然很脏。本项目解析后做了多层规则化：

- 页面正文规范化。
- 条款编号提取。
- 跨页条款合并。
- 表格单元格合并。
- 表格 facts 归一化。
- AQL 相关表格字段做别名归一。
- 页眉页脚和运行页眉清理。
- parse report 记录低置信度页面、空页面、表格数量、chunk 数量和清洗统计。

这些规则化动作会直接提升 RAG 的稳定性：召回时更容易命中关键术语，生成时更容易引用干净片段，评审时也能看到为什么某个结果可信或需要复核。

### 2.5 自检和拒答是内置能力

系统不是所有问题都回答。自检逻辑至少检查：

- 有没有检索证据。
- 最高检索分数是否达标。
- 证据置信度是否达标。
- 证据是否覆盖问题关键词。
- 问题是否疑似超出文档范围。
- 生成答案是否主动表达证据不足。

这对金融、合规、标准文档尤其重要，因为错误答案比不回答更危险。

### 2.6 配置、异常和可复现启动比较完整

工程上做了这些基础保障：

- `.env.example` 放环境变量模板，不提交 API Key。
- `config/default.yaml` 管理 OCR、chunk、RAG、rerank、Agent 阈值和 UI 开关。
- `scripts/ingest.py`、`scripts/ask.py`、`scripts/eval.py`、`scripts/run_api.sh` 支持命令行复现。
- `README.md` 说明安装、启动、解析、问答、评估。
- `docs/` 下拆分设计、配置、模块、数据结构、评估、AI 使用和演示说明。
- 解析中间结果全部落盘到 `data/processed/{doc_id}/`，方便人工检查。

## 3. 当前已经完成的能力

- PDF 上传、解析、索引、问答的本地闭环。
- 文本 PDF / 扫描 PDF 类型判断。
- 扫描页渲染和 OCR。
- OCR 文本清洗和清洗统计。
- 正文、条款、表格 chunk 构建。
- 表格 rows、facts、HTML、Markdown 多格式输出。
- SQLite 元数据和 QA 日志。
- Milvus Lite 向量索引，本地 fallback 向量文件。
- DashScope / OpenAI-compatible LLM 和 embedding 接入。
- 无 API Key fallback。
- 混合召回、去重、表格 boost、条款 boost、规则 rerank。
- 答案来源引用和自检拒答。
- 前端页面、API、命令行脚本。
- 用户反馈 resolved / unresolved 记录。
- 默认 5 问评估脚本。
- 单元测试覆盖核心解析和清洗风险。

## 4. 当前限制

- OCR 质量依赖本地 Tesseract 和中文模型，复杂扫描件、倾斜页面、低清晰度页面会影响结果。
- 表格识别是规则增强版本，不等价于商业级版面分析。
- 当前 rerank 是规则 rerank，没有接入独立 reranker 模型。
- 多文档、权限过滤、租户隔离还没有做成生产级能力。
- 反馈数据目前只记录和查询，还没有自动进入训练集、评估集或主动学习流程。
- 评估脚本是轻量规则评估，还没有引入大规模 golden set、人工标注和指标看板。

## 5. TODO：后续提升方向

### 5.1 文档上传解析改为 MQ 异步任务

现在上传后会同步解析和索引。后续要改成：

```text
Upload API
  -> 文件落盘 / OSS
  -> 写入 document 记录，状态为 pending
  -> 投递 MQ 消息
  -> Worker 消费解析任务
  -> OCR / 表格 / chunk / index
  -> 更新状态 parsed / failed / needs_review
```

这样可以解决：

- 多个大 PDF 同时上传时接口超时。
- OCR 任务处理不过来导致请求阻塞。
- 文件积压无法观测。
- 失败任务无法重试。

可选技术：

- Redis Queue / Celery：适合第一版轻量异步。
- RabbitMQ：适合业务系统集成。
- Kafka：适合大量文档和事件流。

### 5.2 上传文件和解析产物迁移到 OSS

当前原始 PDF、页面图片、OCR JSON、表格 JSON、chunk JSON 都在本地 `data/processed`。

后续生产化建议：

- 原始 PDF 存 OSS。
- 页面图片存 OSS。
- OCR JSON / tables JSON / parse_report JSON 存 OSS。
- SQLite 升级为 PostgreSQL / MySQL。
- Milvus Lite 升级为 Milvus Standalone / Zilliz Cloud。

这样更适合多机器部署、容器重启、文件备份和客户交付留痕。

### 5.3 接入真正的 rerank 模型

当前已经有规则 rerank：

- 标题匹配 boost。
- OCR 置信度权重。
- 表格问题 boost。
- 条款编号 boost。
- 去重。

但对于语义复杂问题，后续应该接入独立 reranker：

- `bge-reranker`
- DashScope rerank
- Cohere rerank
- 其他中文 rerank 模型

推荐流程：

```text
向量召回 top 20
关键词召回 top 20
合并去重 top 30
reranker 重排
返回 top 5 给答案生成
```

这样能进一步降低“召回到了但排序靠后”的问题。

### 5.4 表格能力单独升级

表格是标准文档、金融报表、合规清单中最容易出错的部分，后续建议把表格当成独立子系统做。

提升方向：

- 表格区域检测单独模型化。
- 用 OpenCV 更稳定地识别横线、竖线和合并单元格。
- 支持跨页表格合并。
- 支持多级表头。
- 将每张表导出为独立文件：
  - `tables/{table_id}.html`
  - `tables/{table_id}.md`
  - `tables/{table_id}.csv`
  - `tables/{table_id}.json`
- 表格 chunk 不只存整表，还生成行级 fact chunk。
- 对数值、单位、公差、AQL、日期做字段级校验。

表格问答可以从普通 RAG 中拆出专门流程：

```text
判断是否表格问题
  -> 优先查 table facts
  -> 再查行级 chunk
  -> 必要时返回原始表格 HTML/CSV 供人工核验
```

这样能明显提升“尺寸、公差、数量、金额、指标”类问题的可信度。

### 5.5 人工反馈进入评估和修复闭环

当前已经能记录 unresolved 反馈。后续要把反馈真正用起来：

- unresolved 问题自动进入待标注池。
- 人工补充正确答案、正确页码、失败原因。
- 将失败样本加入 `demo/questions` 或正式 golden set。
- 每次改 OCR、chunk、检索、rerank 后跑回归。
- 统计失败原因：
  - OCR 错。
  - 表格识别错。
  - chunk 切坏。
  - 检索没召回。
  - rerank 排错。
  - 生成幻觉。
  - 自检阈值不合理。

这会让项目从 Demo 变成可持续优化的系统。

### 5.6 业务场景迁移配置化

后续可以新增场景配置：

- `config/finance.yaml`
- `config/compliance.yaml`
- `config/delivery.yaml`

金融场景重点：

- 表格和数值精度。
- 金额、日期、单位、同比环比、口径一致性。
- 财报、授信、尽调材料的多文档对比。

合规场景重点：

- 条款编号、适用范围、禁止性要求。
- 答案强制引用。
- 低置信度拒答。
- 审计日志和权限控制。

客户交付场景重点：

- 多租户和权限过滤。
- 解析报告、评估报告、已知限制说明。
- 失败任务重试和交付留痕。

## 6. 给代码阅读者的阅读顺序

建议按下面顺序读代码：

1. `README.md`：先跑通安装、解析、问答、评估。
2. `config/default.yaml`：理解 OCR、RAG、Agent 阈值。
3. `app/document/parser.py`：看 PDF 类型判断、OCR、表格、chunk、parse report。
4. `app/document/text_normalizer.py`：看 OCR 文本清洗规则。
5. `app/rag/service.py`：看混合召回、去重、表格 boost、规则 rerank。
6. `app/agent/workflow.py`：看多问题拆解、拒答、自检、日志。
7. `app/storage/sqlite_store.py`：看本地元数据、QA 日志、反馈闭环。
8. `app/main.py`：看 FastAPI 上传、问答、反馈接口。
9. `scripts/eval.py` 和 `tests/`：看怎么验证。

## 7. 演示建议

演示视频或截图建议覆盖：

1. 展示仓库、README 和启动命令。
2. 运行 `python scripts/ingest.py "GBT 1568-2008 键 技术条件.pdf"`。
3. 展示 `data/processed/{doc_id}/pages.txt`、`tables.json`、`parse_report.json`。
4. 启动 `bash scripts/run_api.sh`。
5. 打开前端页面上传或选择文档。
6. 展示正文片段和表格结果。
7. 问至少 5 个问题：
   - 适用范围问题。
   - 技术要求问题。
   - 检验或验收问题。
   - 表格尺寸 / 公差问题。
   - 无答案问题。
8. 展示每个答案的引用页码、证据片段和 self_check。
9. 对一个答案点 unresolved，展示 `/api/feedback/unresolved`。
10. 运行 `python scripts/eval.py` 展示评估结果。

## 8. 一句话总结

这份项目的重点不是“能不能问 PDF”，而是把扫描 PDF 问答拆成可解析、可检索、可引用、可拒答、可反馈、可评估、可迁移的工程闭环。当前版本已经跑通最小闭环，并为后续 MQ 异步化、OSS 存储、reranker、表格子系统和业务场景迁移留下了清晰升级路径。
