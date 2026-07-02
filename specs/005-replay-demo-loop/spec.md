# Feature Specification: Replay Demo Loop (F-005)

**Feature Branch**: `claude/glucose-system-review-qrmw6e`

**Created**: 2026-07-02

**Status**: In progress

**Input**: 2026-07-02 全项目评审结论——能力层完成度 ~85% 但产品环未闭合：数据只能手动 CSV 进入、`push_tick` 产出从未真正投递（`push_events.delivery_id` 恒 None）、核心价值点"长期记忆"无有效性证据。用户裁决：① 数据入口用**回放模拟器优先**（真实数据源推迟）；② 推送最后一公里依托 **Hermes 微信入口**；③ 范围含**记忆有效性评测** + **工程债修复**。

## Overview

本特性把系统从"能力齐备但环未闭合"推进到"MVP demo 可端到端演示"。不引入真实 CGM 数据源（推迟），而是补齐四块让闭环可跑、可证、可演示：

1. **工程债（Phase 0）**：修复随时间腐烂的日期脆弱测试、补 Hermes-venv skip guard、裁决 email 通道（冻结）。
2. **推送→投递桥（Phase 1）**：闭合 F5 last-mile 审计断链——投递成功后回写 `push_events.delivery_id`；微信发送经 Hermes（原则 VII）。
3. **回放引擎（Phase 2）**：把历史数据集加速回放，驱动 点→L0/consolidation→推送调度→投递 全链路，用模拟时钟。无传感器也能演示闭环。
4. **记忆有效性评测（Phase 3）**：with/without-memory 的确定性上下文召回评测，产出核心价值点的第一份证据。

## Clarifications

### Session 2026-07-02

- Q: 数据入口选哪个方案？ → A: **回放模拟器优先**。真实数据源（Nightscout/Libre）本轮推迟，另出 ADR。
- Q: 推送最后一公里用什么通道？ → A: **依托 Hermes 的微信入口**。能力层不接触微信 API/凭证，只产出策略/内容/状态并回写投递审计；微信发送在 Hermes 侧（原则 VII）。
- Q: 回放引擎是否暴露为 Hermes 工具？ → A: **否，CLI-only**。回放会操纵模拟时钟，暴露给模型等于交出时钟策略面，违背 D048"now 仅供测试/回放"。保留为 dev/demo CLI 表面。
- Q: 记忆评测 v1 是否用 LLM 评判答案质量？ → A: **否**。v1 确定性度量**上下文召回**（expected_terms 在注入上下文中的命中率），可进 CI、零成本。LLM 评答案质量记 KNOWN GAP。
- Q: email 通道删还是冻结？ → A: **冻结**。保留 enum + queued stub，加 pinning 测试，不删除（避免破坏 Hermes 已见的 channel 契约）。
- Q: 推送→投递桥怎么接，改不改调度器？ → A: **不改 `PushSchedulerService`**。在 delivery 侧回写 `delivery_id`（`payload_ref`=push_id，`IS NULL` 幂等）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 — 无传感器也能演示完整闭环 (Priority: P1)

演示者运行 `replay --dataset ... --deliver`，系统把 14 天历史数据加速回放：逐模拟日触发 `push_tick`，产出 daily/weekly 陪伴推送并投递，`push_events.delivery_id` 被回写。演示者随后在真实对话中提问，记忆/prefetch 能召回回放期形成的画像与假设。

**验收**：
- AS1：instant 回放 14 天 → 产出 ≥1 daily 与 ≥1 weekly 推送，`content` 非空且 ≤100 字。
- AS2：`--deliver` → 每个 pushed 项在 `push_events` 有非空 `delivery_id`，且 `<db_dir>/deliveries/` 有对应 manifest。
- AS3：重跑同一回放**幂等**——不产生重复 points/push_events。
- AS4：`align_end_to_now` 使数据集末端落在 now±48h，回放后真实对话的 L0 窗口能看到数据。

### User Story 2 — 推送真正投递并留痕 (Priority: P1)

`push_tick` 产出推送后，投递（local_file/webhook）成功即回写 `push_events.delivery_id`；Hermes cron 按 runbook 把 `content` 逐字转发微信。

**验收**：
- AS1：local_file/webhook 投递达到 `sent` 且 `payload_ref` 命中真实 push 行 → `delivery_id` 回写。
- AS2：`payload_ref` 不命中任何 push 行 → 投递仍成功，无回写、无错误。
- AS3：已链接的 push 行不被二次投递覆盖（`IS NULL` 守卫）。
- AS4：email → `queued`，无副作用，不谎报送达。

### User Story 3 — 长期记忆有效性有据可证 (Priority: P2)

运行 `eval-memory` 对 ~20 条需个体历史才能答对的查询，对比"有记忆库"与"空库"两种 prefetch 的上下文召回。

**验收**：
- AS1：有记忆库的 mean recall 显著高于空库（delta > 0）。
- AS2：`--min-recall` 阈值门禁：低于阈值 exit 1（可进 CI）。
- AS3：产出 Markdown 证据报告（逐查询表 + 聚合）。

### Edge Cases

- 回放数据集某模拟日 weekly/monthly 触发时 daily 被 1/日限流抑制（预期，报告如实记录只 pushed 的项）。
- 时区日界：23:50 Asia/Shanghai 的点落入正确 `period_key`。
- 空 `pushed`（限流/未过阈值）→ 静默，非错误。

## Requirements *(mandatory)*

- **FR-001**：回放引擎经 `executor.execute("scheduling.push_tick", {now})` 走真实工具路径（审计对等），不旁路。
- **FR-002**：回放为 CLI-only，**不**注册为 Hermes 工具。
- **FR-003**：投递成功回写 `push_events.delivery_id`，`payload_ref`=push_id，`IS NULL` 幂等，不匹配则静默 no-op。
- **FR-004**：email 冻结为 KNOWN GAP，行为 `queued` 无副作用。
- **FR-005**：记忆评测确定性、无 LLM、可 CI 门禁。
- **FR-006**：不改 `PushSchedulerService`；不改 Hermes 安装树（原则 VII）。
- **FR-007**：所有新测试先行（原则 V），全套保持绿。
- **FR-008**：新决策入 DECISION_LOG（D050–D053，原则 VI）。

## Success Criteria *(mandatory)*

- **SC-001**：`replay --deliver` 端到端产出推送 + 回写 delivery_id + manifest 落地。
- **SC-002**：`eval-memory` 产出 with>without 的证据报告，delta > 0。
- **SC-003**：全套测试绿（≥462 + 新增），Hermes-venv 外仅既有 2 skip。
- **SC-004**：日期脆弱测试在任意运行日期稳定通过。
