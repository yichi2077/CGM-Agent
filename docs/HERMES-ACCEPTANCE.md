# Hermes CGM 本地服务全链路验收

`hermes-accept` 是 CGM capability layer 的验收控制面，不是第二个聊天引擎。它把 Hermes 当作真实 shell，把本地 SQLite 事实库当作唯一 oracle，并在受保护的 run 目录中保存可复核的证据。

## 两个副本

- `retrieval-copy` 保留现有 L0-L3、Warm、报告和投递状态，用来验证 Hermes `cgm_memory` 的 prefetch、跨会话召回和 personal/authoritative 双轨隔离。
- `rebuild-copy` 只保留 CGM 事实；验收清除派生记忆、候选、Warm、报告、投递、审计和 pending interaction 表，再按模拟日期重建。第二次重放必须保持计数不变。

## 硬门

1. 选出的窗口必须包含连续三天同类非 `data_gap` 事件；不会为了通过而降低 L2/L3 晋升阈值。
2. L0、L1、L2、L3、Warm、prefetch 和会话候选检查全部由数据库/服务 oracle 判定。
3. 六个 RAG 主题执行本地检索、严格 quote verification 和双轨隔离检查。
4. 真实 Hermes 运行先配置并检查 `cgm_memory`，再执行 provider smoke；403、工具加载失败或模型非零退出立即停止，禁止静默换模型。
5. 24 个场景（6 memory、6 RAG、8 中文 SELF 陪伴、4 反例）受 30 次模型调用上限保护；工具审计必须出现，答案不得泄露内部层级/工具/JSON/user id，带单位的数字必须能在确定性 oracle 或检索卡中找到。
6. 三个模拟日期生成日报，周期 tick 和事件选择来自真实事件 oracle；相同 period/event 的重放不能新增 push。
7. 只有所有隔离硬门通过且明确传入 `--activate-on-pass` 时，才允许修改默认 Hermes profile。切换前备份 config、`.env`、cron、DB 和 storage key；任一切换后检查失败即恢复并重启 gateway。

验收子进程会在工具边界强制统一模拟时间锚点：对模型生成的
`window_start/window_end` 保留原始时长、把结束点对齐到窗口结束，把
`now`/`anchor_at` 对齐到同一时刻。这样本地时区、UTC 和宿主机当前时间
不会让实时工具读到验收窗口之外的数据。

## 工件

每次 run 目录包含 `manifest.json`、`timeline.jsonl`、`links.json`、`scenario-manifest.json`、本地完整 `scenario-results.json`、去回答内容的 `public-scenario-results.json`、`final-report.json` 和 `final-report.md`。manifest 只记录代码/Hermes/config 哈希和路径，不记录密钥。sidecar links 用 `run_id` 关联 Hermes session、tool audit、report 和 push id，不写入生产业务表。

## 默认运行 jobs

切换后的 jobs 使用 Windows Python 脚本：09:00 中文个人陪伴日报、每 30 分钟的幂等事件监视器，以及每 2 小时只在失败时输出的 health check。旧的 CGM jobs 只暂停、不删除。真实 Weixin 投递必须显式 `--send-external`，使用已有 private target，每条消息含 `[CGM模拟验收]`、模拟日期和 run/correlation id，总数最多六条。

## Acceptance delivery execution

When `--send-external` is enabled, the cutover manager receives the three
dates and the oracle-confirmed event list from `final-report.json`. It creates
three date-specific and at most two event-specific jobs, each with a unique
`run_id`/`correlation_id`, `--repeat 1`, and the simulated-message prefix. Each
job is then invoked with `hermes cron run` and must report `Ran now: succeeded`;
the remaining sixth budget slot is reserved for the default-profile canary.
Any missing job record, execution failure, delivery error, or failed profile
check aborts the cutover and restores the backup bundle.
