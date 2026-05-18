# 配置化设计

## 1. 设计目标

项目尽量通过配置控制策略，而不是把业务逻辑写死在代码里。

当前配置入口：

```text
config/default.yaml
```

本地环境变量入口：

```text
.env
```

原则：

- `.env` 放密钥和机器相关路径。
- YAML 放解析、RAG、Agent、模型、前端和 session 策略。
- 后续不同业务场景可以新增 `config/finance.yaml`、`config/compliance.yaml` 等配置文件。

## 2. 当前已配置化内容

- 文档解析：文档类型、PDF 页数上限、OCR 引擎、OCR 语言、页面渲染倍率、OCR 后处理清洗、chunk 大小、overlap、表格关键词。
- 模型：Chat model、Embedding model、Embedding 维度、最大输入 token、最大输出 token、temperature。
- RAG：Milvus/local backend、vector top_k、keyword top_k、candidate top_k、final top_k、权重、表格 boost、条款 boost、去重阈值、rerank。
- Agent：最低检索分数、最低置信度、无答案拒答、引用数量、多问题拆解、few-shot 示例、出域关键词。
- UI：OCR、evidence、confidence、删除文档、反馈按钮等展示开关。
- Session：空闲超时和归档策略。

## 3. 场景扩展示例

金融材料：

```yaml
rag:
  table_boost: 0.3
  vector_top_k: 40
agent:
  answer_min_confidence: 0.35
```

合规文档：

```yaml
rag:
  clause_id_boost: 0.3
agent:
  force_citation: true
  refuse_below_confidence: true
```

产品手册：

```yaml
agent:
  multi_question:
    enabled: true
```

OCR 后处理：

```yaml
document:
  text_cleanup:
    enabled: true
    merge_orphan_lines: true
```

默认清洗会修复中文异常空格、标准号和日期中的异常分隔符、短标题断行、孤立引号等常见 OCR 噪声。清洗发生在抽表、切 chunk 和写入向量库之前，同时 `pages.json` 会保留 `original_text` 和 `text_normalization` 统计，方便复核。

## 4. 后续待做

- 文档删除联动 Milvus / SQLite / 文件系统。
- 解析报告 `parse_report.json`。
- 前端用户反馈按钮。
- session 超时归档。
- 多 parser registry。
