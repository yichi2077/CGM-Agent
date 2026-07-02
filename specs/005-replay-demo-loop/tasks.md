# Tasks: Replay Demo Loop (F-005)

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

状态图例：`[ ]` 未做 · `[X]` 已完成并测试绿

## Phase 0 — 工程债

- [X] T001 修复 `test_provider_prefetch_includes_warm_and_l0_summaries` 相对 now 播种（仅测试层，不改生产代码）。范围说明：其余 ~129 处固定 2026 日期中，传显式 `now=`/`anchor_at`/`data_scope` 的**不腐烂、不在范围内**（如 `test_report_with_retrieve_context_injects_but_keeps_facts` 传固定 `data_scope` 窗口，自洽）。
- [X] T002 `test_hermes_plugin_integration.py::setUpClass` 加 `find_spec("agent") is None → SkipTest`。
- [X] T003 email 冻结（D050）：`delivery.py` 注释标 KNOWN GAP。
- [X] T004 `tests/test_delivery_channels.py::EmailFreezeTests` pinning 测试（email→queued、无副作用、不建 deliveries 目录）。
- [X] T005 DECISION_LOG 写 D050。

## Phase 1 — 推送→投递桥（D052）

- [X] T006 `delivery.py` 新增 `_link_push_event`（`UPDATE push_events ... WHERE ... AND delivery_id IS NULL`）。
- [X] T007 local_file `sent` 后调用 `_link_push_event`。
- [X] T008 webhook `sent` 后调用 `_link_push_event`。
- [X] T009 `tests/test_delivery_channels.py` bridge 测试（local_file 回写 / 未知 payload_ref no-op / IS NULL 防覆盖 / webhook 回写）。
- [X] T010 `docs/RUNBOOK-wechat-push.md`（cron 注册 + 逐字提示词 + 验证）。
- [X] T011 DECISION_LOG 写 D052。

## Phase 2 — 回放引擎（D051）

- [ ] T012 `services/replay/__init__.py` + `engine.py`：`ReplayConfig` / `ReplayReport` / `ReplayService`。
- [ ] T013 `tests/test_replay_engine.py`：instant 14 天产出 daily+weekly（period_key 互异、content ≤100 字）。
- [ ] T014 幂等：重跑不产生重复 points/push_events。
- [ ] T015 `deliver=True` → delivery_id 全非空 + manifest 存在。
- [ ] T016 `align_end_to_now` 平移末端到 now±48h；`--days` 截取；时区日界用例。
- [ ] T017 CLI `replay` 子命令 + `_replay` 分发；打印 ReplayReport 摘要。
- [ ] T018 DECISION_LOG 写 D051；README 增 `replay` 用法。

## Phase 3 — 记忆有效性评测（D053）

- [ ] T019 `eval/memory/queries.jsonl` 扩到 ~20 条（L1/L2/warm，中英混合）。
- [ ] T020 `eval/memory/fixture.jsonl`（相对 now 播种种子语料）。
- [ ] T021 `services/memory/eval_recall.py`：`seed_fixture` + `evaluate_memory_recall`（双库对照命中率 + Markdown 报告）。
- [ ] T022 `tests/test_eval_memory.py`：fixture 播种 / 有记忆命中·无记忆不命中 / 门禁失败 / 报告渲染。
- [ ] T023 CLI `eval-memory --queries --fixture --min-recall --report` + `_eval_memory` 分发。
- [ ] T024 `eval/memory/report-latest.md` 证据报告；`eval/README.md` 增"Personal Memory Recall"节。
- [ ] T025 DECISION_LOG 写 D053；README 增 `eval-memory` 用法。

## 验证

- [ ] T026 `PYTHONPATH=src python3 -m unittest discover -s tests` 全绿（≥462 + 新增，Hermes-venv 外仅既有 2 skip）。
- [ ] T027 CLI 手动验证：`replay --deliver`（delivery_id 回写 + manifest）+ `eval-memory`（证据报告 delta>0）。
