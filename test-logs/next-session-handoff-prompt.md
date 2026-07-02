# CGM Agent 14-Day Simulation — Next-Session Handoff

你是 CGM Agent 14 天虚拟仿真测试的接手执行者。请完整理解以下上下文并继续执行未完成的测试。

## 项目信息

- 项目路径: E:\字幕组测试\CGM-Agent\hermes-cgm-agent-latest
- Hermes provider: deepseek, model: deepseek-v4-flash
- Hermes DB: C:\Users\postgres\AppData\Local\hermes\cgm-agent\app.db
- 测试用户: user_id=demo-prediabetes-14d-v2, source=virtual:aidex-v2
- 数据集: examples/cgm_test_dataset/cgm_14d_1min.csv (SHA256: 7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc)
- 微信目标: weixin:o9cq80yxtMVOK0GbUpXeZ7dDrYQI@im.wechat
- 时区: 北京时间 (UTC+8)，所有 cron 用 UTC expression (如 0 4 * * * = 北京 12:00)

## 当前 DB 状态 (2026-07-02 15:21 UTC+8)

| 表 | 行数 | 备注 |
|---|------|------|
| glucose_points | 14 | demo-prediabetes-14d-v2, virtual:aidex-v2, 07:08:47→07:21:51 UTC |
| audit_logs | 13 | — |
| import_batches | 14 | — |
| raw_cgm_records | 14 | — |
| user_events | 1 | smoke test event |
| memory_candidates | 1 | — |
| 其余表 | 0 | clean |

数据范围: 14 个点，~13 分钟的 1-min 节奏数据，值在 102-107 mg/dL 区间。

## 已完成的关键修复

### 1. L0 上下文 data_coverage 溢出 bug — 源码已修复

- **文件**: src/hermes_cgm_agent/services/memory/l0_builder.py (第 127-132 行)
- **根因**: `_daily_aggregates` 的 `day_scope` 用 min(timestamp)/max(timestamp) 替代了完整日历天边界。当数据集中在几分钟窗口内时，`_expected_point_count` 算出 tiny denominator，导致 `data_coverage > 100%`，触发 Pydantic 校验失败。
- **修复**: 改用 calendar day boundaries：
  ```python
  window_start=datetime.combine(day, datetime.min.time()).replace(tzinfo=zone),
  window_end=datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(tzinfo=zone),
  ```
- **验证**: 终端 Python 独立导入确认修复生效：`coverage=0.1%` (14 pts / 4032 expected over 14d)
- **⚠️ 重要**: Hermes agent 进程在修复前已加载旧模块到 `sys.modules`。即使 `__pycache__` 已清理，内存缓存仍保留旧字节码。**新会话首次调用 `cgm_context_get_l0` 可能仍报错 `data_coverage > 100`**——这是 HERMES 内存缓存问题，不是源码问题。**需要重启 Hermes 进程**（不只是一个新会话）。重启后修复自动生效。

### 2. WeChat iLink 熔断器配置 — 已持久化

- `platforms.weixin.extra.rate_limit_circuit_threshold: 3`
- `platforms.weixin.extra.rate_limit_circuit_window_seconds: 300`
- `platforms.weixin.extra.rate_limit_circuit_open_seconds: 3600`
- 持久化在 config.yaml，重启后自动加载

### 3. 外部接收器 — 独立 Python 脚本

- **脚本**: examples/cgm_test_dataset/external_receiver.py
- **架构**: 每 N 分钟从 CSV 抽取一个值，分配当前 UTC 时间戳，通过 SourcePollService 写入 DB
- **索引**: 从 SQLite `COUNT(*)` 读取当前位置 → 完全无状态，中断后可恢复
- **⚠️ 每次新会话必须重新启动接收器**（它是独立进程，不随 Hermes 持久化）

## 启动命令（新会话第一步）

```bash
cd "E:/字幕组测试/CGM-Agent/hermes-cgm-agent-latest"

# === 0. 清理残留 receiver 进程（重要！）===
# 先检查是否有孤儿：
#   在终端中运行:
#     python -c "
#     import subprocess
#     r = subprocess.run(['cmd.exe','/c','wmic process where name=\"python.exe\" get ProcessId,CommandLine /format:csv'],
#                        capture_output=True, timeout=10)
#     output = r.stdout.decode('gbk', errors='replace')
#     for line in output.split('\n'):
#         if 'external_receiver' in line:
#             pid = line.strip().split(',')[-1].strip()
#             if pid.isdigit():
#                 subprocess.run(['cmd.exe','/c',f'taskkill /F /PID {pid}'])
#                 print(f'Killed PID {pid}')
#     "
#   确认无残留后再继续。

# === 1. 清理 __pycache__（确保没有过期 .pyc）===
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
echo "Cache cleaned"

# === 2. DB 已清理完毕（本次会话已做），新会话可跳过 ===
# 如果需要重新清理：
# python -c "
# import sqlite3, os
# db = os.path.expanduser(r'~\AppData\Local\hermes\cgm-agent\app.db')
# conn = sqlite3.connect(db)
# for tbl in ['glucose_points','user_events','reports','push_events','memory_candidates','memory_summaries','l1_episodes','l2_profile_items','l3_hypotheses','raw_cgm_records','import_batches','audit_logs','detected_glucose_events','import_issues']:
#     conn.execute(f'DELETE FROM {tbl}')
# conn.commit(); conn.close()
# print('DB cleaned')
# "

# === 3. 验证 L0 修复（Hermes 重启后）===
python -c "
from hermes_cgm_agent.services.memory import L0ContextBuilder
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore
from hermes_cgm_agent.config import resolve_database_path
store = SQLiteStore(resolve_database_path())
repo = SQLiteCGMRepository(store)
builder = L0ContextBuilder(repository=repo)
ctx = builder.build(user_id='demo-prediabetes-14d-v2')
print(f'L0 OK: {len(ctx.high_res_recent)} pts, coverage={ctx.window_summary.data_coverage:.1f}%')
"

# === 4. 启动外部接收器 ===
# 1 分钟节奏（约 13.9 天跑完 20010 行）
python examples/cgm_test_dataset/external_receiver.py \
    --csv examples/cgm_test_dataset/cgm_14d_1min.csv \
    --interval-min 1
# ⚠️ 此命令持续运行 ~13.9 天，必须用 terminal(background=true)
# ⚠️ 绝对不要同时启动多个 receiver 实例！
```

## 工具链快速验证（数据开始流入后）

```
# 1. 验证数据可读
cgm_timeseries_get_points(data_scope={user_id:"demo-prediabetes-14d-v2", window_start:"2026-07-02T00:00:00+08:00", window_end:"2026-07-03T00:00:00+08:00"}, limit=5)
# 预期: status=ok, points 有解密后的 value_mg_dl

# 2. 验证 aggregate
cgm_timeseries_get_aggregate(data_scope={user_id:"demo-prediabetes-14d-v2", window_start:"2026-07-02T00:00:00+08:00", window_end:"2026-07-03T00:00:00+08:00"}, window_label="day")
# 预期: status=ok, MBG/CV/TIR 有值

# 3. 验证 L0（重启后）
cgm_context_get_l0(user_id="demo-prediabetes-14d-v2")
# 预期: status=ok, coverage < 100%

# 4. 验证 stale_status（关键！）
cgm_timeseries_get_realtime_snapshot(data_scope={user_id:"demo-prediabetes-14d-v2", window_start:"2026-07-02T00:00:00+08:00", window_end:"2026-07-02T23:59:59+08:00"}, expected_interval_minutes=1)
# 预期: stale_status=false（说明当前 UTC 时间戳生效）
```

## L0 字节码缓存问题 — 诊断与修复

如果 `cgm_context_get_l0` 仍然报 `data_coverage > 100` 错误：

1. **确认源码正确**: 检查 `l0_builder.py` 第 127 行是否为 `datetime.combine(day, ...)` 而非 `min(timestamp)`
2. **确认 `__pycache__` 已清理**: `find . -name "__pycache__" -type d | wc -l` 应为 0
3. **确认 Hermes 进程已重启**: 这是最常见的原因——Hermes daemon 缓存了旧模块。完全退出 Hermes 并重启
4. **验证**: 在 Hermes 外的终端运行上面的 L0 验证命令，应返回 `coverage < 100%`

## 关键数据收敛时间线

| 时间 | 数据量 | 可验证指标 |
|------|--------|-----------|
| 1 小时 | ~60 pts | 所有工具 `status=ok`，snapshot `stale_status=false` |
| 1 天 | ~1440 pts | TIR/TAR/TBR 开始有统计意义 |
| 5 天 | ~7200 pts | TIR 收敛到 ±1% |
| 14 天 | ~20010 pts | 全量数据集，所有指标应与 manifest 匹配 |

## ⚠️ 常见陷阱

1. **孤儿 receiver 积压**: 前一个 receiver 失败后未清理，多次重启累积多个实例 → 重复写入 + iLink 熔断。**每次启动前必须先查杀**。
2. **多 receiver 并行**: 绝对不要同时运行两个 `external_receiver.py`。它们读取相同的 SQLite COUNT → 双写相同点。
3. **__pycache__ 残留**: 源码修复后不清理 `__pycache__`，新进程可能加载旧 `.pyc`。
4. **Hermes 内存缓存**: 修复源码 + 清理 `__pycache__` 对已运行中的 Hermes 无效。必须重启 Hermes 进程。
5. **WeChat 推送**: iLink 熔断后需 ≥12h 静默才能恢复。测试期间建议优先用 `local_file` delivery。
6. **DB 加密**: SQLite 中的 `value_mg_dl` 是 `enc:v1:gAAAAA...` 密文。只能通过 `cgm_*` 工具读取解密值。
