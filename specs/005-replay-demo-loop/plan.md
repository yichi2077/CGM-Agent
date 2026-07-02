# Implementation Plan: Replay Demo Loop (F-005)

**Branch**: `claude/glucose-system-review-qrmw6e` · **Spec**: [spec.md](./spec.md) · **Status**: In progress

## Technical Context

- **Language**: Python 3.11 · stdlib-only new code (urllib/sqlite3/argparse)；无新依赖。
- **复用资产**：`examples/cgm_test_dataset/cgm_3x14.csv`（42 天确定性合成数据）；`services/data/{importer,normalizer}.py`；`services/scheduling/scheduler.py`（只读复用 `push_tick(now=)` 注入）；`services/rag/eval_hit3.py`（评测 harness 模式）；`services/tools/executor.py`（工具路由）；Dexcom CLI→executor 路由模式。

## Constitution Check

| 原则 | 评估 |
|---|---|
| I. Medical Zero-Tolerance | ✅ 投递诚实（email 不谎报）；回放不生成/改写任何指标（仍由 analytics 确定性计算） |
| II. Dual-Track Isolation | ✅ 不触碰 `assert_track_isolation` |
| III. Hard-Coded Safety Routing | ✅ 三区路由不变；回放数据仍过安全层 |
| IV. Informed-Companion Persona | ✅ 推送 `content` 仍过 `enforce_companion_text` |
| V. Test-First & Green CI | ✅ 每阶段先写失败测试，全套保持绿 |
| VI. Traceable Decisions | ✅ D050–D053 入 DECISION_LOG |
| VII. Hermes Boundary | ✅ 回放 CLI-only；微信发送在 Hermes；不改安装树；调度器不动 |

**Gate：GREEN**。无未计划架构引入；无新工具（漂移守卫不受影响）。

## 分阶段设计

### Phase 0 — 工程债（先落地）
- 日期脆弱测试：`tests/test_memory_integration.py` 的 prefetch 测试改相对 now 播种（不改生产代码——锚定真实 now 是正确行为）。
- skip guard：`tests/test_hermes_plugin_integration.py::setUpClass` 加 `find_spec("agent") is None → SkipTest`。
- email 冻结（D050）：注释标 KNOWN GAP + pinning 测试。

### Phase 1 — 推送→投递桥（D052）
- `services/tools/handlers/delivery.py` 新增 `_link_push_event(user_id, push_id, delivery_id)`，local_file/webhook `sent` 后调用。
- `docs/RUNBOOK-wechat-push.md`：cron 注册 + 逐字提示词 + 验证。

### Phase 2 — 回放引擎（D051）
- 新增 `services/replay/{__init__,engine}.py`：`ReplayConfig`/`ReplayReport`/`ReplayService`。
- 算法：归一化→（可选平移末端到 now）→全量入库（UNIQUE 去重）→逐模拟日经 executor 触发 `scheduling.push_tick(now=sim)`→可选 `delivery.send`。
- CLI `replay` 子命令；README 用法。

### Phase 3 — 记忆有效性评测（D053）
- `eval/memory/queries.jsonl` 扩到 ~20 条；新增 `eval/memory/fixture.jsonl`（相对 now 播种）。
- `services/memory/eval_recall.py`：`seed_fixture` + `evaluate_memory_recall`（双库对照，命中率）。
- CLI `eval-memory --min-recall --report`；`eval/README.md` 更新。

## 提交顺序（每步全绿）
1. Phase 0 + Phase 1（+D050/D052 + runbook）
2. specs/005 工件
3. Phase 2 回放（+D051）
4. Phase 3 评测（+D053）+ README

## 验证
1. `PYTHONPATH=src python3 -m unittest discover -s tests` 全绿。
2. `python -m hermes_cgm_agent replay --dataset examples/cgm_test_dataset/cgm_3x14.csv --user-id demo --deliver`。
3. `python -m hermes_cgm_agent eval-memory --min-recall 0.8 --report eval/memory/report-latest.md`。
