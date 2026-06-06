# 重构与剪枝计划（工程）

- **日期**：2026-06-06
- **依据**：[ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md)、[DECISION_LOG](DECISION_LOG.md)、[MEM-ARCH](MEM-ARCH.md)
- **目的**：把记忆/知识架构转向落到具体文件的「删 / 改 / 留 / 重构」，并给出开发方向与验证方法。

> **判定原则**：是否与 ADR-0001 新方向冲突。工具契约（registry 13 个 active 工具）**整体稳定**——本次主要是内部实现重构 + 离线管线新增，对 agent 面影响很小。
>
> **已自查纠正**：安全路由 H1（单位）、M1（黄区）在当前代码**已修复**（[router.py](../src/hermes_cgm_agent/services/safety/router.py) 三区齐备、全程 `value_mg_dl`，commit `76bf5a1`），**不在本计划待办内**。

---

## 一、全量分类

### 🟥 删除（DELETE）
| 目标 | 文件/位置 | 原因 | 影响半径 |
|---|---|---|---|
| naive 纯文本抽取产物 | `src/hermes_cgm_agent/knowledge/_extracted/*.json`（10 个） | 孤儿、表格已线性化成乱码、被新解析管线取代 | 零代码引用，安全删 |
| 3 条手写摘要内容 | `src/hermes_cgm_agent/knowledge/authoritative_kb.json` | 手写摘要=幻觉源（D028），由生成的论断卡替换 | `tests/test_rag.py` 硬编码 `tir-consensus` 需改 |
| HashingEmbedder 作为 dense 默认 | `services/memory/retrieval.py` | 非语义词袋伪向量；跨语言改用真语义桥（D030） | 降级 test-only 或删；改 `build_default_embedder` |
| 对 Hot 小库的检索路径 | `services/memory/assembler.py`（活跃假设走 retrieve 的分支） | 个位数行不该检索（D029） | 仅改 assembler |

### 🟧 重构（REFACTOR，逻辑大改、职责保留）
| 模块 | 文件 | 重构内容 |
|---|---|---|
| 权威 RAG | `services/rag/authoritative.py` | 加载论断卡（非 stub）；以 CJK-aware BM25 检索 cards；返回**逐字 + 页码引用**；保留跨语言语义选项。工具输出 schema 不变 |
| 组装层 | `services/memory/assembler.py` | Hot（画像 L2 全量 + 活跃假设 L3 全量）→ **SQL 直取注入**；Cold（L1 档案 + KB）→ 检索；冲突**医学胜**（D031） |
| 巩固→做梦 | `services/memory/consolidation.py` | 在 L1→L2→L3 + 遗忘之上增 **Warm 合成**（日/周状态摘要入 summary 表）（D034） |
| KB 离线接入 | `src/hermes_cgm_agent/knowledge/`（新增 `ingest/`） | 版面感知解析（Docling/VLM）→ 表格结构化 / 流程图转决策规则 → 论断卡 + 临床核验（D028） |

### 🟨 修改（MODIFY，局部增量）
| 模块 | 文件 | 修改内容 |
|---|---|---|
| Provider | `services/memory/provider.py` | `prefetch` 改为 Hot 直取 + Warm 摘要注入；工具 schema/钩子不变 |
| 检索机制 | `services/memory/retrieval.py` | HybridRetriever/BM25/RRF **仅用于 Cold（多条目）**；默认 BM25 sparse-only + 可选双语语义 |
| 领域模型 | `domain/memory.py` | L1/L2/L3 增 bi-temporal `valid_from/valid_to` + 强制 lineage（D032） |
| 仓储 | `services/memory/repository.py` | schema 迁移（bi-temporal 列）+ 新 `summary` 表 + lineage 约束 |
| 存储层 | `storage/sqlite.py` | 无新增 KB 检索表；写保护 guard 落点（D031） |
| 报告渲染 | `services/reports/builder.py` / `renderer.py` | 权威证据按"引用 + 页码"呈现（卡片新增字段） |
| 测试 | `tests/test_rag.py`、`test_memory_integration.py`、`test_hermes_plugin_integration.py` | 适配卡片结构、retrieval 范围、prefetch 直取 |

### 🟩 保留（KEEP，不受转向影响）
- 存储/审计：`storage/sqlite.py`（Fernet 加密层）、`services/audit/`
- 数据管线：`services/data/`（importer + cgm repo）、`services/dexcom/`（M4 原始报文留存=独立 backlog）
- 分析：`services/analytics/`（MAGE/MODD/AGP=独立 backlog，非本转向）
- 安全：`services/safety/router.py`（**已修 H1+M1**）
- 工具/契约：`services/tools/registry.py` + `executor.py`（13 工具契约稳定）
- 记忆评审：`services/memory/review.py`
- 集成：`integrations/hermes/`、`hermes_plugins/installer.py`、`cli.py`
- 输入：`knowledge/pdfs/*.pdf`（新管线输入）

> **关于"砍到 ~800 行"**：**否决把行数当目标**。删 HashingEmbedder/Hot 检索会减码，但新增离线解析管线、卡片 schema、bi-temporal、写保护、Warm 合成会增码。净行数可能不大降——**正确性与可信度 > 行数**（ADR-0001 §4）。

---

## 二、开发方向（分阶段，对齐 ADR-0001 §5）

- **P2 低风险简化**：assembler Hot 直取；retrieval 删 HashingEmbedder 默认、收敛到 Cold；删孤儿 `_extracted/`。
- **P3 医学库重建（核心，最高价值）**：`knowledge/ingest/` 新管线 → 论断卡（双语 + 页码 + kb_version）+ 临床核验 → `authoritative.py` 加载 + CJK-aware BM25 + 逐字引用。
- **P4 Warm 做梦**：`consolidation.py` 增日/周合成 + `summary` 表；`provider.prefetch` 注入。
- **P5 个人记忆升级**：`domain/memory.py` + `repository.py` 加 bi-temporal + lineage。
- **贯穿 安全闸（D031）**：写保护 guard（个人永不写医学）+ 冲突医学胜，落在 `storage`/`assembler`。

**依赖顺序**：P2 独立可先做；P3 需选定解析工具（Docling/VLM）+ 核验流程；P4 依赖 summary 表；P5 含 schema 迁移需谨慎。

---

## 三、验证方法（端到端）

1. **测试套件**：Hermes venv 跑全量 `pytest`；P2 后 retrieval/assembler 相关测试更新且全绿。
2. **KB 正确性（关键）**：构建论断卡后，验证 Battelino TIR **按人群目标表**逐字回流、数值与原表一致、带页码引用（对照 PDF 第 16 页人工核对）。
3. **跨语言**：中文 query（"我的目标范围是多少"）命中英文来源卡片。
4. **Hot 直取**：画像/活跃假设无需检索即注入 prefetch。
5. **写保护**：构造个人记忆写入医学库的调用 → 必须被 guard 拒绝。
6. **冲突仲裁**：个人信念与医学卡冲突 → 输出以医学为准且温和呈现。
7. **回归**：`reports.generate` 在 `examples/cgm_test_dataset/` 合成数据集上仍正常出报告。

---

## 四、2026-06-06 执行归档与下一阶段

本文件保留为第一次架构转向的重构记录。后续执行以 `DECISION_LOG` 的 D036-D040 与 `MEM-ARCH` 当前版本为准：

- **已完成/稳定基线**：Hot L2/L3 SQL 直取、CJK-aware BM25、claim-card 权威 KB 机器形态、Warm summary、bi-temporal L2/L3、安全闸 D031、`kb-validate` CLI。
- **继续执行**：报告候选自动入队（D037）、分轨 Retriever 工厂（D036）、L0 Builder（D038）、L2→USER.md 单向同步（D039）、PDF→候选卡半自动管线（D040）。
- **不再重复执行**：删除 naive `_extracted/`、删除 3 条手写摘要、HashingEmbedder 默认禁用、Hot 小库检索移除等已落地事项。

下一阶段的状态快照见 [STATUS-REPORT-2026-06-06-NEXT-PHASE.md](STATUS-REPORT-2026-06-06-NEXT-PHASE.md)。
