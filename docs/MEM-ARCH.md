# MEM-ARCH：记忆与知识架构规范（canonical）

- **状态**：Living spec（随实现演进）
- **版本**：MEM-ARCH（2026-06-06 起）
- **取代**：`MEM-ARCH-20260601`（代码注释引用但从未入库的幽灵文档；其 §编号在本文件中保留以便既往引用解析）
- **权威决策来源**：[ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md)、[DECISION_LOG](DECISION_LOG.md)

> **如实声明**：标记含义 ✅ 已实现 / ⏳ 待办（含依赖外部资源）。截至 2026-06-06，G0-G8 能力层已落地、Hermes venv 下 **222 测试全绿**；D036-D040 已进入实现：分轨检索、报告候选自动入队、L0 Builder、L2→USER.md 单向同步、PDF→候选卡半自动扩容。

---

## 1. 设计公理

1. **两类记忆，反向生命周期**（D027）：医学记忆（少/静态/不可变/零容错）与个人记忆（无界增长/演变/可遗忘）**按策略物理隔离**。
2. **记忆不覆盖事实**（D013）：指标永远由 analytics 计算；检索到的内容只作为**带来源标记的证据**补充背景，绝不改写数值。
3. **医学=已核验真理，个人=提示需验证**：信任模型相反，生成层据此区分呈现。
4. **检索是手段不是目的**：结构化小库直取、长文档才检索；机制（BM25 词法 / 可选向量语义）服从"结构保真 + 可引用"。

## 2. 两类记忆对照（D027）

| 维度 | 医学记忆（authoritative） | 个人记忆（user） |
|---|---|---|
| 体量 | 小、有界（~50 篇→几百卡） | 无界增长 |
| 可变性 | 不可变、版本化（kb_version） | 可变、巩固/衰减/遗忘 |
| 写入者 | 离线策展+临床核验；**Agent 永不写** | Agent 自动写（抽取/巩固） |
| 容错 | 零容错 | 容错（带置信度） |
| 冲突 | 新指南双时间取代旧（两版皆留） | 新证据更新置信度/supersede |
| 检索 | 精确、逐字+页码引用；小库默认 BM25 + tags/synonyms/population（D036） | 模糊、重召回、置信度加权；L1 可随规模启用 hybrid/dense（D036） |
| 证据 kind | `authoritative_kb` | `user_memory` |
| 相撞 | **医学胜**，温和呈现 | 让位医学 |

## 3. 三层记忆模型 Hot / Warm / Cold（D029）

- **Hot（上下文窗口）**：近期血糖+事件、用户画像 L2（全量）、活跃假设 L3（全量）。✅**已实现**：`MemoryContextAssembler.build_memory_context` 对 L2/L3 **SQLite 直取注入、无检索层**（即使查询不匹配也注入）。
- **Warm（合成状态 / "做梦"，D034）**：日/周合成结构化状态摘要并 prefetch 注入。✅**已实现（核心）**：`consolidation.synthesize_state` → `memory_summaries` 表 → `provider.prefetch` 注入。⏳**未接**：cron 日/周触发（= M2 backlog）。
- **Cold（原始档案）**：血糖历史、L1 情景档案、医学卡库。按需检索，不主动注入。权威轨与个人 L1 轨使用**不对称检索策略**（D036）：权威轨默认 BM25-only；个人 L1 在 episode 规模增长后可启用 hybrid。

## 4. 医学记忆：版本化论断卡（D028 / D030 / D031）

### 4.1 离线解析管线（✅ P3b 工程化，D041）
`knowledge/pdfs/*.pdf` → `pdf_loader` 分页文本、表格探测、页面 PNG 渲染 → `HermesClaimExtractor` 通过 `hermes chat -q ... -Q` 抽取文本页，通过 `--image page.png` 抽取表格/图/低文本量页 → `quality` 机器过滤与去重 → review queue → `kb-merge` 以 `verified:false` 合入生产 KB。安全关键卡（阈值/低血糖处置/滴定算法）仍需外部临床/人工核验后才可改为 `verified:true`。

> ✅**已实现（机器）**：[authoritative_kb.json](../src/hermes_cgm_agent/knowledge/authoritative_kb.json) 现为 **6 张双语论断卡**（`schema: claim-cards-v1`），全部 `verified:false`（草稿）；旧的 naive `_extracted/` 与 3 条手写摘要**已删除**。
> ✅**已实现（P3b / D041）**：Hermes 委托抽取流水线可由非医学开发者运行，支持文本页与页面截图多模态抽取；机器过滤通过结构校验、噪声模式、源页文本/表格数字交叉校验降低编造风险。自动合入生产 KB 的卡一律保留 `verified:false`。

### 4.2 论断卡 schema（✅ 已实现，`ClaimCard`）
```jsonc
{
  "card_id": "battelino-2019-tir-adults",
  "title": "TIR targets — most adults with T1D/T2D",
  "claim_zh": "对多数 1/2 型糖尿病成人：TIR(70–180 mg/dL) 目标 >70%……",
  "claim_en": "For most adults with T1D/T2D: TIR (70–180 mg/dL) target >70% …",
  "population": "adult-t1d-t2d-nonpregnant",
  "tags": ["TIR", "targets"],
  "synonyms": ["目标范围", "time in range goal"],
  "source": { "doc": "...", "citation": "Diabetes Care 2019;42(8):1593-1603", "page": 16, "section": "..." },
  "verified": false               // 草稿；临床核验后置 true
}
```
- **双语**（claim_zh/claim_en）+ CJK bigram 分词 → 跨语言召回（D030，已实测）。
- **版本化**：`kb_version`（当前 `kb-2026-06-draft`）;换版用双时间取代（旧卡保留 + 生效日期）。
- **核验闸**：`verified:false` 的卡在检索结果中打 `[待核验/unverified]` 标，生成层不得以权威口径呈现。

### 4.3 安全闸（D031）— ✅ 已实现 [memory_guard.py](../src/hermes_cgm_agent/services/safety/memory_guard.py)
- **轨隔离**：`assert_track_isolation`（已接入 executor `_inject_retrieved_context`）——两轨证据（`authoritative_kb` vs `user_memory`）永不互混，违反即抛错。
- **单向写保护**：`assert_kb_readonly`——医学 KB 无任何写 API（随包 JSON + 内存索引），个人记忆永不能改写它。
- **冲突仲裁**：`resolve_conflict`——医学胜，附温和呈现提示（不否定用户）。

## 5. 个人记忆

### 5.1 存储与检索（repository / retrieval；保留 §5.1 引用）
- 表（[repository.py](../src/hermes_cgm_agent/services/memory/repository.py)）：`l1_episodes` / `l2_profile_items` / `l3_hypotheses` / `memory_candidates` / `memory_summaries`，PHI 字段 Fernet 加密入库。
- ✅**已实现**（D029）：检索仅服务 **Cold（L1 情景档案）**；Hot（L2/L3）直取。`build_default_embedder` 无 opt-in 时返回 `None`（纯 BM25），HashingEmbedder 仅 `CGM_AGENT_USE_HASHING_EMBEDDER` 强制时返回；`tokenize` 加 CJK bigram → 中文召回（D030）。向量语义经 `CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL` 显式开启。
- ✅**已实现**（D032）：L2/L3 增 `valid_from/valid_to`（双时间）+ `source_episode_ids`（lineage）；`supersede_profile_item`（双时间取代、旧窗口关闭不删）+ `list_valid_profile_items`（时点查询/时间旅行）。
- ⏳**执行中**（D039）：L2 active profile items 将以 SQLite 为 source of truth，单向同步到 Hermes `USER.md` 的受管 CGM 段；第一阶段不解析用户手写段落。

### 5.2 巩固与遗忘（consolidation；保留 §5.2 引用）
[consolidation.py](../src/hermes_cgm_agent/services/memory/consolidation.py)：L1 情景 → L2 信念（同型 ≥N 天）→ L3 假设状态机（candidate→observing→stable；矛盾→archived）；遗忘 = L1 90 天归档 / L2 30 天 decay。
- ✅**已实现**（D034 Warm）：`synthesize_state` 从指标 + 记忆合成"用户状态摘要"（如"本周 TIR 72% 环比 +3%；近期晚餐后偏高"）入 `memory_summaries`，prefetch 注入。巩固/衰减/遗忘逻辑保留。

### 5.3 写入侧（生成机制入口）
- **候选 → L1**：`provider.sync_turn`（会话轮）/ `on_memory_write`（内建记忆写）/ G7 报告 `g8_memory_candidates` → `memory_candidates` 队列（`requires_user_confirmation`）→ `memory.confirm` 接受 → `consolidation.ingest_accepted_candidate` 落 L1。D037 要求 `reports.generate` 自动完成报告候选入队，但仍保留确认闸门。
- **L1 → L2/L3**：`consolidation.consolidate`（会话结束等触发）。

## 6. 双轨 RAG（保留 §6 引用，D013）

- 两条物理隔离的证据轨，**永不合并**：用户记忆轨（Hot L2/L3 + Cold L1 → `user_memory`）、权威轨（医学卡库 → `authoritative_kb`）。
- ✅**已实现** [authoritative.py](../src/hermes_cgm_agent/services/rag/authoritative.py)：加载论断卡、**CJK-aware BM25 检索**（取代 FTS5，见 D035）、返回逐字 + 出处 + `verified` 标、跨语言靠双语卡；`AuthoritativeRAGService` 无写 API（随包 JSON + 内存索引，不入用户 DB）。D036 后权威轨默认不加载 dense embedder；工具 `rag.authoritative_search` 契约保持向后兼容并增加可选人群过滤。

## 7.5 L0 工作记忆（D038）

`L0Context` 是短期工作记忆，不是长期记忆。它由确定性 Builder 从最近 14 天 CGM 点、聚合指标、检测事件与确认用户事件组装，执行 `near_point_far_hourly_v1` 压缩策略：

- 近 3 天：保留点级 `high_res_recent`。
- 第 4-7 天：压缩为小时摘要 `mid_far_hourly`。
- 更早窗口：仅保留日聚合 `far_daily_only`。
- 关键事件与用户确认事件始终作为锚点保留。

该对象可由 `context.get_l0` 工具或 `context-build` CLI 生成；LLM 不直接读取原始无界时序。

## 7. 组装与上下文（assembler；保留 §7 引用）

[assembler.py](../src/hermes_cgm_agent/services/memory/assembler.py)：把两轨桥接进 G7 报告的 `memory_context` / `authoritative_context` 槽，**保持双轨可分辨、不互相覆盖**。
- ✅**已实现**：`build_memory_context` Hot（L2/L3）直取、Cold（L1）检索；`build_authoritative_context` 走卡库；executor 注入两轨后调用 `assert_track_isolation`（§4.3）。

## 8. 落地映射（→ ADR-0001 §5 / §7）

| 阶段 | 本规范小节 | 关键文件 | 状态 |
|---|---|---|---|
| P1 文档 | 全文 | docs/ | ✅ |
| P2 简化 | §3 §5.1 §7 | assembler / provider / retrieval | ✅ |
| P3a 医学库机器 | §4 §6 | authoritative.py / authoritative_kb.json | ✅ |
| P3b 解析+核验 | §4.1 | knowledge/ingest + CLI + 临床核验 | ✅ 工程化；verified=true 签核需人工 |
| P4 做梦 | §3 §5.2 | consolidation.py / memory_summaries 表 | ✅ 核心（cron 未接） |
| P5 个人升级 | §5.1 | repository.py / domain | ✅ |
| 安全闸 D031 | §4.3 §7 | memory_guard.py / executor | ✅ |
| D036 分轨检索 | §2 §3 §6 | retrieval / authoritative / assembler | ✅ |
| D037 报告候选入队 | §5.3 | executor / review | ✅ |
| D038 L0 Builder | §7.5 | l0_builder / context.get_l0 | ✅ |
| D039 USER.md 同步 | §5.1 | user_md_sync / provider | ✅ |
| D040 KB 半自动扩容 | §4.1 | knowledge/ingest / eval/rag | ✅ 工程骨架；核验需人工 |
| D041 Hermes 自动建库 | §4.1 | hermes_extractor / pdf_loader / quality / CLI | ✅ 文本+多模态抽取流水线 |
