# DECISION_LOG

项目架构决策日志。代码注释以 `Dxxx` 形式引用本日志中的条目。

> **诚实声明（重要）**：基线提交 `0cbb97f` 自述早期决策"recorded out-of-tree in dveps/docs"，**原始决策日志从未入库**。因此 **D012/D013/D018/D025/D026 的内容是 2026-06-06 从引用它们的代码注释中反推重建的**，标记为 `[reconstructed]`，**不保证与原始措辞一致**，仅供让代码引用可解析。**D027 起为本次记忆/知识架构评审产出的权威决策**，以 [ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md) 为准。

---

## 重建条目（[reconstructed]，来源=代码注释）

| ID | 主题 | 重建自 | 重建要点 |
|---|---|---|---|
| D010 | 记忆 provider 与 Hermes SDK 解耦 | `services/memory/provider.py` | provider 以 duck-typing 实现 Hermes memory-provider 契约、不 import Hermes SDK，保持项目可独立测试/替换 |
| D012 | 自建 Hermes 兼容记忆 provider | `services/memory/provider.py` | `CGMMemoryProvider` 实现 Hermes memory-provider 接口（prefetch/sync_turn/tools），不依赖外部记忆框架 |
| D013 | 双轨 RAG + KB 随包发布 | `services/rag/authoritative.py` | 权威知识库作为 package data（`hermes_cgm_agent/knowledge/`）随 wheel 发布，避免 repo-root 路径在安装后失效（C7） |
| D015 | 数值指标只来自 analytics，不来自 LLM | `domain/context.py`, `services/analytics/events.py` | TIR/TBR/GMI 等指标与血糖事件检测由可复现代码计算，绝不由 LLM 生成 |
| D018 | 记忆工具与 provider 协同 | `provider.py` | memory.confirm / memory.correct 工具 schema 单一来源（`MEMORY_TOOL_SCHEMAS`），内层 provider 与 wrapper fallback 共用 |
| D022 | 事件检测确定性 + UserEvent 与检测事件分离 | `domain/cgm.py`, `services/analytics/events.py` | 血糖事件检测基于信号确定性规则（非 LLM）；用户/agent 记录的 `UserEvent` 与系统检测的 `GlucoseEvent` 是不同来源、不混淆 |
| D024 | L0 工作上下文是结构化装配，非长上下文原始投喂 | `domain/context.py`, `domain/memory.py` | L0 是分辨率分层的结构化上下文（近端高分辨率 + 远端聚合 + 关键事件），不是把原始数据塞进长上下文 LLM；L2 为 USER.md 映射快照 |
| D025 | 混合检索 BM25+dense+RRF | `services/memory/retrieval.py` | sparse(BM25)+dense(向量)+RRF(k=60, 按 rank) 融合；语义嵌入可选。该条记录的是旧设计基线，默认路径已由 D029/D035 修正为 sparse-only，HashingEmbedder 仅保留为显式测试/离线强制 |
| D026 | 分级巩固 + 候选评审 | `services/memory/consolidation.py`, `review.py` | L1→L2→L3 阈值门控巩固 + 遗忘；候选先入队评审再接受 |

> 注：D025/D026 的**机制**保留，但其在"医学库"和"小型结构化库"上的**应用方式**被 D028/D029 修正（见下）。

---

## 权威条目（2026-06-06 记忆/知识架构评审）

### D027 — 医学记忆与个人记忆按策略隔离
**决策**：二者为反向生命周期（医学=少/静态/不可变/零容错；个人=无界增长/演变/可遗忘），必须按**策略**（写权限、可变性、信任模型、检索方式）物理隔离，而非仅打标签。
**理由**：见 ADR-0001 §1、§2.1。

### D028 — 医学库 = 结构化解析 + 临床核验论断卡（否决手写摘要、否决裸分片 FTS5）
**决策**：医学库内容来自版面感知解析（Docling/VLM，表格结构化、流程图转决策规则）→ 逐字论断卡（带页码/来源/适用人群/kb_version）→ 安全关键卡人工/临床核验。**不可逆停止"凭模型记忆写摘要"**。**否决**外部报告的"PDF→段落分片→FTS5"裸抽取方案。
**理由**：PDF 多模态实测（ADA-full 45 页→8KB 文本；Battelino 表格线性化成乱码）证明 naive 抽取丢/乱最高价值内容；检索机制(vector vs FTS5)不是医学库的真问题。见 ADR-0001 §2.3、§3。

### D029 — 三层 Hot/Warm/Cold，热数据 SQL 直取（取代 4 层统一检索）
**决策**：Hot（近期血糖/画像/活跃假设）SQL 直取注入、无检索层；Warm 为后台合成状态；Cold 按需检索。删 HashingEmbedder 与小库上的 RRF/reranker。
**理由**：对个位数行的结构化库跑向量检索是过度工程化。见 ADR-0001 §2.2、§4。

### D030 — 保留跨语言语义桥（否决"FTS5 单路全删语义"）
**决策**：论断卡存中英双语 + 保留可选语义嵌入做跨语言召回。
**理由**：中文 query 与英文指南词法不匹配，纯 FTS5/BM25 会漏召回。见 ADR-0001 §2.4。

### D031 — 单向写保护 + 冲突医学胜
**决策**：代码层硬 guard 保证个人记忆永不写入医学记忆；冲突时医学胜，生成层温和呈现。
**理由**：防止两类记忆互相污染；医学作为权威依据不可被个人信念改写。见 ADR-0001 §2.5。

### D032 — 个人记忆升级：双时间有效期 + 溯源 lineage
**决策**：L2/L3 的 supersede 升级为 bi-temporal（valid_from/valid_to）；L2/L3 必回指支撑的 L1 episode。
**理由**：习惯演变需干净取代 + 可审计；杜绝"凭空长出的信念"。见 ADR-0001 §2.6。

### D033 — 文档先行 + 偿还幽灵文档债
**决策**：本批落盘 ADR-0001 / DECISION_LOG / MEM-ARCH；确立"代码引用的设计文档必须在仓内存在"为硬规矩。先对齐文档再改代码。
**理由**：`MEM-ARCH-20260601`/`DECISION_LOG` 被代码引用却不存在，是文档↔代码漂移的根源。见 ADR-0001 §5。

### D035 — 检索机制偏离:CJK-aware BM25 取代 FTS5(同等产出,更少新面)
**决策**:医学卡库与个人 Cold 检索**不新建 SQLite FTS5 表**,改为扩展现有纯 Python BM25 分词器支持中文字符 bigram,复用已测的 `HybridRetriever`。
**理由**:实测 FTS5 对中文别扭(trigram 需 ≥3 字、unicode61 不切连续中文);而本语料只有数十张卡,BM25 足够。此法**同时修好医学库与个人记忆的中文召回(D030)**,且复用已测代码、减少新表/新依赖。产出与 ADR-0001 §4「FTS5 over cards」一致(词法跨语言检索),仅机制不同;ADR 本就声明 FTS5 是「可接受的机制细节」。医学卡库仍作为随包 JSON + 内存索引,不入用户可变 DB(强化 D031 物理隔离)。
**影响**:`retrieval.tokenize` 增 CJK bigram;`authoritative.py` 以 ClaimCard + HybridRetriever 重写;无 storage schema 变更。

### D034 — Warm "做梦"合成为 consolidation 的升级正名
**决策**：把 consolidation 升级为后台日/周从原始数据合成结构化状态摘要，prefetch 注入；对齐 OpenAI Dreaming 模式（事实召回 41.5%→82.8%）。
**理由**：记忆是"可再生派生产物"，合成注入显著提升召回与偏好跟随。见 ADR-0001 §2.2。

### D036 — 双轨 RAG 采用不对称检索策略
**决策**：权威医学轨保持小库 BM25 默认路径（claim card + tags/synonyms/population 路由），个人记忆轨仅对无界增长的 L1 episodes 启用可选 hybrid/dense；L2/L3 继续 Hot SQL 直取。
**理由**：40–50 篇 PDF 经过策展后仍是有界、低增长、需可审计的小型权威库，embedding 基础设施收益低且增加不可解释排序风险；个人 L1 才是高增长、口语化、需要语义召回的库。
**影响**：`services/memory/retrieval.py` 增分轨 retriever 工厂；`services/rag/authoritative.py` 不默认加载 embedder；`services/memory/assembler.py` 按 episode 规模选择个人 L1 检索路径。

### D037 — 报告记忆候选必须自动入队但保留确认闸门
**决策**：`reports.generate` 产出的 `g8_memory_candidates` 在工具执行成功后自动转入 `memory_candidates` 队列；默认仍需用户确认，只有 `requires_user_confirmation=false` 的候选可自动晋升 L1。
**理由**：现有报告能生成候选但不入队，导致 G7→G8 记忆闭环断裂；自动入队不等于自动写长期信念，确认闸门仍保护个人记忆质量。
**影响**：`services/tools/executor.py` 调用 `MemoryReviewService.ingest_report_candidates`；工具 schema 增 `auto_ingest_memory` 控制项。

### D038 — L0 工作记忆必须由确定性 Builder 生成
**决策**：`L0Context` 不再仅作为领域模型存在，新增确定性 Builder 从 SQLite 数据、分析指标和事件检测器组装 14 天压缩窗口；LLM 只消费压缩后的结构化上下文。
**理由**：长上下文不是原始 CGM 数据入口。近点远小时的 L0 策略必须由代码执行，才能保证指标可复算、token 有界、事件锚点不丢。
**影响**：新增 `services/memory/l0_builder.py`、`context.get_l0` 工具和 `context-build` CLI。

### D039 — L2 Profile 以 SQLite 为源同步到 Hermes USER.md
**决策**：L2 active profile items 由本项目 SQLite 作为 source of truth，导出到 `$HERMES_HOME/USER.md` 的受管 CGM 段；第一阶段单向覆盖受管段，不解析或改写用户手写内容。
**理由**：Hermes 原生记忆与 CGM 语义画像需要接通，但双向合并会引入冲突和误删风险。单向受管段先闭合产品体验，后续再评估回读。
**影响**：新增 `services/memory/user_md_sync.py`，在 consolidation / L2 correction / provider session end 后触发导出。

### D040 — 权威 KB 扩容采用候选卡队列 + 人工签核
**决策**：PDF ingest 管线只生成候选 claim cards，不直接写入生产 `authoritative_kb.json`；进入生产 KB 的 verified 卡必须带 reviewer 或 reviewed_at provenance。
**理由**：医学知识库的瓶颈是结构保真和核验，不是向量索引。候选队列允许半自动提取，同时保留人工签核闸门。
**影响**：新增 `knowledge/ingest` 管线、review queue、RAG eval 样本和 `kb-validate` CI 入口。

### D041 — 权威 KB 采用 Hermes 委托抽取与机器校验入库
**决策**：权威 KB 的默认生产路径从人工手写 claim cards 升级为 `PDF → Hermes CLI 结构化抽取 → 机器质量过滤 → review queue → verified=false 合入生产 KB`。Hermes 调用复用本机 `hermes chat -q ... -Q --max-turns 1`；表格、图和低文本量页面允许按页渲染为 PNG 并通过 `--image` 交给 Hermes 多模态模型读取。自动合入的卡必须保持 `verified=false`，`verified=true` 仍需要外部临床/人工签核。
**理由**：用户不是医学专业人员，不应承担手写医学论断或临床判断。现有 PDF 的最高价值内容集中在表格、图和版面结构中，纯 pypdf 文本抽取会丢失或打乱阈值。本阶段把医学内容生成交给 Hermes 离线抽取，并用确定性规则、页码、数字交叉校验和引用硬规则降低幻觉风险。
**影响**：新增 Hermes 抽取器、分页/渲染 loader、质量过滤器、`kb-ingest-llm` / `kb-ingest-batch` / `kb-merge` / `eval-rag` CLI、RAG hit@3 评测与 citation guard。权威轨检索继续保持 BM25-only；不新增 OpenAI/Anthropic SDK，不修改 Hermes 安装树。

### D042 — 权威 KB 修正：tier 检索护栏 + 真 CI 门禁 + Hermes 重抽（修正 D041 落地偏差）
**背景**：2026-06-06 的扩容轮虽然按 D041 实现了 Hermes 抽取能力，但实际 merge 进生产 KB 的 335 张卡是用 `--engine sentence`（确定性句子切割）产出的草稿，**不是** Hermes 抽取结果。审查实测：仅 6 张人工种子卡时 `eval-rag` hit@3 = 100%；merge 329 张 sentence 卡后掉到 84.4%——自动卡（含论文标题、报头/DOI、中文 PDF 乱码 PUA 字形）把人工种子卡挤出 top-3，属净回归。
**决策**：
1. **生产 KB 先回到 6 张人工种子卡（`tier=curated`），sentence 草稿不进生产**；用 Hermes 引擎重抽 priority PDF（CDS/AACE 走 vision），产出 `tier=auto, verified=false` 卡后再 merge（`kb-2026-06-auto-v2`）。
2. **卡片新增 `tier` 字段**（`curated` 人工 / `auto` 机器）。检索 `search()` 改为**可信优先**（`verified or tier==curated` 永远排在 auto 卡之前）+ 未可信卡的查询词重叠下限，使未审核草稿**永不挤出**人工/已核验卡。`tier` 透传到工具与报告输出。
3. **CI 真门禁**：`eval-rag --min-hit3`（默认 0.95）低于阈值即非零退出；workflow 接线。此前 `eval-rag` 永远 `return 0`，不拦截回归。
4. **质量门加固**：拒绝乱码/PUA 字形、报头/DOI/期刊元数据行、页码前缀标题片段；Hermes 抽取卡的 `card_id` 按 `stem+page` 命名空间化，避免逐页调用的 id 冲突被去重静默丢卡。
5. **citation_guard 语义修正**：`assert_authoritative_quotes` 改为对**生成文本**做整数 token 精确匹配（不再子串误配，不再误用在用户 query 上）；查询侧覆盖信号拆为 `query_number_coverage`（仅检索提示）。真正的生成层防幻觉规则写入 `skills/cgm-safety`。
**理由**：医学零容错下，"卡数"不是目标，"可信检索"才是。tier 护栏与种子优先保证人工/已核验内容不被机器草稿稀释；Hermes 抽取（而非句子切割）才能产出原子、真双语、可引的卡（单页验证：`TIR >70%`、`TBR <4%`、`%CV ≤36%` 等阈值与中文翻译均正确）。门禁 + 质量门确保未来任何 merge 不会再悄悄拉低质量。
**影响**：见 `docs/BUILD-REPORT-2026-06-07-kb-correction.md`。架构不变（Claim Card + BM25 双轨 + verified=false 默认 + 人工签核外置）；为运行 vision 重抽，向 Hermes venv 安装 `pymupdf`/`pdfplumber`（ingest 环境依赖，不改 Hermes 安装树代码）。

### D043 — 权威 KB population 受控词表归一 + 过滤器不再 fail-open
**背景**：`rag.authoritative_search` 声明了自由文本 `population` 过滤参数，但 572 张 auto 卡的 `population` 是 ~150 种自由文本（"elderly T2DM with CKD G3a" 等），`_filter_docs` 用精确小写相等匹配，且零命中时 `return filtered or self._docs` **静默返回整库**——一个声明了却不可靠、且静默失效的能力。
**决策**：新增 `normalize_population()` 把自由文本归一到受控类 `{general, pediatric, pregnancy, elderly, inpatient}`（子串启发式，`nonpregnant` 显式排除出 pregnancy）；`ClaimCard.population_class` 派生属性（不改 JSON、无数据迁移）；`_filter_docs` 改按受控类匹配 `{query_class, general}` 并**删除静默 fail-open**；`search()` 输出 `population_class`，工具 payload 输出 `population_filter`；eval harness 透传 `population` 形成端到端回归。
**理由**：过滤是"功能适应性"承诺，必须可靠且行为诚实——零命中应返回真实（含 general 基线）结果集，而非伪装成"过滤成功"的整库。受控类用派生属性实现，避免 578 卡数据迁移与漂移。
**影响**：`services/rag/authoritative.py`、`rag/__init__.py`、`tools/executor.py`、`tools/registry.py`、`eval_hit3.py`、`eval/rag/queries.jsonl`（+1 population 回归 query）；新测试覆盖 normalize 映射（含 nonpregnant 反例）、free-text→class 过滤、不再 fail-open。架构不变。

### D044 — 抗幻觉守卫从注释承诺升级为运行期工具 `rag.verify_quotes`
**背景**：真正的"生成文本里每个医学数字都必须有卡片支撑"守卫 `assert_authoritative_quotes` 已实现且有单测，但**运行期从未被调用**——只有 `query_number_coverage`（检索提示）和 `assert_track_isolation` 接线。`quote_instruction:"verbatim_only"` 仅是提示。因生成发生在 Hermes 主壳，本仓声明的安全保证此前是 aspirational。
**决策**：新增工具 `rag.verify_quotes`（入参 `generated_text`、可选 `documents`/`query`/`strict`），复用 `assert_authoritative_quotes`，落审计，经 cgm 插件暴露给 Hermes；`skills/cgm-safety/SKILL.md` 把"数字映射检查"从"手动应用规则"升级为 **MANDATORY 工具调用契约**（strict 失败不得输出未支撑数字）。把"强制点在生成层"从注释变为可执行、可审计、可测的边界。
**理由**：医学零容错下，安全保证必须是可强制、可验证的运行期能力，而非文档承诺。本仓无法在生成层内部强制（生成在 Hermes），但可提供 Hermes 在交付前必须回调的审计工具，并在 skill 契约里强制其调用。
**影响**：`tools/registry.py`（+`rag.verify_quotes`，tool_count 14→15）、`tools/executor.py`（`_verify_quotes` handler + import）、`skills/cgm-safety/SKILL.md`、`integrations/hermes/cgm/plugin.yaml`（manifest 声明同步，+漂移守卫测试 R2-1）；5 个 executor 级测试。架构不变（双轨隔离/只读 KB 不变）。

### D045 — CLI 与 Hermes 插件统一数据库路径 + 事件技术字段强制（F1）
**背景**：`AppConfig.from_env()` 硬编码 `DEFAULT_DB_PATH`（`.runtime/app.db`），绕过 `resolve_database_path()`；而 `cgm`/`cgm_memory` 插件用 `resolve_database_path(hermes_home)` 解析到 `~/.hermes/cgm-agent/app.db`。结果 CLI 与 Hermes 各读一个库（split-brain），用户在 Hermes 对话中看不到 CLI 导入的数据。另外 `events.create` 让模型自填 `event_id/created_by/user_confirmed`，既易失败又可被模型伪造 provenance。
**决策**：(1) `from_env()` 改走 `resolve_database_path(HERMES_HOME)`，`storage_key_path` 派生自 DB 同目录，密钥与库不同目录时告警；单一真实源优先级保持 `CGM_AGENT_DB_PATH` > `<hermes_home>/cgm-agent/app.db` > `.runtime`（开发回退）。(2) 旧数据迁移用户触发（`migrate-db`，DB+key 一并、拒绝静默覆盖、缺 key 即拒）。(3) `events.create` 展平内联 schema（仅 `event_type`+`ts_start` 必填），executor 在校验前**硬覆盖** `event_id/user_id/created_by=agent/user_confirmed=false`（不可被模型绕过）。(4) 解密失败抛显式错误，不静默返回 None。
**理由**：单一存储是"在 Hermes 里能看到数据"的前置；密钥跟随库保证可解密（零数据丢失）；provenance 强制满足 agent 创建事件必须为未确认候选的不变量。详见 [ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md) 与 spec `specs/001-hermes-runtime-usability/`。
**影响**：`config.py`、`storage/sqlite.py`、`cli.py`、`scripts/migrate_legacy_data.py`(新增)、`services/tools/registry.py`、`services/tools/executor.py`、`integrations/hermes/cgm*`；配套回归测试。架构不变（双轨隔离/只读 KB/PHI 加密 0600 均保持）。

### D046 — F4 陪伴叙事修复四裁决（升级阈值以 SOUL.md 为准 / Push 单独渲染 / /report 工具确定性 / 弱势免责休眠）
**背景**：2026-06-10 对 F4（`specs/003-companion-narrative/`）做 post-implementation 一致性审计，发现 F-1…F-9 + 自审 N1…N12（详见 `specs/003-companion-narrative/remediation-plan.md`）。其中四个互斥决策点 RC1–RC4 经人审拍板。关键事实：现行 spec/data-model/code 的升级阈值**全部偏离权威人设源 SOUL.md**；`render_hypothesis_narrative` 为死代码；push 文案含临床缩写且未校验；聊天态 `/report` 无法在能力层硬拦截。
**决策**：
- **RC1（升级阈值，以 SOUL.md 为唯一真相）**：标准用户 `NORMAL 0-2天 / CONCERN 3-6天 / EXTERNAL_SUPPORT ≥7天`（"一周"）；弱势用户 `NORMAL 0天 / CONCERN 1-4天 / EXTERNAL_SUPPORT ≥5天`（触点 1/3/5）。同步改写 `spec.md` US3 AS2+AS3、`data-model.md` 阈值表、`domain/memory.py:EscalationState.derive`，修正现行 day5 标准 / day3 弱势的偏差。
- **RC2（Push 文案来源）**：为 push **单独渲染** companion 文案（`translate_metric`→生活语言，≤100 字，过 `validate_companion_text` 黑名单），**不改** `synthesize_state`，保护其作为 warm prefetch 摘要（D034）的语义。
- **RC3（/report 边界）**：`reports.generate` 工具内**确定性**直出纯 F3（不经 LLM）；"`/report`→调用该工具"的路由保留在 Hermes `provider.py` 提示词。**重述 FR-011**：聊天路由是 prompt 级（原则 VII：能力层不造聊天命令、不改 Hermes 安装树）。
- **RC4（弱势免责声明）**：保留 `builder.py` 强阻断逻辑但标记**生产休眠 KNOWN GAP**（依赖上游写 `vulnerable_population`，spec 已列 out-of-scope），加夹具注入测试覆盖；不在缺触发器时上线半激活阻断流。
**理由**：原则 IV 将人设绑定 SOUL.md，升级节奏/语气以其为准不可协商；push 是 F4 新交付物，必须同受 companion 黑名单与长度约束；原则 VII 下能力层只能把"确定性"落在工具输出，聊天路由归 Hermes；医疗产品范围克制，无触发器的阻断流不提前半上线。冲突裁决遵循宪法 **Security > Functionality > Aesthetics > Performance > Developer Convenience**。
**影响**：待改 `domain/memory.py`、`services/reports/builder.py`、`services/reports/narrative_templates.py`、`services/scheduling/scheduler.py`、`services/reports/tools.py`、`services/memory/provider.py`、`specs/003-companion-narrative/{spec.md,data-model.md,plan.md,tasks.md}`；执行任务见 `specs/003-companion-narrative/tasks.md`（R000/RC1–RC4/R001–R060）。架构不变（双轨隔离/只读 KB/安全路由/PHI 0600 均保持）。

### D047 — F3 医学安全硬化三裁决（citation 报告闸强制 strict / 恢复窗口内存态 2h / kb.approve 允许清单收紧只读不变量）
**背景**：F3（`specs/002-medical-safety-hardening/`）把三项医学安全行为从软约束升级为代码硬门。落地前经代码对照 `/speckit-analyze`，纠正草案三处偏差：(C1) citation guard 调用入参顺序写反会静默空转；(I1) `assert_kb_readonly` 原是 denylist，**不含** `approve`，直接放行新写法会绕过守卫；(D1) 恢复二次确认草案"对同一份数据再 `evaluate()` 一次"会自递归且无法判定恢复。
**决策**：
- **B1（citation 硬门）**：`assert_authoritative_quotes(documents, generated_text, *, strict=False)` 函数默认仍 `strict=False`（`rag.verify_quotes` + `test_rag` 依赖 warn，D044）；唯一在**报告管线交付前**强制 `strict=True`（`reports/builder.py:_apply_citation_gate`，入参 documents 在前）。守卫仅作用于**外部生成的医学叙事**（`ReportInput.medical_narrative`），绝不覆盖用户自身确定性指标段（TIR/TAR/mean 原则 I 天然干净）。失败即返回"无法确认"persona 文案 `CITATION_BLOCK_TEMPLATE` 并落无泄漏审计（仅计数）。本轮 backing 集 = 检索到的卡（不限 `verified`；verified-only 收紧延后到临床签核，KNOWN GAP）。
- **B2（kb.approve 签核 + 只读不变量收紧）**：`assert_kb_readonly` denylist **新增 `approve`** 使任何写方法默认被拦，并加 `allow_methods` 允许清单——仅 `AuthoritativeRAGService` 显式豁免 `approve`（净收紧原则 I）。`approve(card_id, reviewer, reviewed_at?)` 是唯一被许可写路径：限 `tier=curated`、强制 reviewer provenance、幂等、写回 KB JSON；经 `kb.approve` 工具（严格 JSON 边界校验）+ `cgm_kb_approve` 插件暴露。本轮**零卡自动核验**（无临床审核者，KNOWN GAP）。
- **B3（红区恢复二次确认）**：`SafetyRouter` 由无状态改为持 `_last_red_zone: dict[user→(ts,result)]`（进程内、不持久化、单用户可接受）；公共 `evaluate()` 仅调一次非递归 `_evaluate_zone()`。红区→存档；窗口内（默认 `RECOVERY_WINDOW_SECONDS=7200`，env `CGM_AGENT_RECOVERY_WINDOW_SECONDS` 可覆盖）的后续评估比对**存档原始红区**与**当前结果**，附 `recovery_check` 渲染进报告头；窗口到期清状态。`SafetyDecision.recovery_check` 默认 None，向后兼容。
**理由**：医学零容错下安全保证须可强制、可测、不可绕过（原则 III/V）。函数默认不变避免回归既有工具；强制点收口到报告交付闸符合"硬门在代码不在 prompt"。只读不变量先收紧再允许清单，使任何**未来**新写法默认被拦——比直接放行 `approve` 更安全。恢复比对存档原始态而非重算同数据才能真正判定"是否脱离红区"。冲突裁决遵循宪法 **Security > Functionality > Aesthetics > Performance > Developer Convenience**。安全审计见 `specs/002-medical-safety-hardening/sec-audit.md`（OWASP LLM Top 10，SEC-001…006）。
**影响**：`services/safety/{memory_guard.py,citation_guard.py,router.py}`、`services/rag/{authoritative.py,tools.py}`、`services/tools/{registry.py,executor.py,handlers/rag.py}`、`services/reports/{builder.py,renderer.py}`、`domain/report.py`（+`ReportInput.medical_narrative`）、`integrations/hermes/cgm/plugin.yaml`（+`cgm_kb_approve`，漂移守卫同步）；新增 `tests/test_report_pipeline.py` + `tests/test_kb_approve.py`，扩充 `test_citation_guard/test_safety_router/test_memory_guard`。测试基线 407→440 全绿。架构不变（双轨隔离/只读 KB 收紧/安全路由/PHI 0600 均保持）。详见 `specs/002-medical-safety-hardening/{plan.md,tasks.md}`。

### D048 — F5/D1 push_tick 工具化（`PushSchedulerService` 包成 `scheduling.push_tick`，节奏归 Hermes cron）
**背景**：F5（`specs/004-push-delivery-loop/`）D1：分层推送调度核心（`PushSchedulerService.push_tick`/`decide_due_tiers`/`apply_silent_consent`/`_emit`/`_record_push`）已完整且独立受测，但仅 CLI/内部可调，未注册为工具、未接 Hermes cron——主动推送闭环无法由外部按节奏触发。落地前经代码对照 `/speckit-analyze`：(N1) 工具名应遵循点分 `group.action` 约定（与 `delivery.send`/`data.dexcom_sync` 一致），而非裸名 `push_tick`。
**决策**：
- **工具名 `scheduling.push_tick`**（外部 `cgm_scheduling_push_tick` = `cgm_` + name.replace(".","_")）：group=`scheduling`、status=`active`、risk=`write`、owner=`push_scheduler`，自包含 schema（必填 `user_id`、可选 `now` ISO-8601；无 `$ref`/`$defs`）。
- **新 `PushTickHandlerMixin._push_tick`**：严格校验 `user_id`（非空 string）与 `now`（可选 ISO-8601→datetime），由 `repository.store` + `audit_service` 构造 `PushSchedulerService`，调 `push_tick(user_id, now)`，把 `PushTickResult`（`pushed` + `silent_consent`）落进工具信封并写 `tool_call` 审计。模型只能**触发** tick，**不能**控制调度策略/分层选择/内容生成/静默即认可——这些全留在 `PushSchedulerService` 内。
- **纯追加接线**：`registry.py`（1 ToolSpec）、`executor.py`（1 基类 + 1 `_DISPATCH` 项）、`handlers/__init__.py`（1 import）、`plugin.yaml`（1 `provides_tools` 行）；漂移/分发守卫（ExecutorDispatchCoverageTests、plugin.yaml drift、exact-set）同步覆盖新工具。
- **节奏归 Hermes**：本层不驻留调度进程；由 Hermes cron 按日程（如每日 09:00 Asia/Shanghai）调 `cgm_scheduling_push_tick`。运维侧 cron 注册示例见 README（T019c）。
**理由**：原则 VII 划清边界——开放式交互与调度节奏（cadence）属 Hermes，能力层只把"确定性"（策略/内容/状态）落在工具输出与持久化（`push_events`/hypothesis 状态）。模型零策略面（仅 `user_id`+`now`）符合 LLM07/08 最小代理：模型触发，系统决策。幂等由 `push_events` UNIQUE 兜底 + `decide_due_tiers` 跳过已推周期双保险。冲突裁决遵循宪法 **Security > Functionality > Aesthetics > Performance > Developer Convenience**。
**影响**：新增 `services/tools/handlers/push_tick.py` + `tests/test_push_tick_tool.py`；改 `services/tools/{registry.py,executor.py,handlers/__init__.py}`、`integrations/hermes/cgm/plugin.yaml`；`PushSchedulerService` 不改。测试基线 440→450 全绿（注册/schema/分发/插件漂移 + `execute()` 集成：result shape/幂等/now 覆盖/静默即认可+审计/空窗口稳健）。架构不变（双轨隔离/只读 KB/安全路由/PHI 0600 均保持）。详见 `specs/004-push-delivery-loop/{plan.md,tasks.md}`。

### D049 — F5/D2 webhook 投递闭环（`delivery.send` webhook：env-only endpoint + 硬编码 PHI allowlist + https-only/禁跟随重定向）
**背景**：`delivery.send` 仅 `local_file` 完整，`webhook`/`email` 记为 `queued` 无实际出网。F5/D2 实现 webhook HTTP POST 闭环。落地前经 `/speckit-analyze`：(S1) 出网必须强制 https 且禁止跟随重定向；(U3) PHI allowlist 须严格按 plan.md §"PHI Protection" 显式名单；(U4) v1 manifest 以 `payload_ref` 作 `push_id`、`tier` 取自 arguments。
**决策**：
- **endpoint 仅来自 env `CGM_WEBHOOK_URL`**（调用时读取，FR-011）：模型**不能**经 tool arguments 传入或重定向 endpoint（LLM07/08 防注入）。未设置 → `failed`，零出网。
- **https-only + 禁跟随重定向（S1 安全硬化）**：scheme 非 `https://` → `failed` 无请求（聚合健康指标不走明文）；`_NoRedirectHandler.redirect_request` 恒返 None，使 urllib 对 30x 抛 HTTPError 而非把 POST 转发到 `Location` 主机。三层**确定性**测试覆盖（handler 拒绝跟随 / opener 仅装配 no-redirect handler / 302→failed 单次 POST），不起真实 server（消除线程/端口 flake）。
- **硬编码 PHI allowlist `_filter_webhook_payload`（U3，安全边界）**：deny-by-default，仅放行 `delivery_id`/`push_id`/`tier`/`period_key`/`metrics.{tir_pct,mean_mgdl,gmi}`/`event_summaries[].{type,count}`/`delivered_at`；`user_id`/`content`/`points`/`session_id`/原始序列/凭证一律剥离；嵌套对象降维到允许子集。对**任意** manifest 生效（即使上游误带也被剥）。
- **at-most-once（FR-008）**：单次 POST、10s 超时、不重试（重试归 Hermes/cron）。2xx → `sent`，非 2xx/超时/连接错误 → `failed`。
- **审计无泄漏（C4/FR-010）**：仅记 `delivery_url_domain`（`urlparse` 取域名，非全 URL）、成功记 `http_status_code`、失败记 `error_type`；绝不记全 URL/请求体/响应体/PHI/`payload_ref`。
- **v1 metadata-first（U4/D1）**：manifest = `delivery_id` + `push_id`(=`payload_ref`) + `delivered_at`，`tier`/`period_key`/`metrics`/`event_summaries` 仅在 arguments 已含时携带；`payload_ref→summary→metrics` 解析留作后续，不阻塞 v1。
**理由**：原则 VII PHI 隐私——allowlist 是**代码级**安全边界而非 prompt 约定，deny-by-default 使任何未来字段默认不外泄；endpoint 只来自 env 杜绝模型重定向；https + 禁重定向防明文与跨主机转发泄漏（S1）；at-most-once 把重试复杂度留给 Hermes 层。安全控制测试做成确定性（无真实 socket/线程）以保"全程绿灯"。冲突裁决遵循宪法 **Security > Functionality > Aesthetics > Performance > Developer Convenience**。
**影响**：改 `services/tools/handlers/delivery.py`（新增 `_deliver_webhook` + `_filter_webhook_payload` + `_NoRedirectHandler`/`_build_no_redirect_opener`/`_urlopen_no_redirect`）；新增 `tests/test_webhook_delivery.py`（成功/失败模式/PHI 过滤/审计/https-禁重定向，14 项确定性）。`local_file`/`email` 行为不变。测试 450→464（+webhook 14）全绿；另有仓库既有 `test_hermes_e2e`（httpx/Hermes-venv guard）跳过 1，合计 465。架构不变（双轨隔离/只读 KB/安全路由/PHI 0600 均保持）。详见 `specs/004-push-delivery-loop/{plan.md,tasks.md}`。
