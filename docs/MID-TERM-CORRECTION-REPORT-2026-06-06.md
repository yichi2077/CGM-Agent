# CGM-Agent 中期修正报告（记忆与知识架构）

- **日期**：2026-06-06
- **读者**：产品经理（接手 review）、开发组
- **范围**：记忆系统 + 权威知识检索（"双轨 RAG"）的架构修正
- **状态**：可自动化部分已落地并通过全量测试（**196 tests OK**）；一处依赖外部资源的工作（P3b）待安排
- **配套文档**：[ADR-0001 架构决策](adr/ADR-0001-memory-and-knowledge-architecture.md) ｜ [DECISION_LOG](DECISION_LOG.md)（D027–D035）｜ [MEM-ARCH 规范](MEM-ARCH.md) ｜ [重构/剪枝计划](REFACTOR-PLAN-2026-06-06.md) ｜ [给 PM 的状态报告](STATUS-REPORT-2026-06-06.md) ｜ [蓝图差异审计](../AUDIT-2026-06-06-蓝图实现差异审计.md)

---

## 0. 一页速读（给赶时间的人）

- **项目是什么**：以 Hermes 为外壳、面向 CGM（连续血糖监测）用户的 AI 陪伴 Agent；导入血糖+生活数据 → 分析 → 出报告，带长期记忆与权威医学知识支撑。
- **发生了什么偏离**：主链路（数据→分析→报告→记忆）已通、183 测试绿，但作为医学 Agent 的**可信度脊柱——权威知识层是空心的**（线上库只有 3 条**人手写摘要**，10 篇真指南未接入），且**记忆检索过度工程化**（对几行数据也跑向量检索）、设计文档是**幽灵文档**（代码引用却从未入库）。
- **为什么必须改**：医学场景**零容错**。手写摘要 = 让模型凭记忆复述指南 = 幻觉源；裸抽取 PDF 会**打乱/丢失**最高价值内容（阈值表、流程图）。这是从"能用 demo"到"可信产品"的必经一跃。
- **改了什么（边界）**：只改**记忆生成/召回 + 知识接入 + 相关文档**；**不动**数据导入、分析、报告、安全路由、Dexcom、工具契约（13 个工具对外不变）。
- **改成什么样**：三层记忆 **Hot 直取 / Warm 做梦合成 / Cold 词法检索**；医学知识改为**双语论断卡 + 逐字引用 + 核验闸**；两轨**物理隔离 + 单向写保护**；幽灵文档全部补齐。
- **还差什么**：医学卡现全是 `verified:false` 草稿——变成可信权威需 **P3b**（解析全 10 篇 PDF + **临床人工核验**），这是新的人力/工具瓶颈。

---

## 1. 背景（Background）

CGM-Agent 按 G0–G8 能力分层推进，截至修正前主链路打通、**183 个自动化测试通过**，并实现了 SOUL 人格、红/黄/绿三区安全路由、中文受众化报告、Dexcom API v3 接入（含离线 mock 全链路）。从"功能覆盖"看，项目进度良好。

触发本次修正的，是针对"记忆与知识检索"的连续三轮深度审计 + 对一份外部 agent 简化报告的综合评审。审计目标：确认作为**医学 Agent 可信度核心**的"双轨权威 RAG"是否真的可用。

---

## 2. 项目原来的状态（Before）

### 2.1 宣称的设计
- **双轨 RAG**：用户记忆轨（`user_memory`）+ 权威知识轨（`authoritative_kb`），物理隔离、冲突时医学胜。
- **四层记忆**：L0 上下文 / L1 情景 / L2 画像 / L3 假设，混合检索（BM25 + dense + RRF）。
- 权威库随包发布（package data），供 `rag.authoritative_search` 工具检索。

### 2.2 代码实证的真实状态（审计发现）
| 维度 | 宣称 | 实证 |
|---|---|---|
| 权威库内容 | 双轨权威、可引用 | **线上库只有 3 条手写摘要**（220–278 字符/条） |
| 真实指南 | ADA/ISPAD/ESC/Battelino 等 10 篇 | PDF 已抽成纯文本（`_extracted/`，278 页/~103 万字符），但**全仓零引用、未接入** |
| 切分 | "语义拆分" | **零分块**：一篇文档 = 一个检索单元 |
| 多模态 | 图/表/流程图 | **完全没有**：纯文本抽取，表格被线性化、图与流程图丢弃 |
| 向量 | BM25+dense+RRF | dense 默认是 `HashingEmbedder`（SHA1 词袋伪向量，**非语义**）；真语义嵌入默认关闭 |
| 设计文档 | `MEM-ARCH-20260601` / `DECISION_LOG` | 代码引用 10+ 处，但磁盘上**不存在**（幽灵文档） |

---

## 3. 偏离（The Deviation）

把上述实证收敛为四条核心偏离：

1. **医学可信度脊柱空心**：权威库是 3 条手写摘要，真指南未接入。系统对外宣称"权威医学依据"，实际是装饰。
2. **知识接入方式错误且高危**：曾有一次"3→38 条"扩充（git `493e484`），但 **38 条同样是凭模型记忆写的摘要**，提交后 **2 分钟即回滚**（`9ef7ceb`）——团队已隐约意识到此路不通。即便改走"裸抽取 PDF 文本"，也会因多模态丢失而不可信。
3. **记忆检索过度工程化且用错位置**：对个位数的用户画像/假设也跑重型向量检索；真正需要"演变/遗忘"的个人记忆反而缺时间维度。dense 默认是退化的哈希词袋。
4. **文档与代码漂移**：核心设计文档从未入库，团队无法对齐"单一事实源"。

---

## 4. 偏离的原因（Root Causes）

- **横向铺开优先于纵向夯实**：G0–G8 求"功能齐全的 demo"，权威库被放了"占位种子"（3 条 stub，仅为让管线可跑、测试可过——测试里硬编码了 `tir-consensus`）后未回填。
- **低估了医学文档的多模态复杂度**：临床指南的最高价值内容是**表格与流程图**，而非连续散文；纯文本抽取恰恰丢这部分（见 §9 证据）。
- **把"检索机制"误当成"知识库问题"**：以为换检索引擎能解决，实则问题在**解析与结构保真 + 内容策展 + 出处可溯**。
- **设计文档留在了树外**（基线提交自述 "recorded out-of-tree in dveps/docs"），导致引用悬空。

---

## 5. 出于什么理由进行修改（Rationale）

- **医学场景零容错**：一个被抽离上下文的阈值、或被打乱的决策流程图，一旦被当作权威事实呈现，就是安全事故（对应 OWASP LLM09 过度依赖 / LLM06 敏感信息）。手写摘要与裸抽取都不满足"强事实、可追溯"。
- **语料特性决定方法**：仅 ~10–50 篇、静态（指南一年更一次）、高危。这个规模**策展（curation）的性价比远高于堆多模态向量管线**；难点不是"召回规模"，是"精确归因 + 结构保真 + 不张冠李戴"。
- **外部报告的评审结论**：一位外部 agent 建议"砍掉 RAG、改 FTS5 单路、加做梦合成、砍到 ~800 行"。综合评审后**采纳其约 70%**（热数据直取、删退化向量、做梦合成），**否决其约 30%**（"PDF→裸分片→FTS5"重蹈抽取覆辙、"FTS5 单路"破坏中文召回）——因为那 30% 正好踩在医学零容错上。
- **止血优先**：先把决策与幽灵文档落盘对齐，再动代码，避免越改越漂。

---

## 6. 修改的边界（Boundaries / Scope）

### 6.1 范围内（In scope）
记忆系统（生成 + 召回 + 巩固 + 遗忘）、权威知识接入方式、双轨隔离与安全闸、相关设计文档。

### 6.2 范围外 / 刻意保留不动（Out of scope — deliberately KEPT）
- 数据导入 `services/data`、分析 `services/analytics`、报告 `services/reports`、Dexcom `services/dexcom`、审计 `services/audit`。
- 安全路由 `services/safety/router.py`（三区拦截**已在前序提交修好**，H1 单位缺陷 + 黄区均已实现，**不在本次待办**）。
- 存储加密层（Fernet）。
- **工具契约**：`services/tools/registry.py` 13 个 active 工具对外 schema**整体不变**（`rag.authoritative_search` 输出兼容论断卡）。这意味着**对 Hermes/agent 面几乎零影响**，本次主要是内部实现重构 + 离线管线新增。

### 6.3 明确不做（拒绝的方案）
- 不把行数当 KPI（否决"砍到 ~800 行"——删冗余会减码，但新增解析管线/卡 schema/双时间/安全闸/做梦会增码；**正确性 > 行数**）。
- 不全删语义/跨语言能力（中文用户 ↔ 英文指南需要跨语言召回）。
- 不在运行时堆多模态向量管线（对 50 篇属过度投入）。

---

## 7. 修改的内容（What Changed）

> 决策编号见 [DECISION_LOG](DECISION_LOG.md) D027–D035。分阶段 P2–P5 见 [ADR-0001 §5/§7](adr/ADR-0001-memory-and-knowledge-architecture.md)。

### 7.1 删除（DELETE）
- `knowledge/_extracted/`（10 个孤儿裸抽取 JSON，表格已乱、零引用）。
- `HashingEmbedder` 作为 dense **默认**（降级为仅显式强制时使用）。
- assembler 对 Hot 小库（画像/假设）的检索路径。
- 旧 `authoritative_kb.json` 的 3 条手写摘要内容。

### 7.2 重构（REFACTOR）
- **三层记忆 Hot/Warm/Cold**（D029）：Hot（L2 画像 + L3 活跃假设）**SQLite 直取注入、不检索**；Cold（L1 情景）才检索。
- **医学知识 → 双语论断卡**（D028）：`ClaimCard{claim_zh, claim_en, population, source(含页码), verified}`；检索返回**逐字 + 出处**；未核验卡打 `[待核验]` 标、不以权威口径呈现。
- **检索机制**（D035 偏离记录）：以 **CJK-aware BM25** 取代 FTS5——实测 FTS5 对中文别扭，BM25（加中文字符 bigram 分词）同等产出、复用已测代码，并**同时修好医学库与个人记忆的中文召回**（D030）。

### 7.3 修改（MODIFY）
- `retrieval.py`：`build_default_embedder` 无 opt-in 时返回 `None`（纯 sparse）；`tokenize` 加中文 bigram；dense 路径改可选；向量语义经 `CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL` 显式开启（**未删，仅降级为开关**）。
- `provider.py`：`prefetch` 注入 Warm 状态摘要 + Hot 召回。
- `domain/memory.py` + `repository.py` + `storage/sqlite.py`：L2/L3 增 **bi-temporal** `valid_from/valid_to` + **lineage** `source_episode_ids`（D032）；新增 `memory_summaries` 表（D034）；列迁移兼容既有 DB。
- `executor.py`：两轨注入后调用 `assert_track_isolation`（D031）。

### 7.4 新增（ADD）
- `services/safety/memory_guard.py`（D031）：轨隔离断言 + KB 只读断言 + 冲突仲裁（医学胜）。
- `consolidation.synthesize_state`（D034 Warm "做梦"）：从指标 + 记忆合成日/周状态摘要。
- 仓储双时间方法：`supersede_profile_item`（旧信念关闭窗口而非删除）+ `list_valid_profile_items`（时点/时间旅行查询）。
- 6 张从真实指南忠实转写、带页码引用、`verified:false` 的种子论断卡。
- 文档：`docs/`（ADR-0001、DECISION_LOG、MEM-ARCH、REFACTOR-PLAN、STATUS-REPORT、本报告）。
- 测试：`test_memory_guard` / `test_memory_bitemporal` / `test_warm_synthesis` + 既有测试适配。

### 7.5 保留（KEEP）
见 §6.2。另:现有 L1→L2→L3 巩固、L1 90 天归档、L2 30 天衰减逻辑**保留并在其上叠加**新能力。

---

## 8. 修改的理由（逐项 Why）

| 改动 | 理由 |
|---|---|
| 手写摘要 → 论断卡 + 引用 + 核验闸 | 手写摘要是幻觉源；论断卡可逐字引用、可溯源到页码、可临床核验，满足零容错 |
| Hot 直取、删退化向量 | 对个位数结构化行跑向量检索是过度工程；直取更准更省 |
| 双语卡 + CJK BM25 | 中文 query 命中英文指南（"低血糖"≠"hypoglycemia"），纯英文词法会漏召回 |
| bi-temporal + lineage | 个人习惯会变（"以前不吃早餐"→"现在吃"），需干净取代 + 可审计 + 杜绝"凭空信念" |
| Warm 做梦 | 记忆是"可再生派生产物"；合成注入显著提升召回（业界 OpenAI 数据 ~41%→~83%） |
| 单向写保护 + 冲突医学胜 | 防止个人记忆污染医学权威；医学作为依据不可被个人信念改写 |
| 文档先行 + 补幽灵文档 | 消灭"代码指向不存在的事实源"，让团队对齐 |
| FTS5 → BM25（偏离） | FTS5 对中文分词友好度差；BM25 同等产出且复用已测代码（记录于 D035） |

---

## 9. 关键证据（供 review 复核）

- **多模态实测**（PyMuPDF 逐页统计）：ESC 2023 = 98 页 / 191 图 / 5116 矢量绘图 / 85 表标题；ISPAD full-3 = 41 页 / **11263 矢量绘图**；**ADA-full = 45 页仅抽出 8KB 文本**（内容几乎全在图里）。
- **表格被打乱**：Battelino 2019 第 16 页"TIR%→A1C 目标"对照表，纯文本抽取后数字串行成 `70% / 7.0(53) / (5.6,8.3) / 70% / 6.7(50) …`，**列对齐丢失**，LLM 解读出的任何关系都是臆造。
- **38 条手写摘要被回滚**：git `493e484`（11:55 提交）→ `9ef7ceb`（11:57 回滚），印证手写路线不可行。
- **跨语言已验证**：中文 query "目标范围内时间" 成功命中英文来源的 TIR 卡；"低血糖怎么处理" 命中英文低血糖卡。
- **测试**：基线 183 → **196 全绿**（净增 13，覆盖新契约/跨语言/安全闸/双时间/Warm/CLI 入口）。

---

## 10. 改动后的现状（After / Current State）

### 10.1 记忆系统
- **生成**：候选（对话轮 / 内建写 / 报告 g8 候选）→ `memory_candidates` 队列（需确认）→ `memory.confirm` → L1；巩固 L1→L2→L3（带双时间 + 溯源）；Warm `synthesize_state` 产状态摘要；遗忘（归档/衰减）。
- **召回**：Hot（L2/L3）直取 + Cold（L1）BM25 + Warm 摘要 prefetch 注入。

### 10.2 医学知识
- 6 张双语论断卡（`kb-2026-06-draft`），全部 `verified:false`；CJK-aware BM25 跨语言检索；逐字 + 页码引用；KB 只读、与个人记忆物理隔离。

### 10.3 安全
- `memory_guard` 轨隔离已接入 executor；冲突医学胜；向量语义为可选开关（默认词法）。

### 10.4 文档
- 幽灵文档已补齐（ADR / DECISION_LOG / MEM-ARCH）；MEM-ARCH 已刷新为准确的"✅已实现 / ⏳待办"；AGENTS.md 增加指向 `docs/` 的指针，形成"代码引用必可解析"的闭环。

---

## 11. 遗留与待办（Pending）

| 项 | 状态 | 依赖 |
|---|---|---|
| **P3b**：Docling/VLM 解析全 10 篇 PDF + **临床人工核验**（`verified:false→true`） | ⏳ 待资源 | 工具选型 + 临床/领域核验人力 |
| P4 调度：cron 日/周触发 Warm 合成与推送 | ⏳ 未接 | = M2 backlog（合成引擎已就绪，缺触发线） |
| 流程图 → 结构化决策规则 | ⏳ P3b 内 | VLM + 人核 |
| 分析侧增 MAGE/MODD、AGP 可视化 | 独立 backlog | 与本次修正正交 |

---

## 12. 风险与缓解（Risks）

| 风险 | 等级 | 缓解 |
|---|---|---|
| 草稿医学卡内容有误被当权威 | 高 | 全部 `verified:false` + 检索打"待核验"标 + 生成层不以权威呈现；P3b 临床核验 |
| 解析+核验人力密集 | 中 | "AI 出草稿 + 人核高危卡 + 长尾自动兜底"分摊 |
| 个人记忆历史数据 schema 迁移 | 中 | `_ensure_column` 兼容旧库 + 回归测试覆盖；valid_from 缺省回退 created_at |
| 短期对外新功能放缓 | 中（产品） | 沟通口径：夯实可信内核而非停滞，核心价值（可信+可溯源）显著提升 |

---

## 13. 给 review 者的决策点（Action Items）

1. **PM**：① 批准 P3b 的工具选型与**临床核验人力/流程**；② 确认"对外功能放缓"的沟通口径；③ 确认 P2→P3b 优先级。
2. **开发组**：① 复核 6 张种子卡的医学内容；② 确认 **D035（FTS5→BM25）** 偏离是否接受；③ review `memory_guard` 与双时间迁移的实现。
4. **共同**：是否现在提交 git（含如何处置 `AGENTS.md` 的外部杂项行与未跟踪的 `pdfs/`）。

---

## 附录 A — 改动文件清单

**新增**：`services/safety/memory_guard.py`；`tests/test_memory_guard.py`、`test_memory_bitemporal.py`、`test_warm_synthesis.py`；`docs/`（6 份）。
**修改**：`domain/memory.py`、`domain/__init__.py`、`knowledge/authoritative_kb.json`、`services/memory/{assembler,consolidation,provider,repository,retrieval}.py`、`services/rag/{__init__,authoritative}.py`、`services/safety/__init__.py`、`services/tools/executor.py`、`storage/sqlite.py`、`tests/{test_memory_integration,test_memory_retrieval,test_rag}.py`、`AGENTS.md`。
**删除**：`knowledge/_extracted/`（10 文件）。
规模：核心源码 +698 / −135 行（不含新增文档与测试）。

## 附录 B — 决策映射

D027 两类记忆反向治理 · D028 论断卡（否决手写/裸分片）· D029 三层 Hot/Warm/Cold · D030 跨语言双语桥 · D031 单向写保护+冲突医学胜 · D032 bi-temporal+lineage · D033 文档先行 · D034 Warm 做梦 · D035 FTS5→BM25 偏离。

## 附录 C — 如何验证

```bash
cd /Users/yichizhang/code/CGM-Agent
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests   # 期望 196 OK
```
KB 跨语言冒烟：实例化 `AuthoritativeRAGService`，分别用 `time in range target` 与 `目标范围内时间` 检索，应命中 TIR 卡且 `verified=False`、带 `source` 引用。
