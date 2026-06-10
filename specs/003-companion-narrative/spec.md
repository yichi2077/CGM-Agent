# Feature Specification: Companion Narrative + Negotiated Interaction (F4)

**Feature Branch**: `003-companion-narrative`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "F4 陪伴者叙事 + 协商交互：合并条目 C1+C2+C3。C1 报告中文叙事层完善（TIR→生活语言、周报/医生版/家属版叙事差异）。C2 协商式假设验证话术（四状态机接入话术 + 邀请验证流程）。C3 连续异常渐进关心 + 脆弱人群更早干预。宪法约束：原则 IV 知情陪伴者人设契约。"

## Overview

This feature brings the CGM agent's report narrative and hypothesis interaction
to the level defined in SOUL.md. Today the report builder has audience-specific
branching (self/clinician/family) but the narrative quality is uneven: some
sections still use raw percentages without life-language context, the hypothesis
state machine (candidate → observing → stable → archived) exists in data but
lacks persona-compliant conversational templates, and the continuous-anomaly
escalation strategy defined in SOUL.md (day 1/3/5 concern progression, earlier
intervention for vulnerable populations) has no implementation in the scheduling
or report layer.

The goal is to make every user-facing report section and hypothesis interaction
read like a compassionate companion speaking naturally in Chinese, while
maintaining all medical-safety, dual-track-isolation, and persona-contract
invariants from the project constitution.

Backlog status: C1=PARTIAL (audience skeleton exists), C2=OPEN (state machine
exists but no conversational templates), C3=VERIFY (SOUL.md defines escalation
but needs to verify if scheduling/report implements it).

## Clarifications

### Session 2026-06-10 (Narrative Style & Interaction Flow)

Resolves F4 narrative style and push boundaries via user selection:
- **表达风格 (A1, A5, A6)**：采用“共同探索型”，多用“我们发现”等协商式词汇；用提问引导表达不确定性（“你觉得可能是因为...吗？”）；对“坏习惯”不作道德评判，仅提醒潜在影响且不带情绪。
- **隐性推理与假设 (A2, A3)**：在自然对话中穿插轻量的推理过程（L1/L2）；长期假设（L3）完全在后台流转，前端只通过自然语言体现，不暴露状态机。
- **主动打扰与断联响应 (A4, A11)**：打破每天一次的限制。只要发现有价值的 Insight，就随时主动 Push；遇到数据缺失或断联异常时，立即主动发送消息询问用户。
- **长篇输出与退场 (A10, A12)**：长篇医学分析前，先询问“需要听详细分析吗？”，肯定后再发送；遇到“不知道”的情况，坦诚告知并主动提出一个可执行的观察实验。
- **跨天记忆 (A9)**：在当前话题相关时，Agent 自然引入历史记忆（“这和昨天下午的趋势很像”）。
- **特殊交互机制 (A7, A8)**：对于脆弱人群，增加特殊的“安全免责声明”卡片或弹窗；**F4 (陪伴对话) 与 F3 (医疗卡片) 完全隔离**，对话不涉及严谨报告内容，报告也不体现聊天语气。
- **主动 Push 触发边界 (Clarify-1)**：“有价值的 Insight”不仅限紧急情况，还包括非紧急但具有参考价值的日常趋势发现（如“今天下午的波动比昨天平稳”）。
- **无响应退避策略 (Clarify-2)**：主动 Push 询问断联/异常后，若用户未回复，系统不主动重试，默默记录，等待用户下一次主动交互时再顺带提起。

### Session 2026-06-10 (Checklist Quality Resolution)

Resolves F4 narrative quality, insight threshold, push message length, and tone isolation via user selection:
- **Insight 触发阈值 (CHK006)**：定义明确的触发阈值，仅在满足以下条件之一时触发主动 Push：TIR 变化幅度（delta）≥ 5%、连续 ≥ 2 天在同一时间段出现异常波动、或生成新的 L3 假设候选（CANDIDATE）。
- **Push 消息长度限制 (CHK007)**：主动 Push 消息有独立的长度限制，要求不超过 100 个汉字（因 Push 需具备完整的上下文，可长于日常卡片的 50 字限制）。
- **语气隔离与黑名单校验 (CHK008)**：F4 陪伴者叙事输出中不得包含任何临床专业缩写（如 TIR、TAR、TBR、GMI、CV、LBGI、HBGI）以及任何武断/断言式短语，并建立黑名单校验机制（通过 `validate_companion_text()` 函数）进行强制校验。
- **Push 延迟与实时性要求 (CHK009)**：不要求实时推送，允许有数小时的合理延迟；完全依赖现有的 `push_tick` 轮询机制（频率至少每天 1 次）进行判定与发送。

### Session 2026-06-10 (Checklist Gaps Resolution)

Resolves gaps identified by speckit-checklist:
- **防打扰机制（频率上限）**：非紧急主动 Push 限制为每天最多 1 次，紧急异常不受限制。
- **“沉默记录”失效规则（TTL）**：未回复的主动提问状态仅保留 3 天，逾期作废。
- **“免责声明”交互**：对于脆弱人群采用强阻断式，首次触达时强制弹窗并要求输入“已知晓”才能继续。
- **F3/F4跳转路径**：通过专属指令（如 `/report`）从对话流中独立拉取纯净版 F3 卡片。
- **OS Push兜底**：推送权限关闭时，应用内转为未读消息红点（Badge）积攒。

### Session 2026-06-09

- Q: HypothesisState 枚举中 SOUL.md 使用 "失效/归档" 而代码使用 ARCHIVED，C2 的话术模板应以哪个为准？ → A: 代码枚举 ARCHIVED 为权威，话术模板按 "归档" 语义编写，映射到 HypothesisState.ARCHIVED。
- Q: C3 脆弱人群（孕期妈妈、1型、小朋友、长辈、合并症）的用户类型标识从哪里来？ → A: 复用 L2ProfileItem 中已有的 user_profile 语义，通过 key 区分（如 `vulnerable_population=true`）；不新增独立实体。调度器读取 L2 profile 判断是否启用提前升级策略。
- Q: C2 协商式验证话术应该放在 builder.py（报告层）还是单独的 narrative 模板服务？ → A: 作为 builder.py 内部的 `_hypothesis_narrative_*` 方法族，与现有 `_daily_card_text` 等方法保持同一抽象层次；不引入新的服务层。
- Q: C3 升级策略（第1/3/5天）应该在报告生成时实时计算还是调度器推送时预计算？ → A: 调度器 `push_tick` 时计算连续异常天数并决定升级等级，报告层读取升级状态渲染相应关心话术；调度器负责"何时升级"，报告层负责"怎么说"。
- Q: 医生版报告中是否也需要叙事润色，还是保持现有纯数字结构化格式？ → A: 医生版保持现有结构化数字格式（临床语言），不做叙事润色；润色仅面向 SELF 和 FAMILY 两个受众。

### Session 2026-06-09 (review remediation — autonomous, review-time confirmable)

Resolves ambiguities from a code-grounded `/speckit-analyze` pass.

- Q: 升级等级在 `push_tick` 算出，但按需报告（`reports.generate`）不走 push_tick，`builder.generate(report_input)` 怎么拿到 `consecutive_days`？ → A: 在 `ReportInput` 增加可选字段 `consecutive_anomaly_days` / `escalation_level`；push 路径与 `reports.generate` 执行器**都**在构建报告前调用 `PushSchedulerService.consecutive_anomaly_days(...)` 填充。报告层只读这两个字段渲染关心话术。见 tasks R020-R022（升级数据闭环）。
- Q: `vulnerable_population` key 目前无人写入，脆弱人群路径如何处理？ → A: 本轮保留"读 + 测试夹具注入"，但在生产中该路径**休眠**，直至上游设置该 key。已在 plan.md 标为 KNOWN GAP（带风险）。
- Q: 升级关心话术注入哪个 section？ → A: 固定注入 `_follow_up_section`（见 tasks R020-R022；builder `_follow_up_section`），不再二选一。
- Q: DSG-### 文档是"另行跟踪"还是已在本 feature？ → A: 已在 plan.md 内联为 DSG-001..005 审查门禁（Luna 合并前签核）；spec 的 out-of-scope 措辞以 plan 为准更新。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - I read a report that sounds like a friend talking to me (Priority: P1)

A user asks the agent for their daily or weekly report. The report they receive
uses natural, conversational Chinese — describing TIR as "大部分时间都在范围里"
rather than "TIR 78%", mentioning patterns in life terms ("午餐后有个小高峰，
可能跟外食有关"), and respecting the SOUL.md length norms (daily card 30-50
chars, weekly patterns 50-100 chars). The family version is even simpler — one
sentence conveying "平安" or a clear gentle heads-up.

**Why this priority**: This is the highest-frequency user interaction. Every
daily push and every on-demand report goes through this path. The existing
audience branching exists but needs narrative polish to match SOUL.md quality.
Delivers immediate visible improvement to the core product experience.

**Independent Test**: Generate a daily report (SELF audience) with known data
containing TIR=75%, TAR=20%, TBR=5%, and verify the output uses life-language
phrasing, not raw percentages, and is within the 30-50 char daily card length.
Repeat for FAMILY audience and verify one-sentence simplicity.

**Acceptance Scenarios**:

1. **Given** a daily report for SELF audience with TIR=75%, **When** the report is generated, **Then** the daily card section uses life-language phrasing (e.g., "大部分时间都在范围里") rather than raw "TIR 75%" and the card text is ≤50 Chinese characters.
2. **Given** a weekly report for FAMILY audience, **When** the report is generated, **Then** the summary is one sentence conveying overall safety status without clinical terminology or numbers.
3. **Given** a daily report with no exceptions (all values in range), **When** generated for SELF, **Then** the card is a brief positive message within SOUL.md norms that does not feel like a clinical clearance.

---

### User Story 2 - The agent invites me to explore patterns together (Priority: P2)

The agent detects a recurring pattern (e.g., post-lunch spikes on multiple
days). Instead of stating a conclusion, it presents the observation as a
hypothesis candidate and invites the user to help verify it. The language
matches the current hypothesis state: "看起来可能有关" (candidate), "过去5次里
有3次类似" (observing), "这个模式比较常见" (stable), or "之前的规律最近不明显"
(archived). The user can confirm, correct, or dismiss the hypothesis in
conversation.

**Why this priority**: This is the core of the "negotiated interaction" paradigm
(SOUL.md §协商式假设验证). Without persona-compliant hypothesis language, the
agent risks sounding like it's making clinical assertions — violating Principle
IV. Depends on the existing HypothesisState machine but adds the conversational
layer.

**Independent Test**: With an L3 hypothesis in CANDIDATE state, verify the
report/push content uses hedged language and an invitation to verify. With the
same hypothesis advanced to OBSERVING, verify the language reflects accumulated
evidence without asserting causation.

**Acceptance Scenarios**:

1. **Given** a detected pattern with an L3 hypothesis in CANDIDATE state, **When** the report includes this pattern, **Then** the narrative uses hedged language ("看起来可能有关，但还不够确定") and invites verification ("要不要接下来多留意一下？").
2. **Given** a hypothesis in OBSERVING state with evidence_count=3, **When** rendered in a report, **Then** the narrative references the count naturally ("过去几次里有几次类似") without using causal language.
3. **Given** a hypothesis in ARCHIVED state, **When** referenced, **Then** the narrative says "之前的规律最近不明显" and does not present it as an active finding.

---

### User Story 3 - The agent cares more when I need it most (Priority: P3)

When a user experiences consecutive days of glucose anomalies, the agent's
concern escalates naturally per SOUL.md: day 1 is normal attribution, a few
consecutive days (day 3+) shift to personal concern ("你还好吗？"), and about a
week (day 7+) suggests external support ("要不要跟医生聊聊？"). For vulnerable
populations (pregnancy, type 1 diabetes, children, elderly, comorbidities), the
escalation happens earlier — concern from day 1 and external support by day 5
(the SOUL.md "第一天/第三天/第五天" schedule). The user never feels monitored —
they feel cared for. (Thresholds fixed by D046/RC1; see data-model.md.)

**Why this priority**: This completes the companion persona by implementing the
progressive care strategy from SOUL.md. It touches the scheduler (escalation
calculation) and report (narrative rendering). Lower frequency than US1/US2 but
critical for the "informed companion" identity.

**Independent Test**: Simulate 7 consecutive days of anomaly data for a standard
user. Verify the daily report on day 1-2 uses normal attribution, day 3-6 shifts
to personal concern tone, and day 7+ includes an external-support suggestion.
Repeat for a vulnerable-population user and verify concern begins at day 1 and
external support by day 5 (the earlier SOUL.md "第一天/第三天/第五天" schedule).

**Acceptance Scenarios**:

1. **Given** a standard user has had glucose anomalies for 3 consecutive days, **When** the daily report is generated, **Then** the narrative shifts from data attribution to personal concern ("最近几天都有点波动，你还好吗？").
2. **Given** a standard user has had anomalies for 7+ consecutive days (about a week), **When** the daily report is generated, **Then** the narrative gently suggests external support ("要不要下次复诊时跟医生聊聊？"). (Days 3-6 remain in the concern band.)
3. **Given** a user is flagged as a vulnerable population, **When** anomalies persist, **Then** escalation begins earlier than the standard timeline: personal concern from day 1, and external-support suggestion by day 5 (vs. day 7 for standard users).

---

### Edge Cases

- **No CGM data available**: Report falls back to the existing "no data" messaging per audience; no escalation or hypothesis narrative is emitted.
- **Hypothesis with zero evidence_count in OBSERVING state**: System treats it as equivalent to CANDIDATE for narrative purposes (defensive coding; this state should not occur but the narrative layer must not crash).
- **Vulnerable population flag missing from L2 profile**: System falls back to the standard (non-vulnerable) escalation timeline; no error raised.
- **Red zone safety override**: All narrative is replaced by the safety message (Principle III); escalation concern and hypothesis narratives are suppressed entirely.
- **User explicitly dismisses a hypothesis**: Hypothesis transitions to ARCHIVED; narrative immediately reflects the archived state without residual language from previous states.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: F4 conversational interactions MUST be strictly isolated from F3 clinical reports. Chat interactions MUST NOT include clinical report structures, and F3 reports MUST NOT use chat/companion tone.
- **FR-002**: The system MUST use a "co-exploration" narrative style in conversations, incorporating light reasoning, questions for uncertainty, and objective consequence reminders without moral judgment.
- **FR-003**: The system MUST translate clinical metrics (TIR, TAR, TBR, MBG, CV, GMI) into life-language equivalents for SELF and FAMILY audiences (e.g., TIR → "大部分时间都在范围里", TAR → "偏高的时候").
- **FR-004**: The system MUST render L3 hypothesis narratives using state-appropriate templates: CANDIDATE → hedged + invitation, OBSERVING → evidence-counted observation, STABLE → confirmed pattern language, ARCHIVED → "最近不明显" demotion language.
- **FR-005**: Hypothesis and companion narratives MUST NOT use causal/assertive language ("经分析发现", "研究表明", "数据证明") in any state; all language MUST be hedged and non-directive per Principle IV. The system MUST implement a validation function `validate_companion_text()` to enforce this and strictly forbid clinical abbreviations (TIR, TAR, TBR, GMI, CV, LBGI, HBGI) in F4 outputs.
- **FR-006**: The system MUST calculate consecutive anomaly days for each user and derive the escalation level, grounded in SOUL.md (decision D046/RC1). **Standard users**: `NORMAL` day 0-2, `CONCERN` day 3-6, `EXTERNAL_SUPPORT` day ≥7 ("about a week"). **Vulnerable users** (earlier): `NORMAL` day 0, `CONCERN` day 1-4, `EXTERNAL_SUPPORT` day ≥5. Consecutive-day counting MUST be computed deterministically from analytics/events (not solely from persisted push summaries), using the scheduler timezone for day boundaries.
- **FR-007**: The system MUST proactively PUSH messages upon detecting Insights or missing data. Non-urgent pushes MUST be rate-limited to 1 per day. Urgent criticals are unlimited. Unanswered queries MUST NOT be retried proactively; they are stored with a 3-day TTL and referenced in the next user-initiated interaction if valid. Proactive pushes for daily trends are triggered only when meeting explicit thresholds: TIR delta ≥ 5%, consecutive ≥ 2 days same-period anomaly, or a new L3 hypothesis candidate. Push latency is non-realtime, relying on the daily `push_tick` loop.
- **FR-008**: Vulnerable population users MUST be identified via L2ProfileItem. The system MUST present a strong-blocking "Safety Disclaimer" requiring explicit "已知晓" (acknowledged) input before allowing further interaction. **Production-dormant (D046/RC4)**: the blocking logic and acknowledgment read MUST exist and be test-covered, but the path stays dormant until an upstream process sets the `vulnerable_population` key (out of F4 scope — see Assumptions).
- **FR-009**: The system MUST NOT emit escalation or hypothesis narratives during red-zone safety override (Principle III compliance).
- **FR-010**: The report narrative MUST respect SOUL.md output length norms: daily card ≤50 chars, weekly pattern ≤100 chars, general default ≤80 chars (for SELF audience). Active push messages MUST have an independent limit ≤100 chars to ensure self-contained context.
- **FR-011**: The system MUST expose strictly isolated F3 clinical reports on demand (e.g., via `/report`). Per D046/RC3 and Principle VII, the determinism guarantee lives in the `reports.generate` tool — its clinical path MUST return pure F3 (numbers/tables, no companion narrative, no LLM in the tool); routing the literal `/report` to that tool is handled by the Hermes provider prompt (the capability layer does not build a competing chat command).
- **FR-012**: The system MUST implement a fallback mechanism accumulating internal unread badges for proactive pushes if OS-level push notifications fail or are disabled.
- **FR-013**: All narrative changes MUST preserve existing evidence_refs, source_tracks, confidence, and data_quality_warnings structures — narrative is a rendering concern, not a data concern.
- **FR-014**: The existing automated test suite MUST remain green (374+ tests), and new narrative/escalation behaviors MUST be covered by regression tests.

### Key Entities

- **L3Hypothesis**: Existing entity with state machine (CANDIDATE → OBSERVING → STABLE → ARCHIVED). F4 adds narrative templates per state.
- **ReportAudience**: Existing enum (SELF, CLINICIAN, FAMILY). F4 enriches the narrative differentiation for each.
- **EscalationState** (new concept, not a separate table): Derived from consecutive anomaly days + vulnerable flag. Levels: NORMAL (day 0-2), CONCERN (day 3-4), EXTERNAL_SUPPORT (day 5+). Vulnerable users use compressed thresholds.
- **L2ProfileItem**: Existing entity. F4 reads `vulnerable_population` key to determine escalation timeline.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: F3 reports and F4 conversations are verifiably isolated in tone and content formatting.
- **SC-002**: The system triggers immediate proactive messages upon simulated sensor disconnection or new Insight generation.
- **SC-003**: Hypothesis narratives for each of the 4 states (CANDIDATE, OBSERVING, STABLE, ARCHIVED) match the SOUL.md template language — verified by state-specific test cases.
- **SC-004**: Escalation concern language appears at the correct consecutive-day thresholds for standard and vulnerable users — verified by simulating 1-7 days of anomaly data.
- **SC-005**: Zero narrative leakage during red-zone safety override — verified by safety router integration test.
- **SC-006**: The full automated test suite remains green with no regressions, and new test coverage ≥ 15 test cases for narrative/escalation/hypothesis behaviors.

## Assumptions

- **L2ProfileItem for population type**: Vulnerable population identification uses the existing L2ProfileItem key-value store. The `vulnerable_population` key (or equivalent) must be populated by an upstream process (not in F4 scope).
- **Consecutive anomaly tracking**: The scheduler (push_tick) already runs daily. F4 adds consecutive-day counting logic there; no new scheduling infrastructure is needed.
- **No new domain models**: EscalationState is a derived concept (computed from consecutive days + L2 profile), not a new persisted entity. If persistence is needed later, it can be added.
- **Narrative templates are code-embedded**: Following the existing pattern in builder.py, templates live as Python string literals in the builder. No external template engine or file-based templates.
- **Principle IV compliance**: All new narrative must pass the persona contract: non-directive, explicit uncertainty, history before knowledge, no judgment, default short conversational Chinese.
- **Out of scope**: The actual population-type detection/population (how `vulnerable_population` gets set in L2 — see plan.md KNOWN GAP) and the PRD §2.4 invitation verification UI flow are tracked separately. The DSG-### design/ethics review items are **in scope** and live in plan.md (DSG-001..005) as Luna review gates. F4 focuses on the narrative and escalation logic.
