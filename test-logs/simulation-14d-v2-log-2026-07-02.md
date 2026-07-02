# CGM Agent 14-Day Virtual Simulation Test Log
## Session: 2026-07-02 — Test v2

**Test ID:** demo-prediabetes-14d-v2
**Source:** virtual:aidex-v2
**Dataset:** cgm_14d_1min.csv (SHA256: 7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc)
**Provider/Model:** deepseek / deepseek-v4-flash
**Hermes DB:** C:\Users\postgres\AppData\Local\hermes\cgm-agent\app.db

---

## A. 发生的问题 (Issues Log)

| # | Time (UTC) | Trigger | Tool/Command | Error | Impact | Workaround |
|---|-----------|---------|-------------|-------|--------|-----------|
| 1 | 02:28:00 | 运行 auto_poll --count 12 --interval-min 5 | terminal(background) | 55分钟耗时过长，无法在对话中等待 | 延迟smoke test | 改用 --max-polls 3 --interval-sec 1 快速验证通道 |
| 2 | 02:28:25 | DB查询glucose_points | python sqlite | `sqlite3.OperationalError: no such column: ts` | 阻塞查询 | 查询 PRAGMA table_info 发现列名为 `timestamp` / `value_mg_dl` (非 `ts` / `mgdl`) |
| 3 | 02:28:25 | DB查询直接查询值 | python sqlite | 值显示为 `enc:v1:gAAAAA...` 加密密文 | 无法直接读raw值 | 使用 cgm_timeseries_get_points 工具有解密，返回明文 |
| 4 | 02:30:00 | 读取 simulation_tick.py | read_file | "File not found" // 实际文件存在 | 假阳性 | 目录名含中文字符+反斜杠路径格式导致；使用 search_files 或 terminal cat 可正常访问 |
| 5 | 02:36:00 | cronjob create --script | cronjob | "Script path must be relative to ~/.hermes/scripts/" | 无法直接使用跨盘符路径 | 创建 wrapper.sh 在 ~/.hermes/scripts/ 目录下 |
| 6 | 02:36:49 | 数据集时间戳 | 全部 | 数据时间锚在 2026-04-24，而当前时间是 2026-07-02 | realtime_snapshot 显示 stale_status=true, missing_rate_1h=100% | 这是预期行为——历史数据在实时快照中自然标记为stale；不影响 aggregate 和 report |

---

## B. 测试执行记录

### Phase 0: Pre-Test Verification (Completed 02:28 UTC)
- [x] PRETEST-FREEZE-2026-07-02.md 确认
- [x] manifest.json 确认
- [x] README.md 确认
- [x] CSV SHA256: 7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc ✅
- [x] DB 18 tables present ✅
- [x] demo-prediabetes-14d-v2: 0 rows (clean slate) ✅
- [x] Old data exists but isolated: demo-prediabetes-14d (48pts), demo-prediabetes-sim (36pts)

### Phase 1: Infrastructure (Completed 02:29 UTC)
- [x] virtual_cgm_feed.py started (PID 20180) → http://127.0.0.1:17580/sgv.json
- [x] 4002 virtual CGM points loaded from CSV
- [x] auto_poll.py started (PID 7652) → --duration-hours 336, --interval-min 5
- [x] Cron job created: cgm-14d-simulation-tick (every 5 min, script fallback)
- [x] Smoke test: 3/3 polls inserted ✅ (0 duplicates, 0 errors)

### Phase 2: Tool Chain Verification (Completed 02:30-02:31 UTC)
- [x] **cgm_timeseries_get_realtime_snapshot**: ✅ status=ok, latest=101.5 mg/dL
- [x] **cgm_timeseries_get_aggregate**: ✅ status=ok, 6 pts, TIR=100%, MBG=103.52
- [x] **cgm_timeseries_get_points**: ✅ status=ok, 6 decrypted points returned
- [x] **cgm_context_get_l0**: ✅ status=ok, 296 tokens, compression active
- [x] **cgm_reports_generate** (daily): ✅ status=ok, ID=a13e251c3...
- [x] **cgm_scheduling_push_tick**: ✅ status=ok, empty (early phase - expected)
- [x] **cgm_events_create** (meal): ✅ status=ok, ID=1f1e6c51...
- [x] **cgm_memory_list**: ✅ status=ok, 0 entries (clean slate)
- [x] **cgm_delivery_send** (local_file): ✅ status=ok → 115d8025...json

### Phase 3: Permanent Infrastructure Status
| Component | Status | Details |
|-----------|--------|---------|
| virtual_cgm_feed.py (feed server) | 🟢 RUNNING | PID 20180, port 17580, 4002 pts |
| auto_poll.py (14-day poller) | 🟢 RUNNING | PID 7652, 336h duration, 5min interval |
| Cron job (durable backup) | 🟢 SCHEDULED | every 5 min, script fallback |
| Hermes DB app.db | 🟢 ACTIVE | 90 total glucose points |

### Data Snapshot at 02:31 UTC
| Metric | Value |
|--------|-------|
| Total glucose_points (v2) | 6 |
| Time range | 2026-04-24T18:05 → 18:30 |
| User events | 1 (meal, unconfirmed) |
| Reports | 1 (daily) |
| Import batches (aidex) | 58 (all v1+v2) |
| DB total glucose_points | 90 |

---

## C. 14-Day Simulation Timeline & Milestones

### Milestones to check (relative to test start: 2026-07-02 02:28 UTC)

| Time Elapsed | Calendar | Checkpoint Action |
|-------------|----------|-------------------|
| T+0 | Jul 02 02:28 | ✅ Infrastructure deployed, 6 pts |
| T+1 hour | Jul 02 03:28 | ~18 pts accumulated — verify realtime snapshot freshness |
| T+4 hours | Jul 02 06:28 | ~54 pts — run 2nd daily report |
| T+12 hours | Jul 02 14:28 | ~150 pts — verify push_tick with meaningful data |
| T+1 day | Jul 03 02:28 | ~294 pts — generate daily report #2 |
| T+2 days | Jul 04 02:28 | ~582 pts — generate daily report #3 |
| T+3 days | Jul 05 02:28 | ~870 pts — generate daily report #4 |
| T+5 days | Jul 07 02:28 | ~1,446 pts — generate weekly report #1 |
| T+7 days | Jul 09 02:28 | ~2,022 pts — generate weekly report #2, doctor report |
| T+10 days | Jul 12 02:28 | ~2,886 pts — generate daily report, push_tick verification |
| T+14 days | Jul 16 02:28 | ~4,002 pts (end) — final report, delivery, summary |

### Continuous monitoring (auto_poll.py):
- **PID 7652** runs `--duration-hours 336` (14 days)
- If process dies: cron job `cgm-14d-simulation-tick` auto-restarts simulation via script
- Virtual feed server (PID 20180) must stay alive for the whole 14 days

---

## D. Known Limitations (This Test)

1. **Data timestamps are April 2026** — realtime snapshots show stale=true. This is expected for historical dataset replay. For fresh-data testing, regenerate with recent timestamps.
2. **KB cards are unverified** — all KB references tagged with `[待核验/unverified]` per test scope.
3. **Email delivery not tested** — scoped out per PRETEST-FREEZE.
4. **DB encryption** — values stored encrypted (`enc:v1:`); only accessible through cgm_* tools.
5. **No user events loaded from behavior_events_14d.json** — would need manual import or event detection from data artifacts.
