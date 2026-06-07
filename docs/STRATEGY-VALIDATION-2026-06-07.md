# CGM-Agent 战略方向验证报告

> **日期**：2026-06-07  
> **范围**：外部行业对标（2025–2026）× 内部实现审计 × 交叉裁决  
> **方法**：Web 调研 + 只读审阅 `AUDIT-2026-06-07-IMPLEMENTATION-REVIEW.md`、STATUS、DECISION_LOG、MEM-ARCH、ADR-0001、R1–R6 闭环报告及关键实现；本地取证 `dev-status` + 337 tests OK  
> **约束**：未修改源码；本文档为唯一新增产出

---

## 1. 执行裁决（Executive Verdict）

### 总体判断：**部分就绪、方向正确，无需架构 pivot**

| 维度 | 裁决 | 置信度 |
|------|------|--------|
| 架构选择（Hermes 壳 + CGM 能力层 + 双轨 RAG + 分层记忆） | ✅ **与 2025–2026 行业最佳实践高度同构** | 高 |
| 开发优先级（记忆/RAG 闭环先于交付面） | ✅ **基本正确**；但 P3 onboarding/空库体验应提前到 4–8 周窗口前半段 | 中高 |
| 产品蓝图一期 MVP | 🟡 **能力层 ~70%，交付面 ~40%** | 高 |
| 生产/灰度就绪 | 🟡 **工程 7/10，产品门槛 5.5/10** | 高 |

**一句话**：项目已从「能力层 spike」进入「分层推送产品闭环已实现」的工程态；**不应 pivot 架构或主壳选择**。主要风险不在模块缺失，而在 **默认空库体验**、**KB 临床签核（578 卡全 `verified=false`）**、**外部投递面未闭环**，以及 **`rag.verify_quotes` 依赖 SKILL 契约而非生成层硬强制**。

**与 PM 蓝图关系**：微泰/Weitai 生态正走向「设备 + 算法 + 云平台 + 大模型」（混合闭环、AGP、AI 问答），本项目选择 **Hermes 个人 agent 壳 + 本地 CGM 能力层**，在 ToC 个人 companion 与 ToB 检棠/人工胰腺之间取了一个 **可自托管、可审计、非诊断** 的中间路线——与蓝图「Agent 非聊天框主形态」「分层推送」「医生报告」方向一致，但 **微信/App 卡片、AGP 可视化、PDF** 仍缺。

---

## 2. 外部行业 landscape 摘要（含来源）

### 2.1 产品层：CGM AI 助手与糖尿病 coaching

| 产品/方向 | 核心模式 | 与 CGM-Agent 相关性 | 来源 |
|-----------|----------|---------------------|------|
| **Open-D** | 24/7 AI agent、 proactive alerts、人格化 coaching、本地隐私、Libre/Dexcom | 最接近「Agent 非聊天框」愿景；强调 whole-day context + 记忆 | [open-d.app](https://open-d.app/) |
| **SNAQ** | 照片餐食 + CGM 对齐 + 对话式 AI Nutritionist；RAG over 用户餐食/CGM + 通用营养指南 | 双源 RAG（个人数据 + 指南）的商业验证；Lancet/eClinicalMedicine 2025 RCT | [snaq.ai](https://www.snaq.ai/) |
| **Aegle** | CGM 模式 → 一小步行动 → 次日验证；明确非医疗替代 | 「小步闭环」与 tiered push 同构；安全免责声明范本 | [aegle.ai](https://aegle.ai/) |
| **Manna** | 2 周 CGM 训练 digital twin → 脱传感器持续 coaching；临床文献 grounding | 「Warm 合成状态」+ 权威文献映射的产品化先例 | [mannahealth.ai](https://mannahealth.ai/) |
| **微泰 Weitai** | CGM + 贴敷泵 + 云平台；混合闭环 MPC；DeepSeek 等大模型 → 饮食/运动/用药指导、AI 医学顾问 | **生态位不同**：厂商走 closed-loop 与治疗设备；本项目可走 **开放 agent + 微泰数据接入** 互补 | [新浪财经 2025-02](https://finance.sina.com.cn/jjxw/2025-02-25/doc-inemswwe8906246.shtml)、[微泰官网](https://microtechmd.com/cn/about/news/1957) |

**行业共识（2025–2026）**：
- 领先产品从「图表 App」转向 **proactive agent + 模式记忆 + 分层触达**。
- **非诊断 coaching** 必须显式 disclaimer + 避免剂量/处方建议（Open-D、Aegle、Manna 均如此）。
- 微泰官方路线偏 **设备闭环 + 医生端系统（检棠）**；个人 AI companion 仍是 **差异化空白**，但需避免与 SaMD/人工胰腺监管路径混淆。

**不确定性**：Open-D 等产品多为 waitlist/区域 rollout，公开技术架构细节有限；对标时以 **产品行为** 为主，非实现细节。

### 2.2 技术层：健康 LLM agent 架构

| 主题 | 行业趋势 | CGM-Agent 对齐度 |
|------|----------|------------------|
| **Dual-memory / 双轨知识** | ClinicalAgents：Working Memory（可变患者状态）+ Experience Memory（静态指南）；OpenClaw×Hospital：page-indexed longitudinal memory | ✅ Hot SQL + Warm 合成 + Cold 检索；权威 vs 个人物理隔离（D027/D031） |
| **Tool use + 本地计算** | CGM-Agent 论文（arXiv 2604.17133）：LLM 仅选 analytical functions，数值本地算，PHI 不出设备 | ✅ D015 指标只来自 analytics；工具边界 registry |
| **RAG 抗幻觉** | VeReaFine/LINS/MEGA-RAG：检索→验证→迭代 refine；临床 QA citation precision 70–96% | ✅ claim card + BM25 + `rag.verify_quotes` + tier 护栏；⚠️ 生成层强制仍靠 SKILL |
| **Agent shell vs 自建 chat** | Hermes（2026）：自改进 loop、分层记忆、cron、多通道 gateway；OpenClaw：gateway-first、更广渠道 | ✅ AGENTS.md 明确 Hermes 主壳；与 innFactory/TuringPost 2026 对比结论一致 |
| **Bitemporal memory** | Memento/Membread：valid time vs transaction time；`as_of` 查询 | ✅ L2/L3 `valid_from/valid_to` + supersede（D032） |
| **USER.md 同步** | Memento 等将 markdown 作为 session 蒸馏投影；非双向 merge | ✅ D039 单向受管段；SQLite 为 source of truth |

**来源**：[ClinicalAgents arXiv:2603.26182](https://arxiv.org/abs/2603.26182v1)、[OpenClaw×Hospital PDF](https://arxiv.org/pdf/2603.11721)、[CGM-Agent QA arXiv:2604.17133](https://arxiv.org/html/2604.17133v1)、[VeReaFine ACL 2025](https://aclanthology.org/2025.bionlp-share.34.pdf)、[Hermes Agent](https://hermes-agent.org/)、[Memento](https://github.com/shane-farkas/memento-memory)

### 2.3 类似开源项目

| 项目 | 特点 | 差异 |
|------|------|------|
| **GlycemicGPT** | 自托管 Docker、BYOAI、Nightscout、AGP 报告、tiered alerts | 更完整的 **交付面**（移动 App、PDF）；CGM-Agent 记忆/RAG 架构更深 |
| **nightscout-cgm-skill** | Agent Skill 标准、AGP HTML、本地隐私 | 轻量 skill，无 L0–L3 巩固 |
| **Agentic-RAG-Diabetes-Assistant** | LangGraph + ChromaDB 学术 demo | 无 CGM 时序、无双轨隔离 |
| **yanjunCC/cgm-agent** | 隐私 preserving QA benchmark | 验证「工具选函数、本地算数」范式 |

### 2.4 分层推送 / 静默 consent

- **UX 研究**：健康 App 通知需 **granular opt-in**、低频默认、安全 alert 与 coaching 分 lane（[ResearchGate 2023](https://www.researchgate.net/publication/370017873_Notifying_Users_Customisation_Preferences_for_Notifications_in_Health_and_Well-being_Applications)）。
- **临床 CDS**：severity tier — critical 需 acknowledge，informational 进独立 tray（[Momentum healthcare UI](https://www.themomentum.ai/blog/healthcare-app-design-ui-patterns-design-systems)）。
- **GlycemicGPT**：info/warning/urgent/emergency 四级 + 用户偏好持久化（[commit 8a0463d](https://github.com/GlycemicGPT/GlycemicGPT/commit/8a0463d6a7c8cf4f931788f216da25fdf8ece2eb)）。
- **CGM-Agent**：`PushSchedulerService` daily/weekly/monthly + `apply_silent_consent` **刻意收窄**为 candidate→observing，不 auto-accept 记忆——比「静默即认可一切」更保守，符合伦理文献对 **informed consent** 的要求（[IRJENET 2024](https://irjernet.com/index.php/fecsit/article/view/172/156)）。

### 2.5 医生报告 / AGP / LLM 摘要

- AGP 为 IDC 标准一页报告；Dexcom CLARITY / LibreView 等已内置（[Nature Sci Reports 2025](https://www.nature.com/articles/s41598-024-84003-0)）。
- GPT-4 对 14 天 CGM **定量指标 9/10 完美**、叙事 safety 9.5–10/10，但 **阈值 prompt 歧义** 可导致 TAR 计算错误——支持「代码算数、LLM 叙事」分工。
- 2025–2026 检索 grounding 的 CGM counseling CA 研究强调 **plain-language、非 directive**（[arXiv:2604.15124](https://arxiv.org/pdf/2604.15124)）。

### 2.6 监管与安全（非诊断 coaching）

- FDA DSF/MMA guidance：**coaching/prompting 辅助自我管理、不提供具体 treatment suggestions** → 倾向 **enforcement discretion**（[FDA Step 7](https://www.fda.gov/medical-devices/digital-health-center-excellence/step-7-does-device-software-functions-dsf-and-mobile-medical-applications-mma-guidance-apply)）。
- 2026 General Wellness guidance：非侵入、非诊断声明、低风险的 wellness 产品（[FDA 2026](https://www.fda.gov/media/100032/download?attachment=)、[Troutman 解读](https://www.troutman.com/insights/fdas-2026-guidance-on-general-wellness-devices-policy-for-low-risk-devices/)）。
- **红线**：胰岛素剂量建议、诊断、替代 CGM 报警 → 易落入 SaMD。**CGM-Agent 的 SafetyRouter 红区 + 不做开放式医疗 QA** 与监管友好路径一致。

### 2.7 常见 failure modes（行业）

| Failure mode | CGM-Agent 缓解 | 残留 |
|--------------|----------------|------|
| LLM 编造临床数字 | analytics 硬算 + verify_quotes | SKILL 未加载时无硬强制 |
| 裸 PDF 分片 RAG | claim card + 版面感知 ingest（D028/D041） | 578 卡未临床 verified |
| 个人/医学记忆污染 | assert_track_isolation + KB readonly | — |
| 通知 fatigue | tiered push + 幂等 period_key | 无 UI 偏好层 |
| 空数据 hallucination | 空窗不产 G8 候选 | **默认 DB 0 points** |
| 自建 chat 引擎重复造轮 | Hermes 主壳 | 依赖 Hermes 演进节奏 |

---

## 3. 内部项目快照

### 3.1 取证基线（2026-06-07 实测）

```text
dev-status: 15 active tools, push_scheduler_present=true, dual_track_rag_present=true
glucose_point_count: 0, report_count: 0
eval-rag: hit@3 = 1.0 (44/44), kb-2026-06-auto-v2
tests: 337 OK (~2.1s)
hermes: v0.15.1, cgm plugin enabled
```

### 3.2 愿景 → 实现 → 缺口映射

| PM 蓝图主题 | 实现状态 | 缺口 |
|-------------|----------|------|
| Agent 非聊天框 / 分层推送 | ✅ `push-tick` + scheduler + silent consent | CLI-only；无微信/App 卡片 |
| L0–L3 + USER.md | ✅ Builder + consolidation + 受管段 sync | P1-1 `occurred_at=now` 历史坍缩 |
| 双轨 RAG | ✅ 578 卡 BM25 + tier + population 受控类 | 全 `verified=false` |
| 绿/黄/红安全路由 | ✅ SafetyRouter + 红区零泄漏 | verify_quotes 非 Hermes hook |
| 日报/周报/月报 | ✅ synthesize_state + reports.generate | 无投递自动 `delivery.send` |
| 医生报告 | 🟡 clinician audience Markdown | 无 AGP 百分位、无 PDF |
| 餐食/运动写回 | 🟡 events.create/confirm | 无拍照/语音管线 |
| 记忆用户控制 | ✅ list/delete/correct/confirm + candidates | — |
| Dexcom live | 🟡 代码完整 | 阶段决策 mock/CSV only |

### 3.3 架构亮点（审计 Wins 摘要）

1. G0–G8 能力层结构性完成；15 工具全 active。
2. 持续审计 R1–R6：7 项实改 + 永久守卫（plugin 漂移、population fail-open、phantom doc）。
3. 记忆/RAG 产品闭环可测：`seed-demo` → L2/L3 → prefetch 召回（E2E）。
4. 推送调度符合 AGENTS.md：策略+触发面、无常驻进程。
5. Hermes 集成边界清晰：cgm 工具 vs cgm_memory provider。

### 3.4 关键设计债

| ID | 问题 | 影响 |
|----|------|------|
| P0-R1 | 默认 runtime DB 空 | 首次 Hermes 会话无报告/记忆 |
| P0-R2 | KB 578 卡 verified=false | 医学零容错产品门槛 |
| P1-1 | memory.confirm occurred_at=now | 多日模式无法巩固 |
| P1-2 | verify_quotes 靠 SKILL | 未加载 skill 时医学数字未校验 |
| P1-3 | push-tick 非 Hermes tool | cron 须调 CLI |
| P1-4 | delivery email/webhook queued only | 外部触达未闭环 |

---

## 4. 对齐矩阵：外部最佳实践 × 内部选择

| 外部最佳实践 | 内部选择 | 对齐 | 备注 |
|--------------|----------|------|------|
| 不在 App 内自建 general chat 引擎 | Hermes 主壳 + CGM 插件 | ✅ | 与 2026 agent shell 趋势一致 |
| 数值由确定性代码计算 | analytics + D015 | ✅ | 优于纯 LLM AGP 摘要方案 |
| 权威 KB vs 个人 memory 隔离 | dual-track + memory_guard | ✅ | 同 ClinicalAgents dual-memory |
| Hot 直取 / Cold 检索 | D029 Hot SQL + L1 hybrid 阈值 | ✅ | 避免小库 over-engineering |
| Claim-level RAG + citation verify | claim card + verify_quotes | 🟡 | 检索强；生成强制弱 |
| Tiered proactive push | PushScheduler daily/weekly/monthly | ✅ | 缺渠道/UI |
| AGP + clinician report | TIR/GMI/CV + clinician Markdown | 🟡 | 缺 AGP 图/PDF |
| Privacy / local-first | SQLite + Fernet + 本地 CLI | ✅ | 与 Open-D/GlycemicGPT 同向 |
| Non-diagnostic coaching disclaimer | SafetyRouter 三区 | ✅ | 需产品 copy 层配合 |
| Clinical KB human sign-off | D040/D041 管线 + verified 闸门 | 🟡 | 工程有，内容无 |
| Bitemporal belief tracking | L2/L3 valid_from/to | ✅ | 领先多数消费级 App |
| USER.md 单向投影 | D039 user_md_sync | ✅ | 避免双向 merge 冲突 |

---

## 5. 交叉裁决：五个显式问题

### Q1. 架构选择是否与行业最佳实践对齐？

**是，且偏保守（安全侧）**。

- **Hermes shell + capability layer**：符合 2026 个人 agent 框架分工（Hermes do-learn-improve vs OpenClaw gateway-first）。自托管、MIT、分层记忆与项目 AGENTS.md 一致。
- **Dual-track RAG + Hot/Warm/Cold**：与 ClinicalAgents、Deep-DxSearch、OpenClaw Hospital 论文方向同构；对 ~50 篇指南规模，**BM25 + claim card 优于盲目 embedding**（D036 决策有行业支撑）。
- **Bi-temporal + USER.md sync**：领先多数消费级 diabetes App；与 Memento/Membread 等 agent memory 前沿一致。

**无需 pivot**。可选增强（非必须）：Hermes hook 后置 `verify_quotes`、manifest-guided  longitudinal retrieval（若 L1 episode 规模暴增）。

### Q2. 开发优先级（记忆/RAG 闭环先于交付面）是否正确？

**基本正确，需微调顺序**。

**支持该顺序的理由**：
- 产品差异化在 **「记得住、引得对、推得准」**，不在又一个 glucose chart UI。
- R1–R6 证明：population fail-open、verify_quotes、plugin 漂移等 **不先闭环会导致交付面建立在假能力上**。
- SNAQ/Manna 等产品的 moat 在 **数据+指南 RAG**，非 UI 炫技。

**需要提前的项（4–8 周内）**：
1. **P0-R1 空库 onboarding** — 无数据则 memory/RAG 闭环无法被用户感知；应在 P3 smoke 前完成 `seed-demo`/import 文档或 installer 可选种子。
2. **P3 Hermes 安装 smoke** — 属于「闭环验证」，不是「交付面」，应与 memory 审计并行。
3. **KB 签核批次** — 不是工程 refactor，但是 **产品门槛**；4–8 周内应启动 curated tier 的 `verified=true` 小批次（如 6 张种子 + 20 张高危卡），而非等 P5 全量。

**可继续后置**：微信/App 卡片、PDF、MAGE/AGP 可视化、Dexcom live、拍照餐食。

### Q3. 相对替代方案，当前路径何处 suboptimal？

| 替代方案 | 优势 | 相对当前路径的劣势 | 结论 |
|----------|------|-------------------|------|
| **A. 自建 chat + 移动 App 一体** | UI 控制、上架快 | 重复 Hermes 已有能力；违反 AGENTS.md；memory/cron/多通道维护成本高 | ❌ 不推荐 |
| **B. 纯 Nightscout skill / Copilot skill** | 极轻、AGP HTML 现成 | 无 L0–L3、无推送调度、无安全路由、无 Weitai 蓝图对齐 | 仅适合作 skill 导出层 |
| **C. GlycemicGPT 式全栈自托管** | 交付面完整 | 与 Hermes 主壳战略冲突；双倍维护 mobile+backend | 可借鉴 tiered alerts UI，非主路径 |
| **D. 微泰官方云平台深度集成** | 数据与注册通道 | 封闭、难自托管 agent；监管路径不同 | 作 **数据源/API** 互补，非替换 Hermes |
| **E. 更大 embedding/RAG 栈** | 语义召回 | 578 卡有界库上 ROI 低；增加不可解释排序风险 | D036 已正确否决为默认 |
| **F. 更早做 MAGE/AGP/PDF** | 临床可读性 | 在 verify_quotes/KB 未签核前放大错误医学叙事风险 | 正确后置到 P4 |

**当前路径主要 suboptimal 点**：
1. **生成层安全依赖 SKILL 契约** — 行业趋势是 verifier loop（VeReaFine）；应在 Hermes 侧加 hook 或 post-turn callback。
2. **push-tick 仅 CLI** — 与「Agent 原生调度」愿景略脱节；Open-D 类产品的 proactive 感来自 **in-app/agent 内触发**。
3. **大模块 cli/executor/builder** — 可维护性成本；非方向错误，但会拖慢 P3 后迭代。
4. **文档计数漂移**（314→337 tests，14→15 tools）— 治理问题，非架构问题。

### Q4. 哪些明显正确、不应改动？

1. **Hermes 作为主 shell，不在仓内造 general chat engine**（AGENTS.md 硬规则）。
2. **双轨 RAG 物理隔离 + analytics 算数 + LLM 只叙事/编排**（D015/D027/D031）。
3. **Claim card + tier 护栏 + eval-rag CI 门禁**（D042 修正路径正确）。
4. **PushScheduler 策略+外部 cron、无常驻进程**（AGENTS.md + 可测试性）。
5. **Silent consent 收窄到 observing**（伦理上优于 broad auto-accept）。
6. **SafetyRouter 红区整体替换、不做开放式医疗 QA**（监管友好）。
7. **SQLite 统一存储 + Fernet + audit**（个人健康 agent 合理默认）。
8. **Dexcom 代码保留但阶段 mock**（避免 live 接入分散 P3 注意力）。
9. **持续审计闭环 + 守卫测试**（R1–R6 已证有效）。
10. **文档先行 ADR/DECISION_LOG/MEM-ARCH**（偿还 phantom doc 债的方向对）。

### Q5. 未来 4–8 周建议调整（有序）

| 优先级 | 行动 | 理由 | 预估 |
|--------|------|------|------|
| **1** | P3：Hermes installer 端到端 smoke（安装→dev-status 非空→hermes chat tool-call→verify_quotes 路径） | 闭环「声明=运行面」 | 1–2 周 |
| **2** | Onboarding：文档/installer 可选 `seed-demo` 或示例 CSV import；解决 P0-R1 | 无数据则产品不可演示 | 与 #1 并行 |
| **3** | P1-1：`memory.confirm` 支持真实 `occurred_at`；统一 report-candidate 与 seed-demo 路径 | 记忆巩固正确性 | 1 周 |
| **4** | KB 签核 SOP：首批 20–30 张高危卡 `verified=true`（TIR/TBR/ hypo 处置） | P0-R2 产品门槛 | 2–4 周（含人工） |
| **5** | Hermes hook：post-generation 调用 `rag.verify_quotes` strict（或等效 middleware） | 闭合 P1-2 | 1–2 周 |
| **6** | 评估 `push_tick` Hermes tool + `delivery.send` local_file 自动衔接 | 分层推送可感知 | 1 周 |
| **7** | 更新 STATUS 计数；CI paths 交叉或 scheduled 全量 | 治理 | 0.5 周 |
| **8** | P4 起步：AGP 百分位 **文本附录**（先于 PDF/图） | 医生报告蓝图差距 | 2 周（可并行 #4 后） |

**显式不做（除非用户授权）**：Dexcom live 默认、微信全量、胰岛素剂量建议、自建 mobile App UI。

---

## 6. 风险对比：当前路径 vs 替代路径

| 风险 | 当前路径 | 主要替代（自建 App 一体 / 重 UI 先行） |
|------|----------|--------------------------------------|
| 医学数字幻觉 | 中 — 有 guard，生成层未硬强制 | 高 — 易先堆 UI 后补 RAG |
| 交付慢、demo 空 | 中 — 空库 + 无卡片 | 低 demo 感 — 但 memory 浅 |
| Hermes 依赖/版本漂移 | 中 | 低（若自建）但 engineering 爆炸 |
| 监管越界 | 低 — 三区 + 非诊断 | 中 — UI 易诱导剂量建议 |
| KB 签核瓶颈 | 高 — 578 unverified | 同样高若走 RAG |
| 维护成本 | 中 — 大模块 | 高 — 全栈 |
| 与微泰生态协同 | 中 — 需 API 对接 | 中 — 厂商路线不同 |

**结论**：当前路径的 **最大可执行风险** 是「工程已就绪但 **用户首次打开是空的** + **KB 未签核**」，而非架构选错。替代路径通常 **交换** 为更高监管/维护/重复建设风险。

---

## 7. 不确定性声明

1. **微泰 AI 产品路线图** 公开细节少于 press release；与第三方 agent 的 API 开放度未验证。
2. **Open-D / Aegle** 等技术栈未开源，对标限于产品描述。
3. **FDA 2026 General Wellness** 对「CGM 数据 + AI coaching」组合的执法边界仍在演进；本项目应持续避免 treatment suggestion 表述。
4. **Hermes 快速迭代**（v0.15.1）：plugin API、memory provider 契约可能变化；P3 smoke 应 pin 版本或加兼容测试。
5. **临床签核** 无法由工程 alone 完成；时间表依赖外部 reviewer。

---

## 8. 参考文档（内部）

- [AUDIT-2026-06-07-IMPLEMENTATION-REVIEW.md](./AUDIT-2026-06-07-IMPLEMENTATION-REVIEW.md)
- [STATUS-REPORT-2026-06-07.md](./STATUS-REPORT-2026-06-07.md)
- [DECISION_LOG.md](./DECISION_LOG.md)
- [MEM-ARCH.md](./MEM-ARCH.md)
- [ADR-0001](./adr/ADR-0001-memory-and-knowledge-architecture.md)
- [AUDIT-2026-06-07-持续审计闭环-R1-R6.md](./AUDIT-2026-06-07-持续审计闭环-R1-R6.md)

---

*本报告由 2026-06-07 战略方向验证任务产出；未修改任何源码。*
