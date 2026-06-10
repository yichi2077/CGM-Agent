# Remediation Plan: F4 Companion Narrative — 实现一致性修复方案

**Feature Branch**: `003-companion-narrative`（当前 git 分支：`master`）
**Status**: Draft（待人审 / Luna + Damocles 签核）
**Created**: 2026-06-10
**Spec Reference**: [spec.md](./spec.md) · [plan.md](./plan.md)（原 Approved 计划，本文件不覆盖它）
**Source of findings**: 2026-06-10 post-implementation 一致性审查（F-1…F-9）

> 本文件是对 F4 既有实现缺口的**修复计划**，不是新特性。它在不破坏已批准的 `plan.md`
> 前提下补充：缺口→修复映射、分阶段任务、测试策略、排序与风险。执行（改 spec/tasks/代码）
> 需人审批准后另行进行。

---

## 1. Technical Context

### 受影响文件（已核实路径）
| 关注点 | 文件 | 现状 |
|---|---|---|
| 假设叙事模板（死代码） | [services/reports/narrative_templates.py](../../src/hermes_cgm_agent/services/reports/narrative_templates.py) | `render_hypothesis_narrative` 未被调用 |
| 报告装配 / 叙事接线 | [services/reports/builder.py](../../src/hermes_cgm_agent/services/reports/builder.py) | `_patterns_section` 用旧 `signal.summaries`；companion 校验只覆盖报告路径 |
| 升级天数 / push 限流 / badge | [services/scheduling/scheduler.py](../../src/hermes_cgm_agent/services/scheduling/scheduler.py) | `consecutive_anomaly_days` 读不到 tar/tbr；push 未过 companion 校验 |
| 摘要写入 | [services/memory/consolidation.py](../../src/hermes_cgm_agent/services/memory/consolidation.py) | `synthesize_state` 只回写 `tir_pct`/`mean_mgdl` |
| 升级状态枚举 | [domain/memory.py](../../src/hermes_cgm_agent/domain/memory.py) | `EscalationState.derive` 弱势阈值与 spec 矛盾 |
| `/report` 入口 | [services/memory/provider.py:151](../../src/hermes_cgm_agent/services/memory/provider.py#L151) | 仅系统提示词，非代码命令 |
| 任务追踪 | [tasks.md](./tasks.md) | T003–T010 误标 `[ ]`；引用幻影 T042b/T044 |

### 未决问题（NEEDS CLARIFICATION）— 阻断 Phase 1 前必须定
- **NC-1（升级阈值——标准 + 弱势，均需对齐 SOUL.md）**：现状**五方互斥**，且关键是**全都偏离权威源 SOUL.md**：
  - SOUL.md（原则 IV 权威，[SOUL.md:122-124](../../SOUL.md#L122-L124) / [156-160](../../SOUL.md#L156-L160)）：**标准** = 第一天 normal → "连续几天" concern → **"一周(~day7)" external**；**弱势** = 触点 **day1 / day3 / day5**（即不等到第七天）。
  - spec US3 AS2 / data-model / `EscalationState.derive`：标准 EXTERNAL = **day5**（≠ SOUL 的 day7）。
  - 代码弱势 = day1 CONCERN / day3 EXTERNAL（≠ SOUL 的 day5 EXTERNAL）。
  - **裁决必须以 SOUL.md 为准**（见 §5 Phase 0 修订后默认值）；随后对齐 spec US3 AS2/AS3、data-model.md、`EscalationState.derive` 四处。⚠️ 本审计 F-5 原仅指出弱势侧矛盾，**漏了标准侧 day5↔day7**，N2 已补。
- **NC-2（push 文案来源）**：`synthesize_state` 的内容同时用于 **warm prefetch 摘要（D034）** 与 push。能否直接去缩写化它，还是为 push 单独渲染 companion 文案？建议单独渲染（见 §5 Phase 2），避免污染 prefetch 语义。
- **NC-3（`/report` 边界归属——确定性的可行边界）**：聊天态 `/report` **无法**在 CGM 层做硬拦截——`cli.py` 是 argparse 开发 CLI（[cli.py:65-263](../../src/hermes_cgm_agent/cli.py#L65)），不是 Hermes 聊天 REPL；按原则 VII 不得改 Hermes 安装树。可行的"确定性"只能是：**`reports.generate` 工具内部确定性直出纯 F3（工具内不经 LLM）**，而"`/report`→调用该工具"的路由仍由 Hermes 提示词承担。FR-011 据此**重述**（见 N3）。
- **NC-4（弱势免责声明落地 vs 休眠）**：F-9 的 `vulnerable_disclaimer_acknowledged` 是"补写入路径使其可用"还是"显式标记为生产休眠 KNOWN GAP（依赖上游写 `vulnerable_population`）"？需 Phase 0 裁决（原 R032 把它当实现细节，实为决策点）。

---

## 2. Constitution Check（修复方案对 7 原则的映射）

| 原则 | 评估 | 说明 |
|---|---|---|
| I. Medical Zero-Tolerance | ✅ 强化 | F-3 修复杜绝 push 把临床缩写/未校验文本投递给用户；叙事层不产生/改写任何数值（FR-013 不变）。注：`rag.verify_quotes` 自动闭环属 F3/002 范围，本计划仅记录、不实现。 |
| II. Dual-Track Isolation | ✅ 不回归 | 所有修复不触碰 `assert_track_isolation`（[reports/tools.py:80](../../src/hermes_cgm_agent/services/reports/tools.py#L80)）；新增回归断言其仍在叙事路径生效。 |
| III. Hard-Coded Safety Routing | ✅ 受保护 | F-1/F-2 接线后，红区必须继续整段抑制叙事与升级关心；Phase 1/3 各加红区抑制回归测试（守护 FR-009）。 |
| IV. Informed-Companion Persona | ✅ 主目标 | F-1（假设话术）、F-3（push 语气）、F-5（关心节奏）、F-8（校验不崩溃）直接服务原则 IV。 |
| V. Test-First & Green CI | ✅ 强制 | 每个修复**先写失败测试**；保持 ≥374 绿；新增 ≥15 用例（SC-006）。任务状态与代码对齐（修 tasks.md）。 |
| VI. Traceable Decisions | ✅ 修复 | F-6（plan 路径漂移）、F-7（幻影 T042b/T044）一并收敛；NC-1、NC-2 的裁决写入 `DECISION_LOG.md`（新 Dxxx）。 |
| VII. Hermes Boundary | ✅ 保持 | badge 兜底、push、`/report` 均经适配器/工具暴露，不改 `~/.hermes` 安装树；NC-3 在此原则下裁决。 |

**Gate 状态：GREEN（待 NC-1/NC-2/NC-3 裁决后复评）**。无未计划架构引入。

---

## 3. 缺口 → 修复映射

| ID | 严重度 | 修复目标 | 主要文件 | 验收锚点 |
|----|--------|----------|----------|----------|
| F-1 | CRITICAL | 把 `render_hypothesis_narrative` 接进报告（按状态渲染），消除死代码 | builder.py `_patterns_section` | US2 AS1-3 / FR-004 / SC-003 |
| F-2 | HIGH | `consecutive_anomaly_days` 改为从 analytics/events 直接重算，不依赖未写入的 tar/tbr | scheduler.py（+ 可选 consolidation.py） | US3 AS1-3 / FR-006 / SC-004 |
| F-3 | HIGH | push 发送前走 companion 渲染+`validate_companion_text(max_len=100)`，去缩写化 | scheduler.py `_emit` + 新 push 渲染器 | FR-005 / FR-007 / FR-010 / SC-002 |
| F-5 | HIGH | 统一弱势升级阈值（NC-1），四处文档+代码对齐 | memory.py + spec + data-model | US3 AS3 / SC-004 |
| F-8 | MEDIUM | runtime 校验改"记录+降级"，硬失败移到守护测试，避免叙事问题炸毁报告 | narrative_templates.py + builder.py | FR-013 / 原则 IV |
| F-4 | MEDIUM | `/report` 补代码级直出 F3 的硬保证（NC-3） | cli.py 或 tools 路径 | FR-011 / T009 |
| F-9 | MEDIUM | 免责声明/弱势路径"生产休眠"显式化：补 ack 写入路径或确认为 KNOWN GAP | builder.py + 写入方 | FR-008 / T008 |
| F-7 | MEDIUM | 删除/修正 spec 中幻影 T042b/T044 引用 | spec.md | 原则 VI |
| F-6 | LOW | 修正 plan.md 中 `services/scheduler.py`、`/report@cli.py` 路径描述 | plan.md | 原则 VI |
| 追踪 | — | T003–T010 按实情逐条标注（done/部分/休眠/偏差） | tasks.md | 原则 V |

---

## 4. Phase 0：Research / 裁决（先行，阻断后续）

**产出**：在 `research.md` 追加"Remediation Decisions"小节 + `DECISION_LOG.md` 新条目。

1. **NC-1 升级阈值（以 SOUL.md 为唯一真相，产品/Luna 拍板）**。推荐默认（接地 [SOUL.md:122-124](../../SOUL.md#L122-L124) / [156-160](../../SOUL.md#L156-L160)）：
   - **标准用户**：`NORMAL: day 0-2` · `CONCERN: day 3-6`（"连续几天"）· `EXTERNAL_SUPPORT: day ≥7`（"一周"）。
   - **弱势用户**（触点 1/3/5）：`NORMAL: day 0` · `CONCERN: day 1-4` · `EXTERNAL_SUPPORT: day ≥5`。
   - 理由：现 spec/data-model/code 的标准 EXTERNAL=day5 与 SOUL 的"一周"冲突（N2），代码弱势 EXTERNAL=day3 与 SOUL 的 day5 冲突（N1）；二者都必须改向 SOUL。
   - 落地：**同步改写 spec US3 AS2+AS3、data-model.md 阈值表、`EscalationState.derive` 四处**，并在 `DECISION_LOG.md` 记录"阈值以 SOUL.md 为准"的裁决。
2. **NC-2 push 文案**：裁定"为 push 单独渲染 companion 文案"，不改 `synthesize_state`（保 prefetch/D034 语义纯净）。
3. **NC-3 `/report` 边界**：裁定保留 Hermes 提示词路由为 UX 入口，但 CGM 层补一条**确定性工具/路径**直出 F3（不经 LLM 决策），作为 FR-011 的硬保证。

**Gate**：三项裁决落 `DECISION_LOG.md`（新 Dxxx）后方可进入 Phase 1。

---

## 5. 分阶段修复任务（Test-First，每项先写失败测试）

### Phase 1 — 核心叙事接线（F-1，CRITICAL）
- **R001 [test]** 写失败测试：weekly 报告含 L3 假设（CANDIDATE/OBSERVING/STABLE/ARCHIVED 各一）时，`patterns` 段输出对应 SOUL.md 话术（对照 [narrative-contracts.md](./contracts/narrative-contracts.md)）。
- **R002** 在 `_patterns_section`（或新 `_hypothesis_narrative_section`）按 `list_hypotheses` 的 `state`+`evidence_count` 调 `render_hypothesis_narrative`，替换/补充裸 `signal.summaries`。
- **R003** 红区回归：红区时假设叙事整段抑制（守 FR-009）。
- **完成标准**：US2 AS1-3、SC-003 在真实报告输出可验证；无死代码 import。

### Phase 2 — Push 合规（F-3，HIGH）
- **R010 [test]** 失败测试：daily push 内容不含 `TIR/TAR/TBR/GMI/CV/LBGI/HBGI`，长度 ≤100，且通过 `validate_companion_text`。
- **R011** 新增 push companion 渲染器（NC-2）：把摘要指标经 `translate_metric` 转生活语言，组装 ≤100 字文案。
- **R012** `_emit` 在 `send_os_push` 前调用渲染器+`validate_companion_text(max_len=100)`；badge 兜底路径同样投递校验后的文案。
- **完成标准**：FR-005/007/010 在 push 路径硬生效；SC-002 即时性测试保持。

### Phase 3 — 升级数据闭环（F-2 + F-5，HIGH）
- **R020 [test]** 失败测试：模拟 1–7 天异常（经 analytics/events，不预置 push_events），断言标准用户 day1 NORMAL/day3 CONCERN/day5 EXTERNAL_SUPPORT；弱势用户按 NC-1 裁决值。
- **R021** 重写 `consecutive_anomaly_days`：直接用 `CGMAnalyticsService`/`GlucoseEventDetector` 按日判定异常（TAR/TBR>0 或异常事件），不依赖 `memory_summaries.tar_pct/tbr_pct`。
- **R022** 按 NC-1 同步 `EscalationState.derive` + spec US3 AS3 + data-model.md 阈值表。
- **R023** 红区回归：红区时升级关心整段抑制（守 FR-009）。
- **完成标准**：US3 AS1-3、SC-004 在 push 路径与 `reports.generate` 路径都成立。

### Phase 4 — 边界与可达性（F-4 + F-9，MEDIUM）
- **R030 [test]** `/report` 直出 F3：断言绕过 F4 叙事、输出纯临床卡片（确定性，不经 LLM）。
- **R031** 实现 NC-3 裁定的确定性 `/report` 路径（cli.py 或工具），与 provider 提示词路由并存。
- **R032** F-9：补 `vulnerable_disclaimer_acknowledged` 的写入路径（用户输入"已知晓"→置位），或在 spec/tasks 明确标注为"生产休眠 KNOWN GAP（依赖上游写 `vulnerable_population`）"并加测试夹具注入用例。
- **完成标准**：FR-011 有硬保证；FR-008 路径要么可用要么显式休眠且有测试。

### Phase 5 — 健壮性（F-8，MEDIUM）
- **R040 [test]** 超长/含缩写的 companion 文案：runtime 下报告**不抛异常**（降级为截断+审计日志），同时 CI 守护测试断言模板本身合规。
- **R041** `validate_companion_text` 拆为：(a) 纯函数返回违规明细；(b) builder runtime 调用走"记录+sanitize/截断"；(c) 测试层用严格 raise 版守护模板。
- **完成标准**：叙事问题不再炸毁报告生成（FR-013：叙事仅渲染层）。

### Phase 6 — 文档/任务对齐（F-6 + F-7 + 追踪，MEDIUM/LOW）
- **R050** 修 [tasks.md](./tasks.md)：T003/T004/T006/T007/T008a 标 `[x]`；T005/T008 标"部分/休眠 + 引用本计划 R0xx"；T009 标"偏差 → R031"；新增 R001…R051 任务行。
- **R051** 删/修 spec.md 中 `T042b`/`T044` 幻影引用（F-7）；修 plan.md `services/scheduler.py`→`services/scheduling/scheduler.py`、`/report@cli.py`→实际落点（F-6）。

### Phase 7 — 回归门禁（Principle V）
- **R060** 全量 `python -m unittest discover -s tests`（或 pytest）≥374 绿、0 回归；新增用例计数 ≥15（SC-006）。**未运行前不得宣称完成**。

---

## 6. 排序、依赖与并行

```
Phase 0 (裁决 NC-1/2/3)  ──►  必须最先，阻断一切
   ├─► Phase 1 (F-1 假设叙事)        ┐
   ├─► Phase 2 (F-3 push 合规)        ├─ 目录基本不相交，可并行
   └─► Phase 3 (F-2/F-5 升级闭环)     ┘   （builder vs scheduler；F-5 需 Phase0 裁决）
Phase 4 (F-4/F-9) 依赖 Phase 0 NC-3
Phase 5 (F-8) 独立，建议在 Phase 1 之后（叙事接线会放大崩溃面）
Phase 6 (文档/任务) 在 1–5 落地后统一收敛
Phase 7 (回归) 最后
```

- **文件争用**：`builder.py` 被 Phase 1/4/5 同时碰 → 这三阶段串行或同一代理处理，避免 merge 冲突。`scheduler.py` 被 Phase 2/3 碰 → 同上。
- **人审强制（医疗项目克制）**：F-2/F-3/F-5 触及安全/医学语气与升级节奏 → Damocles（原则 I/III）+ Luna（原则 IV）合并前签核，不盲跑。

---

## 7. 测试策略汇总（新增用例，对齐 SC-006 ≥15）

| 用例 | 覆盖 | 类型 |
|---|---|---|
| 4× 假设状态话术（R001） | FR-004/SC-003 | unit(builder) |
| 红区抑制假设叙事（R003） | FR-009/SC-005 | integration |
| push 无缩写+≤100+校验（R010） | FR-005/007/010 | unit(scheduler) |
| 1/3/5 天升级（标准）（R020） | FR-006/SC-004 | integration |
| 弱势阈值（NC-1 值）（R020） | FR-006/SC-004 | integration |
| 红区抑制升级关心（R023） | FR-009/SC-005 | integration |
| `consecutive_anomaly_days` 不依赖 push_events（R020） | F-2 | unit |
| `/report` 确定性直出 F3（R030） | FR-011 | integration |
| 免责声明 ack 写入/休眠（R032） | FR-008 | unit |
| 超长 companion 不崩溃（R040） | FR-013 | unit |
| track 隔离在叙事路径仍生效 | 原则 II | guard |

---

## 8. 验证与回滚

- **验证**：Phase 7 全绿 + `analyze` 复跑 clean + 三条裁决入 `DECISION_LOG.md`。
- **回滚**：所有修复均为渲染/调度层增量，无 schema 变更（`pending_interactions`/`unread_badges` 表已存在）；按 Phase 粒度可独立 revert。
- **不做**：不引入外部模板引擎/新依赖（保持 plan.md "code-embedded templates" 约束）；不动 `~/.hermes` 安装树；不改既有 `synthesize_state` 的 prefetch 语义。

---

## 9. Next Steps

1. 先就 **NC-1/NC-2/NC-3** 走 `/speckit-clarify`（或人审直接拍板），写入 `DECISION_LOG.md`。
2. 按 Phase 1→7 执行（建议每 Phase 一个 review gate）。
3. 收尾跑 `/speckit-analyze` 复核 spec/plan/tasks 与代码再次一致。

---

## 10. /analyze 自审发现与修订（2026-06-10）

对本计划自身做了一次 `/speckit-analyze`（对照 spec / data-model / constitution / SOUL.md / 代码）。12 项发现及解法如下；标注 **(已就地修订)** 的已直接改入上文对应章节。

### CRITICAL / HIGH
- **N1（CRITICAL，原则 IV）(已就地修订 §1 NC-1 + §5 Phase 0.1)**：原推荐默认"弱势 EXTERNAL at day≥3"**违反 SOUL.md**（弱势触点 1/3/5 → EXTERNAL at **day5**）。SOUL.md 是原则 IV 权威源。**解法**：阈值一律以 SOUL.md 为准，见修订后的 Phase 0.1。
- **N2（HIGH，Inconsistency）(已就地修订 §1 NC-1)**：原审计 F-5 只指出弱势侧矛盾，**漏了标准侧**——spec US3 AS2 / data-model / code 的标准 EXTERNAL=**day5**，而 SOUL = **一周(~day7)**。**解法**：NC-1 同时修标准侧（5→7），四处对齐。
- **N3（HIGH，原则 VII 边界）(已就地修订 §1 NC-3 + 见下)**：原 R031"确定性 `/report` 落 cli.py"不可行——`cli.py` 是 argparse 开发 CLI，聊天 `/report` 须 Hermes 路由，不得改 Hermes。**解法**：**重述 FR-011**——确定性 = `reports.generate` 工具内部直出纯 F3（工具内不经 LLM）；路由保持提示词级。R030/R031 相应改为"工具确定性 + provider 提示词路由"两件事；F-4 归类为"重述+工具保证"，非 cli 命令缺陷。
- **N4（HIGH，Contradiction）**：F-8 把"超长"与"含临床缩写"两类违规混用一个"记录+截断"解法——截断**治不了缩写泄漏**，且 raise→不 raise 会削弱 FR-005 强制门。**解法（替换 §5 Phase 5 R040/R041）**：
  - **黑名单命中**（TIR/TAR/… 或断言式短语）→ **硬阻断**：拒绝发出该文案，回退到不含缩写的安全模板，并审计；这是原则 IV 的硬门，不降级。
  - **仅超长** → 记录审计 + 截断/重排到限长，不抛异常（避免炸毁报告，守 FR-013）。
  - `validate_companion_text` 拆为：(a) 纯函数返回 `violations` 明细（含类型）；(b) builder/scheduler runtime 按类型分流（阻断 vs 截断）；(c) 测试层用 strict-raise 守护**模板自身**合规。

### MEDIUM
- **N5（Coverage Gap）**：无任务保证 F-1 接线**保留 FR-013**（evidence_refs / source_tracks / confidence / data_quality_warnings）。**解法**：在 Phase 1 增 **R004 [test]**——断言接入假设叙事后 `patterns` 段的上述结构不变。
- **N6（Underspecification）**：R021 异常日定义丢了 data-model 的"or warnings present"，且未定义日界/时区。**解法**：异常日 = `TAR>0 或 TBR>0 或 存在非 DATA_GAP 异常事件 或 data_quality warning`；按 `scheduler._tz` 切日界；与 [data-model.md ConsecutiveAnomalyDays](./data-model.md) 对齐。
- **N7（Inconsistency）(已就地修订：新增 §1 NC-4)**：F-9 的"实现 ack vs 标 KNOWN GAP"是隐藏决策。**解法**：提升为 **NC-4**，Phase 0 裁决；R032 依裁决二选一执行。
- **N8（Inconsistency）**：§7 声称"≥15"(SC-006) 但仅列 11 行。**解法**：把多状态行展开计数（4 假设状态 = 4；标准 1/3/5 + 弱势 1/3/5 = 6 个升级用例；push 合规含"无缩写/≤100/校验"= 3）后实际 ≥18；§7 表改为按用例计数并补足列举，确保 ≥15。
- **N9（Ordering）**：Phase 2 R012 按"raise"语义调用校验，Phase 5 R041 改其契约 → API 依赖未排序。**解法**：**Phase 5 的 `validate_companion_text` 契约重构先于（或同 PR 于）Phase 2 的调用点**；Phase 2 直接消费新 API（纯函数 + 分流），避免返工。排序更新：`Phase 0 → Phase 5(契约) → Phase 1 ∥ Phase 2 ∥ Phase 3 → Phase 4 → Phase 6 → Phase 7`。

### LOW
- **N10（Inconsistency）**：§5 Phase 6 文案"R001…R051"漏 R060，R0xx 不连贯。**解法**：统一任务号 **R001–R060（含新增 R004）**，R050 插入 tasks.md 时列全。
- **N11（Process，原则 V/工作流）**：当前在 `master` 上，违反 spec-kit 分支约定（`speckit-git-validate` 期望 `003-companion-narrative`）。**解法**：R-任务开工前先建/切 `003-companion-narrative` 分支。
- **N12（Robustness）**：`validate_companion_text` 的 `"CV" in text_upper`（[narrative_templates.py:24-28](../../src/hermes_cgm_agent/services/reports/narrative_templates.py#L24-L28)）按子串匹配，会误伤任何含 "cv" 的文本。**解法**：缩写校验收紧为词边界/token 匹配（正则 `\bCV\b` 或按词切分），并入 N4 的纯函数重构。

### 由发现新增/调整的任务（并入 §5）
- **R004 [test]**（Phase 1）：F-1 接线保留 FR-013 结构回归（来自 N5）。
- **R041 改写**（Phase 5）：校验拆分 = 黑名单硬阻断 + 超长截断 + 词边界匹配（来自 N4、N12）。
- **R021 细化**（Phase 3）：异常日定义 + 时区日界对齐 data-model（来自 N6）。
- **R030/R031 改写**（Phase 4）：工具确定性直出 F3 + 提示词路由；FR-011 重述（来自 N3）。
- **Phase 0 增 NC-4 裁决**（来自 N7）；**Phase -1**：建 feature 分支（来自 N11）。
- **§7 测试表**：按用例计数补足 ≥15（来自 N8）。

> 复评：以上修订不引入新架构、不改 schema，仍满足 §2 Constitution Check（GREEN）。N1/N3/N4 直接强化原则 IV / VII，应在 Phase 0 裁决时一并确认。
