# Feature Tasks: Companion Narrative + Negotiated Interaction (F4)

## Phase 1: Foundational State & Scheduler
**Goal:** Prepare the underlying data models and push scheduler logic for the narrative layer to consume.
**Independent Test Criteria:** State models can hold TTL, and scheduler correctly respects rate limits and accumulates badges.

- [x] T001 [US3] Add `EscalationState` derivation logic to `src/hermes_cgm_agent/domain/memory.py`.
  - **测试方式**: Unit tests for derivation function with 1, 3, 5 days inputs.
  - **完成标准**: Model correctly maps consecutive anomaly days + vulnerable flag to NORMAL/CONCERN/EXTERNAL_SUPPORT.
- [x] T002 [US2] Implement `PendingInteraction` model with 3-day TTL in `src/hermes_cgm_agent/domain/memory.py`.
  - **测试方式**: Unit test instantiating and expiring the model.
  - **完成标准**: Model exists, `is_active` computed correctly based on 3-day TTL.
- [x] T003 [P] [US3] Update `PushSchedulerService` in `src/hermes_cgm_agent/services/scheduler.py` for 1-day rate limit and explicit trigger thresholds.
  - **测试方式**: Mock time/data and trigger non-urgent pushes; verify only pushes meeting thresholds (TIR delta ≥5%, consecutive ≥2 days same-period anomaly, new L3 hypothesis) are sent, non-urgent is rate-limited to 1/day, and push latency is non-realtime.
  - **完成标准**: Rate limit, thresholds, and non-realtime polling latency are correctly enforced.
- [x] T004 [US3] Add OS Push failure fallback to `PushSchedulerService` (`src/hermes_cgm_agent/services/scheduler.py`).
  - **测试方式**: Mock `PermissionDenied` on OS push API; assert internal badge count increments.
  - **完成标准**: Push failure does not drop the message; instead, it writes to a pending badge state.

## Phase 2: Narrative Extraction & Templates (US1, US2)
**Goal:** Cleanly separate clinical and companion narrative templates.
**Independent Test Criteria:** Templates generate string outputs with explicit uncertainty and no clinical jargon.

- [x] T005 [P] [US2] Create `src/hermes_cgm_agent/services/reports/narrative_templates.py`, add Hypothesis state templates, and implement `validate_companion_text()`.
  - **测试方式**: `pytest` matching templates against SOUL.md styles, and testing `validate_companion_text()` with text containing clinical abbreviations (TIR, TAR, TBR, GMI, CV, LBGI, HBGI) or assertive phrases.
  - **完成标准**: 4 templates exist using协商式词汇; `validate_companion_text()` correctly blocks blacklisted abbreviations and assertive/causal phrases, and enforces ≤100 char limit for push messages.
- [x] T006 [P] [US1] Extract companion tone translations (TIR -> life language) into `narrative_templates.py`.
  - **测试方式**: Pass mock TIR=75% and verify output is "大部分时间都在范围里".
  - **完成标准**: All raw clinical metric translations are isolated here.

## Phase 3: Builder Isolation & Safety (US1, US3)
**Goal:** Hook the new templates into the report builder and enforce strict F3/F4 physical isolation and safety blockers.
**Independent Test Criteria:** F3 output is pure clinical; F4 output is pure companion; vulnerable users get blocked until acknowledged.

- [x] T007 [US1] Refactor `src/hermes_cgm_agent/services/reports/builder.py` to branch cleanly into `render_clinical` and `render_companion`.
  - **测试方式**: Generate both report types and assert tone/content differences.
  - **完成标准**: F3 returns numbers/tables; F4 returns conversational Chinese.
- [x] T008 [US3] Inject "Safety Disclaimer" blocking logic in `builder.py` for vulnerable populations.
  - **测试方式**: Generate report for user with `vulnerable_population=true` and unacknowledged disclaimer.
  - **完成标准**: Output is ONLY the disclaimer prompt requesting "已知晓" before rendering the actual report.
- [x] T008a [US3] Implement Principle III Safety Override blocker in `builder.py`.
  - **测试方式**: Generate report when SafetyRouter returns RED_ZONE.
  - **完成标准**: Report entirely skips companion narrative and hypothesis rendering (FR-009 compliance).

## Phase 4: CLI Integration & Final Wiring (US1)
**Goal:** Allow users to fetch F3 explicitly.
**Independent Test Criteria:** `/report` command successfully outputs F3 clinical card in the chat interface.

- [x] T009 [P] [US1] Expose `/report` slash command in `src/hermes_cgm_agent/cli.py` (or command router).
  - **测试方式**: CLI end-to-end integration test simulating `/report` input.
  - **完成标准**: Invoking the command bypasses F4 narrative and prints the F3 clinical report directly.

## Phase 5: Polish & Regression
**Goal:** Ensure we didn't break the world.
**Independent Test Criteria:** CI is green.

- [x] T010 Run full test suite to verify 374+ tests remain green.
  - **测试方式**: Run `pytest tests/` locally.
  - **完成标准**: 0 failures, 0 regressions.

---

# Remediation Tasks (源自 [remediation-plan.md](./remediation-plan.md) + 2026-06-10 /analyze §10)

> 这些任务修复审计发现的 F-1…F-9 与自审 N1…N12。**ID 用 `R*`/`RC*` 前缀**与上方 T001–T010 命名空间隔离；T001–T010 状态由 **R050** 在执行时带证据核对（不在此预先翻动复选框）。Test-First：标 `[test]` 的任务先写失败测试。**先建 feature 分支再开工（当前在 master，N11）。**

## R-Phase 0：Setup + 裁决（阻断一切；先行）
**Goal:** 切到 feature 分支并就四个互斥点拍板，写入决策日志。
**Independent Test Criteria:** 分支为 `003-companion-narrative`；`docs/DECISION_LOG.md` 含 RC1–RC4 四条新 `Dxxx`。

- [x] R000 切换/创建 feature 分支 `003-companion-narrative`（脱离 `master`）— git 工作区
- [x] RC1 裁决升级阈值并以 SOUL.md 为准（标准 CONCERN≥day3/EXTERNAL≥day7；弱势 CONCERN≥day1/EXTERNAL≥day5）→ 已记入 `docs/DECISION_LOG.md` **D046**
- [x] RC2 裁决 push 文案来源（为 push 单独渲染 companion 文案，不改 `synthesize_state`）→ 已记入 **D046**
- [x] RC3 裁决 `/report` 确定性边界（工具内确定性直出纯 F3 + 提示词路由；重述 FR-011）→ 已记入 **D046**
- [x] RC4 裁决弱势免责声明（标 KNOWN GAP + 夹具测试）→ 已记入 **D046**

## R-Phase 1：校验契约重构（N9：必须先于 Phase 2/Phase 3 的调用点）
**Goal:** 把 `validate_companion_text` 拆成可分流的纯函数，区分"黑名单硬阻断"与"超长截断"。
**Independent Test Criteria:** 黑名单命中被硬阻断，超长被截断+审计，`CV` 按词边界匹配。

- [x] R040 [test] 黑名单/超长/词边界用例 in `tests/services/reports/test_narrative_templates.py`
- [x] R041 拆分 `validate_companion_text`：纯函数返回 `violations`（带类型）+ runtime 分流（黑名单阻断/超长截断）+ `\bCV\b` 词边界 in `src/hermes_cgm_agent/services/reports/narrative_templates.py`

## R-Phase 2：US2 假设叙事接线（修 F-1，CRITICAL）
**Goal:** 让报告按 L3 状态渲染协商式话术，消除死代码。
**Independent Test Criteria:** weekly 报告中 4 状态话术匹配 narrative-contracts；红区抑制；FR-013 结构不变。

- [x] R001 [test] [US2] 4 状态（candidate/observing/stable/archived）话术接入 `patterns` 段的失败测试 in `tests/services/reports/test_report_builder.py`
- [x] R002 [US2] 在 `_patterns_section`（或新 `_hypothesis_narrative_section`）按 `state`+`evidence_count` 调 `render_hypothesis_narrative` in `src/hermes_cgm_agent/services/reports/builder.py`
- [x] R003 [test] [US2] 红区整段抑制假设叙事（FR-009）in `tests/services/reports/test_report_builder_safety.py`
- [x] R004 [test] [US2] 接线后保留 `evidence_refs/source_tracks/confidence/data_quality_warnings`（FR-013，N5）in `tests/services/reports/test_report_builder.py`

## R-Phase 3：Push 合规（修 F-3；依赖 R041）
**Goal:** push 投递前经 companion 渲染并强制校验，去缩写、≤100 字。
**Independent Test Criteria:** push 内容无 TIR/TAR/… 缩写、≤100 字、过校验；badge 兜底同样投递校验后文案。

- [x] R010 [test] [US1] push 无临床缩写 + ≤100 + 过校验 in `tests/services/scheduling/test_scheduler.py`
- [x] R011 [US1] push companion 渲染器（`translate_metric`→生活语言，组装 ≤100 字，RC2）in `src/hermes_cgm_agent/services/scheduling/scheduler.py`
- [x] R012 [US1] `_emit` 在 `send_os_push`/badge 前调用渲染器 + `validate_companion_text`（黑名单阻断/超长截断）in `src/hermes_cgm_agent/services/scheduling/scheduler.py`

## R-Phase 4：升级数据闭环（修 F-2 + F-5；依赖 RC1）
**Goal:** 升级天数从 analytics/events 直接重算，阈值对齐 SOUL.md。
**Independent Test Criteria:** 模拟 1–7 天异常（不预置 push_events），标准/弱势升级在 push 与 reports.generate 两路径都正确。

- [x] R020 [test] [US3] 1–7 天升级（标准 + 弱势按 RC1）无需预置 push_events in `tests/services/scheduling/test_escalation.py`
- [x] R021 [US3] 重写 `consecutive_anomaly_days`：用 `CGMAnalyticsService`/`GlucoseEventDetector`，异常日=TAR/TBR>0 或非 DATA_GAP 事件 或 warning，按 `self._tz` 切日界（N6）in `src/hermes_cgm_agent/services/scheduling/scheduler.py`
- [x] R022 [US3] 按 RC1 同步 `EscalationState.derive` 阈值 in `src/hermes_cgm_agent/domain/memory.py`，并改 `specs/003-companion-narrative/spec.md`(US3 AS2/AS3) + `data-model.md` 阈值表
- [x] R023 [test] [US3] 红区整段抑制升级关心（FR-009）in `tests/services/reports/test_report_builder_safety.py`

## R-Phase 5：/report 可达性 + 弱势免责声明（修 F-4 + F-9；依赖 RC3/RC4）
**Goal:** `reports.generate` 工具确定性直出纯 F3；免责声明依 RC4 落地或显式休眠。
**Independent Test Criteria:** 工具对临床路径确定性返回纯 F3（不经 LLM）；免责声明路径可用或有休眠用例。

- [x] R030 [test] [US1] `reports.generate` 临床路径确定性纯 F3（绕过 F4 叙事）in `tests/services/reports/test_report_tools.py`
- [x] R031 [US1] 确认工具内确定性直出 + 在 `provider.py` 记录 `/report` 提示词路由（FR-011 重述，RC3）in `src/hermes_cgm_agent/services/reports/tools.py` + `src/hermes_cgm_agent/services/memory/provider.py`
- [x] R032 [US3] 依 RC4：实现 `vulnerable_disclaimer_acknowledged` 写入路径，或标 KNOWN GAP + 夹具注入用例 in `src/hermes_cgm_agent/services/reports/builder.py`

## R-Phase 6：文档/任务对齐（修 F-6 + F-7 + 追踪）
**Goal:** 收敛单一事实源。
**Independent Test Criteria:** tasks.md 状态有据；无幻影任务引用；plan 路径准确。

- [x] R050 [P] 带证据核对并修订 T001–T010 状态，并把 R*/RC* 行登记入 `specs/003-companion-narrative/tasks.md`
- [x] R051 [P] 删除/修正 `spec.md` 中幻影 `T042b`/`T044` 引用，修 `plan.md` 路径漂移（`services/scheduling/scheduler.py`、`/report` 落点）in `specs/003-companion-narrative/{spec.md,plan.md}`

## R-Phase 7：回归门禁
**Goal:** 不破坏世界。
**Independent Test Criteria:** 全绿。

- [x] R060 全量测试 ≥374 绿、0 回归，新增用例 ≥15（SC-006）— `python -m unittest discover -s tests`（或 `pytest tests/`）

## Dependencies & Ordering
- `R000 → RC1–RC4 → 实现各 Phase`。
- **N9**：`R041`（校验契约）必须先于 `R012` 与 `R002` 的校验调用点。
- US2：`R001 → R002 → R003/R004`。
- 升级：`RC1 → R022`；`R021 → R020` 验证。
- `R050/R051` 在实现后；`R060` 最后。

## Parallel 机会
- 完成 R-Phase 0 + R041 后：**US2（builder.py：R001-R004）** 可与 **scheduler 改动** 并行；但 **R-Phase 3（push：R011/R012）与 R-Phase 4（升级：R021/R022）都改 `scheduler.py` → 必须串行**，勿同时并行。
- `R050 [P]` ∥ `R051 [P]`（不同文件）。

## 建议 MVP
**R-Phase 0（裁决）+ R-Phase 1（校验契约）+ R-Phase 2（US2 假设叙事接线）** —— 直接消灭 CRITICAL（F-1 死代码），是第一个有价值增量；随后 push 合规（F-3）与升级闭环（F-2/F-5）。

---

## 执行状态（2026-06-10，R050 reconcile，带证据）

- **分支**：`003-companion-narrative`（脱离 `main`，R000）。
- **测试基线**：392 → **407 全绿**（+15 新用例，满足 SC-006 ≥15；`python -m unittest discover -s tests`）。
- **analyze**：0 CRITICAL（D046 已 propagate 进 spec/data-model/plan）。
- **决策**：D046（RC1-4）记于 `docs/DECISION_LOG.md`。

| 任务 | 状态 | 证据 / 备注 |
|---|---|---|
| RC1-RC4 | ✅ | D046 四裁决 |
| R000 | ✅ | feature 分支 + checkpoint commit |
| R040/R041 | ✅ | `check_/enforce_companion_text` 拆分 + CV 词边界（N4/N12）；3 用例 |
| R001-R004 | ✅ | `_hypothesis_narrative_section` 接线（消灭 F-1 死代码）；红区抑制 + 个人轨道（FR-013）；3 用例 |
| R010-R012 | ✅ | `_companion_push_text` + `enforce`（去缩写、≤100）；badge 兜底文案合规；2 用例 |
| R020-R023 | ✅ | `EscalationState.derive` → SOUL/D046（标准 3/7、弱势 1/5）；`consecutive_anomaly_days` 改从 analytics 重算（修 F-2）；红区抑制；多用例 |
| R030 | ✅ | 医生版纯 F3、无陪伴叙事泄漏；1 用例 |
| R031 | ✅ | `provider.py` 已路由 `/report`→`cgm_reports_generate(audience=CLINICIAN)`；工具内确定性。**T009 按 D046/RC3 重述**（工具确定性 + 提示词路由，非 cli.py 命令） |
| R032 | ✅(休眠) | 免责声明逻辑 + ack 读取已测（`test_safety_disclaimer_gating`）；生产休眠 KNOWN GAP（依赖上游写 `vulnerable_population`，spec FR-008 已注明） |
| R050 | ✅ | 本表 + T001-T010 复选框据实核对 |
| R051 | ✅ | 删 spec 幻影 `T042b/T044`；修 plan `services/scheduling/scheduler.py` 路径 |
| R060 | ✅ | 407 全绿、0 回归 |

> **合并前仍建议（宪法工作流）**：Damocles 复核安全/边界（F-2/F-3/红区抑制）、Luna 复核人设（F-4 语气/升级话术）。本轮为自主执行（用户授权"最优解"），决策与守卫测试已就位，等待人审签核。
