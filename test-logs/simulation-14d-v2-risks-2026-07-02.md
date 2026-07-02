# Simulation v2 — 未确定事项与风险登记册
## 2026-07-02

**测试 ID:** demo-prediabetes-14d-v2
**数据集:** cgm_14d_1min.csv (SHA256: 7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc)
**状态:** 活跃记录 — 持续更新

---

## 目录

1. [P0] 双重写入竞争：auto_poll vs cron simulation_tick
2. [P0] auto_poll 实际 tick 存活确认
3. [P0] DB 数据加密密钥持久性
4. [P1] 事件检测管道未验证
5. [P1] behavior_events_14d.json 未导入
6. [P1] Cron 脚本运行时环境假设
7. [P2] KB 引用核验状态与 i18n
8. [P2] realtime_snapshot 永远 stale
9. [P2] 最终报告指标 vs manifest reference 收敛性
10. [P3] v2 CSV 时间偏移量验证

---

## 1. [P0] 双重写入竞争：auto_poll vs cron simulation_tick

**发现时间:** 2026-07-02 02:36 UTC
**触发动作:** 部署 cron `cgm-14d-simulation-tick` 作为持久化备份

### 问题描述

两个独立进程同时向同一个 `glucose_points` 表写入，各自使用不同的索引策略确定下一个写入点：

```
路径 A (当前活跃)：
  virtual_cgm_feed.py (port 17580)
    └─ 内部维护 CSV 行指针，每 5 分钟前进 5 行（1-min→5-min聚合）
    └─ auto_poll.py 每 5 分钟 GET /sgv.json?count=1
    └─ 写入 DB

路径 B (cron 备份)：
  simulation_tick.py（每 5 分钟由 Hermes cron 触发）
    └─ 读取 SQLite: `SELECT COUNT(*) FROM glucose_points WHERE ...`
    └─ 以该计数作为 CSV 行索引
    └─ 启动临时 HTTP server → 服务该点 → 立即拉取 → 写入 DB
```

**冲突场景：**
- 两个进程几乎同时计算下一个点
- 都认为索引 N 是"下一个"
- auto_poll 先写入，cron 再写入 = 重复点（duplicate_count=0? duplicate 检测可能基于 batch/poll 而非 content）
- 或者 auto_poll 写入了一个点，cron 在 auto_poll 完成前读 count，拿到旧 count，落后一步 = 乱序写入

**当前状态:** 未测试过共存场景。cron job 已创建但尚未触发（last_run_at=null）。

### 验证步骤
1. 等待 cron 首次触发（约 02:36 UTC）
2. 检查当时 auto_poll 是否同步在写
3. 检查 DB 中是否有重复时间戳
4. 检查 `duplicate_count` 是否非零

### 缓解方案（待选）
a) 暂停 cron，只保留 auto_poll（推荐）
b) 暂停 auto_poll，只保留 cron
c) 修改 simulation_tick.py 加 DB 锁检测，跳过已存在的窗格
d) 使用 --db-path 指向不同数据库隔离路径

**决议：** 待定 — 建议选方案 (a)

---

## 2. [P0] auto_poll 实际 tick 存活确认

**发现时间:** 2026-07-02 02:29 UTC
**触发动作:** 启动 `auto_poll.py --duration-hours 336 --interval-min 5`

### 问题描述

auto_poll (PID 7652) 在前 3.5 分钟内只输出 poll_index=1。它是否真的在等待正确的 5 分钟间隔？可能的故障模式：

| 模式 | 表现 | 影响 |
|------|------|------|
| 正常工作 | 每 300 秒输出一次 | 14天后完成 |
| sleep 偏差 | interval-min=5 但实际 sleep 逻辑不同 | 时间轴偏移 |
| 进程挂起 | 无新输出，CPU=0 | 14天模拟未实际运行 |
| 进程退出 | 被系统杀掉/崩溃 | 管道中断 |

### 验证步骤
- 等 `~02:34:57 UTC`（首次 poll 后 ~5min）检查是否有 poll_index=2 输出
- 检查 PID 7652 是否仍存活
- 检查 `glucose_points` count 是否从 6 增至 7

**决议：** await next tick

---

## 3. [P0] DB 数据加密密钥持久性

**发现时间:** 2026-07-02 02:28 UTC
**触发动作:** SQLite 直接查询发现值存储为 `enc:v1:gAAAAA...`

### 问题描述

CGM Agent 使用 `enc:v1:` 前缀在应用层加密写入 `value_mg_dl` 字段。cgm_* 工具读取时解密。但以下场景未验证：

| 场景 | 风险 |
|------|------|
| Hermes Agent 重启 | 密钥重新生成？旧数据不可读 |
| 跨 profile 访问 | 不同 profile 有不同密钥空间 |
| cgm_* 工具调用时密钥未初始化 | 解密失败返回 `enc:v1:...` 原文 |
| 数据库迁移/备份恢复 | 密钥不随 DB 迁移 |

### 证据
```sql
-- encrypted values from SQLite direct query
2026-04-24T18:30:00+00:00: enc:v1:gAAAAA...1Q==
2026-04-24T18:25:00+00:00: enc:v1:gAAAAA...WA==
```

而通过 cgm_timeseries_get_points 读取正常返回明文（如 101.5 mg/dL），说明当前 session 的密钥上下文有效。

### 验证步骤
1. 定位加密密钥存储位置（`config.yaml`? `auth.json`? 环境变量?）
2. 测试重启后 cgm_timeseries_get_points 是否能解密旧数据
3. 记录密钥轮换策略

### 已知信息来源
- 加密发生在 `hermes_cgm_agent.storage.sqlite` 层
- 解密发生在 `hermes_cgm_agent.services.data.SQLiteCGMRepository`
- 密钥来源待查明

**决议：** 待定 — 需要源代码审计

---

## 4. [P1] 事件检测管道未验证

**发现时间:** 2026-07-02 02:28 UTC
**触发动作:** 所有 auto_poll 返回 `detected_event_count: 0`

### 问题描述

每次 source-poll 返回 `detected_event_inserted: 0`，即使 CSV 包含 artifact 窗口：

```json
// 每次 poll 结果
"detected_event_count": 0,
"detected_event_inserted": 0
```

数据集已知 artifact（来自 manifest.json）：
```json
{
  "type": "compression_low",
  "ts_start": "2026-05-01T03:11:00",
  "ts_end": "2026-05-01T03:38:00",
  "count": 28,
  "note": "compression_low"
},
{
  "type": "sensor_noise",
  "ts_start": "2026-05-06T16:02:00",
  "ts_end": "2026-05-06T16:16:00",
  "count": 15,
  "note": "sensor_noise"
}
```

### 未回答问题
- 事件检测是**在每个 poll 写入后同步触发**，还是**后台异步任务**？
- 触发条件是什么？（value threshold? 趋势突变? artifact 标签?）
- CSV 中的 `artifact` 列是否被 source-poll 解析并传递？
- 需要什么级别的数据量才能触发检测？
- 如果检测算法需要积累特定窗口数据，单个 5min 点不够触发

### 验证步骤
1. 手动构造带 artifact 标记的测试窗口
2. 或直接调用 `cgm_events_create` 对比 pipeline 自动检测 vs 手动注入
3. 查阅 `hermes_cgm_agent.services.detection` 源码

**决议：** 待定 — 需要事件检测器架构文档或源代码分析

---

## 5. [P1] behavior_events_14d.json 未导入

**发现时间:** 2026-07-02 02:30 UTC（生成 daily report 时发现"生活事件"空窗）
**触发动作:** `cgm_reports_generate` → 所有 report 的 events section 为空

### 问题描述

数据集目录包含 `behavior_events_14d.json`（manifest 中引用为 `events_json`），包含驱动血糖曲线的行为事件：早餐/午餐/晚餐、餐后步行、压力窗口、睡眠质量等。但这些事件从未导入 `user_events` 表。

**后果链：**
```
缺少行为事件
  → report 中 "生活事件" section 为空
  → 血糖波动无法归因（meal spike vs stress vs unknown）
  → L0 context 缺乏外部时间锚点
  → 观察/推断 section 置信度受限（当前 confidence=0.55）
```

### 未回答问题
- behavior_events_14d.json 的预期导入时机是什么？
  - 测试前一次性导入？
  - 随 feed 同步按时间戳实时注入？
  - 等待 event detector 自动识别？
- 导入工具有吗？还是需要手动 SQL INSERT？
- 事件 timezone 应该是 UTC 还是 Asia/Shanghai？
- 导入后需要 events_confirm 吗，还是直接作为 confirmed event？

### 可能的导入方式
```bash
# 未知——需要确认是否存在 cli 命令
# 或直接 SQL 导入 user_events 表
# 或通过 cgm_events_create 逐条注入
```

**决议：** 待定 — 需要定义 behavior events 导入流程

---

## 6. [P1] Cron 脚本运行时环境假设

**发现时间:** 2026-07-02 02:36 UTC（创建 cron job 时）
**触发动作:** `cronjob(action='create', script='simulation_tick_14d.sh')`

### 问题描述

包装脚本 `~/.hermes/scripts/simulation_tick_14d.sh` 包含硬编码假设：

```bash
cd "E:/字幕组测试/CGM-Agent/hermes-cgm-agent-latest" || exit 1
exec python examples/cgm_test_dataset/simulation_tick.py \
  --user-id demo-prediabetes-14d-v2 --source virtual:aidex-v2 --count 1
```

**假设清单：**

| 假设 | 风险 | 验证方式 |
|------|------|---------|
| E: 盘符在 cron 运行时挂载 | 如果 cron 在未挂载时运行 → exit 1 | 检查磁盘可用性 |
| 中文字符路径（字幕组测试）在 MSYS/bash 下可访问 | 某些 MSYS 版本对 non-ASCII 路径处理不一致 | 手动执行脚本一次 |
| `python` 命令在 cron 的 PATH 中 | cron 可能使用精简 PATH | 在脚本中硬编码 `exec python3.12` |
| `.hermes/scripts/` 的执行权限 | 创建时 lint 跳过 .sh | 检查 +x 位 |
| simulation_tick.py 的所有 import（hermes_cgm_agent 等）可解析 | 当前 session 有 sys.path 改写，cron 没有 | 验证脚本可独立运行 |

### 验证步骤
1. 手动执行脚本：`bash ~/AppData/Local/hermes/scripts/simulation_tick_14d.sh`
2. 观察是否有 import error 或路径错误
3. 如果失败，修改脚本增加 `PYTHONPATH` 或 `set -x` 调试

**决议：** 待定 — 需要手动测试脚本独立运行

---

## 7. [P2] KB 引用核验状态与 i18n

**发现时间:** 2026-07-02 02:30 UTC（生成 daily report 时）
**触发动作:** `cgm_reports_generate` → observations section

### 问题描述

自动生成的 KB 引用标记 `[待核验/unverified]` 是半中文半英文。所有 KB cards 来自 `kb-2026-06-auto-v2` 组，由自动化流程生成，未经临床人工核验。

当前被引用的 KB cards：
```
kb-2026-06-auto-v2:auto-cds-2024-guideline-p18-cds2024-p18-012
  → "CSME review frequency: every 3 months [待核验/unverified]"
kb-2026-06-auto-v2:auto-ishne-2023-agp-p4-agp-daily-profile-shading
  → "Daily glucose profile shading conventions [待核验/unverified]"
kb-2026-06-auto-v2:auto-ispad-2024-glycemic-p4-ispad2024-p004-kidney...
  → "Higher albuminuria and retinopathy ... [待核验/unverified]"
```

### 未回答问题
- KB 核验是 **CGM Agent 内部需求** 还是 **下游临床产品需求**？
- 未核验 KB 是否应被 report generator **完全排除**（不仅是标记）？
- 标记语言（zh-CN "待核验" vs en-US "unverified"）是否应跟随 report 的 language 参数自动切换？
- 如果 KB 核验不是本轮测试的阻塞项，那它的验收标准是什么？
- KB 的发布时间/来源/证据等级是否应一并展示？

### 当前行为
```
PRETEST-FREEZE §KB Scope: "KB clinical verification is not a gate for this
simulation. Unverified cards may be used as test fixtures. Hermes must record
every place where an answer or report relies on unverified KB content."
```

合规：✅ done（报告正文在 observations 下标注了 "非医疗建议；以下医学参考仍待人工核验"）

**决议：** 不作为本轮阻塞

---

## 8. [P2] realtime_snapshot 永远 stale

**发现时间:** 2026-07-02 02:30 UTC
**触发动作:** `cgm_timeseries_get_realtime_snapshot`

### 问题描述

数据集时间锚在 2026-04-24 ~ 2026-05-08（14 天历史数据），当前系统时间是 2026-07-02。因此：

```json
{
  "stale_status": true,
  "missing_rate_1h": 100.0,
  "data_freshness_minutes": 98400.68
}
```

所有 "实时" 操作返回的数据新鲜度约为 98400 分钟（68 天滞后）。

### 影响
- `missing_rate_1h=100%`：触发报告中的 `low_coverage` 警告
- `stale_status=true`：可能触发下游告警或跳过某些自动化决策
- 差异始终显示 100% 缺失，无法测试 realtime snapshot 在"正常"状态下的行为

### 缓解方向
a) 生成时间戳接近当前日期的 CSV 副本（推荐）
b) 在测试环境中 mock 当前时间
c) 接受这是历史数据回放测试的固有特性

**决议：** 不做修复，记录为已知限制

---

## 9. [P2] 最终报告指标 vs manifest reference 收敛性

**发现时间:** 2026-07-02 02:30 UTC（生成首份 daily report 后对比）
**触发动作:** 对比 report metrics 与 manifest.json 的 reference metrics

### 问题描述

当前 report（6 点）vs manifest reference（20010 点）：

| 指标 | 当前 report (6pts) | manifest reference | 差异 |
|------|-------------------|-------------------|------|
| TIR | 100.0% | 98.28% | +1.72pp |
| TAR | 0.0% | 1.48% | -1.48pp |
| TBR | 0.0% | 0.24% | -0.24pp |
| MBG | 103.52 mg/dL | 125.4 mg/dL | -21.88 |
| CV | 1.65% | 16.5% | -14.85pp |
| GMI | 5.79 | — | N/A |

6 个点在稳态窗口（Flat 趋势，100-107 mg/dL），完全不代表全局分布。

### 关键未验证问题
- **14 天后** report 的 MBG/CV/TIR/TAR/TBR 是否收敛到 manifest reference？
- 如果偏差超过阈值（如 TIR 偏差 > 1%），是 report builder 的算法问题还是 CSV 解析差异？
- manifest 的 CV=16.5% 是否基于 1-min native 点计算？report 是否基于 5-min 聚合点？两者因采样频率不同本身有偏差。
- report 使用的 window 标签（14d）与 manifest 的 14 天 window 是否完全对齐？

**决议：** 待 14 天完成后验证，记录偏差

---

## 10. [P3] v2 CSV 时间偏移量验证

**发现时间:** 2026-07-02 02:28 UTC（发现 feed server 输出时间戳与 CSV 行不一一对应）
**触发动作:** 对比 CSV 首行时间和 feed 服务时间

### 问题描述

CSV 首行：
```csv
timestamp,value,unit,device_id,record_id,trend,status,artifact,event_ids
2026-04-25T02:00:00,104.7,mg/dL,VIRTUAL-AIDEX-X-001,VIRTUAL-AIDEX-X-001-000000,flat,,,
```

Feed 服务的第一点返回的时间是：
```json
{"dateString": "2026-04-24T18:00:00Z"}
```

即 CSV 说 02:00 UTC+8，但 feed 输出 18:00 UTC（= 04-25T02:00 UTC+8 不成立... 让我重新算）。

`2026-04-25T02:00:00`（CSV 中无时区标记）vs `2026-04-24T18:00:00Z`（feed 输出）= 8 小时偏移。
`2026-04-24T18:00:00Z` = `2026-04-25T02:00:00+08:00`。

所以 CSV 的时间是 **Asia/Shanghai 时区**，feed 输出是 **UTC**。时区转换是正确的。

不过，`T02:00:00` 是本地凌晨 2 点——在模拟 prediabetes 场景中，这表示传感器从凌晨开始。但用户是 prediabetes 日常监测，凌晨 2 点是睡眠时间，正常 BG。

没有实际 bug，但需要确认整个 14 天的时区处理是否一致。所有 report 使用 `timezone=Asia/Shanghai`，但如果中间有 UTC 混用，每天的窗口边界会偏移。

**决议：** 记录观察结果，不阻塞

---

## 11. [P1] 微信推送通道限速与可靠性

**发现时间:** 2026-07-02 02:43 UTC
**触发动作:** 首次尝试 `hermes send --to weixin`

### 问题描述

Hermes WeChat (Weixin) 通道通过 iLink bridge 连接。目标已配置：
```
weixin:o9cq80yxtMVOK0GbUpXeZ7dDrYQI@im.wechat (dm)
```

但推送存在以下问题：

| 问题 | 表现 | 影响 |
|------|------|------|
| 频率限制 | `iLink sendmessage rate limited; cooldown active for 30.0s` | 连续 3 次 (间隔 30-90s) 均被拒绝 |
| 冷却累积 | 每次重试重置冷却计时器 | 需要 ≥180s 安静期后重试 |
| 推送可靠性未验证 | 尚未成功投递任何消息到微信 | 无法确认端到端可达 |

### 当前措施
1. 后台挂起一次 180 秒冷却后的发送尝试（PID 8196）
2. 创建 cron job `cgm-daily-wechat-checkpoint`（每 6 小时推送 checkpoint，deliver=weixin）
3. 如果 cron 也因限速失败，需要调整发送频率或确认 iLink 限速阈值

### 验证步骤
1. 等待 PID 8196 的发送结果
2. 等待 cron 首次触发 (约 2026-07-02 08:46 UTC)
3. 如果均失败，检查 iLink 配置中的 rate_limit 参数

**决议：** 待确认 — 微信推送尚未成功投递

---

## 12. [P2] 测试中断恢复能力未验证

**发现时间:** 2026-07-02
**触发动作:** 测试审计

### 问题描述

如果当前 session 中断：
- auto_poll (PID 7652) 会被杀掉 → 14 天轮询停止
- virtual_cgm_feed (PID 20180) 会被杀掉 → 数据源停止
- cron job `cgm-14d-simulation-tick` 理论上可以恢复，但 **尚未验证 cron 脚本能独立工作**

中断恢复路径：
```bash
# 重启 feed
cd "E:/字幕组测试/CGM-Agent/hermes-cgm-agent-latest"
python examples/cgm_test_dataset/virtual_cgm_feed.py --emit-interval-min 5 &

# 重启 auto_poll（从 DB 已有索引继续）
python examples/cgm_test_dataset/auto_poll.py \
  --user-id demo-prediabetes-14d-v2 \
  --url http://127.0.0.1:17580 \
  --count 1 --interval-min 5 --duration-hours 336 \
  --source virtual:aidex-v2
```

或者依赖 cron job 恢复（如果 cron 环境的 simulation_tick.py 可独立工作）。

### 临时缓解
auto_poll 的 resume 机制依赖 feed server 的 CSV 行计数器（非 SQLite 计数）。如果 feed server 重启，它从 CSV 第 0 行开始。目前 10 个点已经写入 DB，但 source-poll 在 fetch 时通过 xdrip 协议的起始点参数控制——**需要验证 feed server 重启后能否跳过已有数据。**

**决议：** 待定 — 需要测试完整中断恢复

---

## 变更日志

| 日期 | 条目 | 操作 |
|------|------|------|
| 2026-07-02 | 全部 12 条 | 初始记录 + 微信通道 + 中断恢复 |
