# CGM Agent 14-Day Virtual Simulation — 综合审计报告
## 测试 v2 | 2026-07-02 02:28~02:42 UTC

---

## 1. 14 天运行日志（截至 2026-07-02 02:42 UTC）

| 时间 (UTC) | 阶段 | 事件 | 累计点 | 最新时间戳 | 状态 |
|-----------|------|------|--------|-----------|------|
| 02:28:00 | 预检 | SHA256 验证、文档确认、DB baseline | 0 | — | ✅ |
| 02:28:25 | 预检 | 确认 old demo-prediabetes-14d=48pts, sim=36pts, v2=0pts | 0 | — | ✅ |
| 02:29:00 | Feed | virtual_cgm_feed.py 启动 (PID 20180, port 17580, 4002 pts) | 0 | — | ✅ |
| 02:29:21 | 注入 | source-poll 第 1 次 (smoke test) | 1 | 18:05 | ✅ inserted_count=1 |
| 02:29:23 | 注入 | source-poll 第 2 次 | 2 | 18:10 | ✅ |
| 02:29:24 | 注入 | source-poll 第 3 次 | 3 | 18:15 | ✅ |
| 02:29:25 | 注入 | source-poll 第 4 次 | 4 | 18:20 | ✅, all detected=0 |
| 02:29:57 | 注入 | auto_poll poll_index=1 (PID 7652) | 6 | 18:30 | ✅ |
| 02:30:41 | 验证 | realtime_snapshot / aggregate / get_points | 6 | 18:30 | ✅ stale=true (历史数据) |
| 02:30:49 | 验证 | L0 context build | 6 | 18:30 | ✅ 296 tokens, 压缩策略生效 |
| 02:30:50 | 报告 | Daily report #1 (clinician, full-day) | 6 | 18:30 | ✅ report_id=a13e251c... |
| 02:31:00 | 推送 | push_tick 调用 | 6 | 18:30 | ✅ ok, empty (系统空闲) |
| 02:31:00 | 事件 | user_event 创建 (meal, 65g carbs) | 6 | 18:30 | ✅ event_id=1f1e6c51... |
| 02:31:10 | 交付 | delivery_send local_file | 6 | 18:30 | ✅ 115d8025...json |
| 02:31:30 | 持久化 | cron job cgm-14d-simulation-tick 创建 | 6 | 18:30 | ✅ every 5min |
| 02:34:57 | 注入 | auto_poll poll_index=2 | 7 | 18:35 | ✅ |
| 02:37:01 | 报告 | Daily report #2 (self, 1h window) | 8 | 18:40 | ✅ report_id=eeef7b41... |
| 02:38:30 | 验证 | 原始值级对比 CSV vs DB: 8/8 完全匹配 | 8 | 18:40 | ✅ |
| 02:39:00 | 事件 | user_event 创建 (note: 测试归因) | 8 | 18:40 | ✅ event_id=7e1c67f8... |
| 02:39:57 | 注入 | auto_poll poll_index=3 | 9 | 18:45 | ✅ |
| 02:42:00 | 审计 | 最终 DB 扫描 | 10 | 18:50 | ✅ |

**中断前最后一个成功点:**
- auto_poll poll_index=3 @ 02:39:57 → timestamp=2026-04-24T18:45:00+00:00
- DB 最新 timestamp: 2026-04-24T18:50:00+00:00 (10 个点)
- Feed server PID 20180: 运行中
- Poller PID 7652: 运行中, uptime 632s

**恢复命令:**
```bash
# 如果 cron job 已运行: 等待每 5min 自动推进
# 如果全部宕机:
cd "E:/字幕组测试/CGM-Agent/hermes-cgm-agent-latest"
python examples/cgm_test_dataset/virtual_cgm_feed.py --emit-interval-min 5 &
python examples/cgm_test_dataset/auto_poll.py \
  --user-id demo-prediabetes-14d-v2 \
  --url http://127.0.0.1:17580 \
  --count 1 --interval-min 5 --duration-hours 336 \
  --source virtual:aidex-v2
```

---

## 2. 每日 Checkpoint 摘要（Day 1 of 14）

### 数据积累
```
glucose_points:  10 pts (目标 4002, 0.25% 完成)
时间范围:      2026-04-24T18:05 → 2026-04-24T18:50+00:00
覆盖时长:      45 分钟 / 5 分钟间隔
所有点质量:    valid, 无 artifact
```

### 聚合指标
| 指标 | Day 1 值 | 说明 |
|------|---------|------|
| TIR | 100.0% | 全部在 70-180 目标范围 |
| TAR | 0.0% | 无高血糖暴露 |
| TBR | 0.0% | 无低血糖暴露 |
| MBG | 104.04 mg/dL | 稳态窗口均值 |
| CV | 1.42% | 极低变异（凌晨稳态） |
| GMI | 5.80 | — |
| data_coverage | 3.47% | 仅覆盖 45 分钟 |

### 系统状态
```
Feed 服务器        🟢 PID 20180, uptime ≈14min
auto_poll          🟢 PID 7652, 3 tick 完成, 336h duration
Cron 备份          🟢 已创建, 每 5min 触发
DB                 🟢 app.db, 92 total glucose_points (10 v2)
Memory/Hypothesis  ⚪ 全部为空 — 未触发
检测事件            ⚪ 全部为 0 — 未触发
```

---

## 3. 问题清单

| # | 时间 | 触发 | 错误 | 影响 | 绕过 | 状态 |
|---|------|------|------|------|------|------|
| 1 | 02:28 | auto_poll --count 12 | 需 55min 完成对话超时 | 延误 smoke test | 改用 --interval-sec 1 --max-polls 3 | ✅ 已绕过 |
| 2 | 02:28 | DB 直接查询 | `sqlite3.OperationalError: no such column: ts` | 阻塞 sqlite 查询 | 实际列名是 `timestamp` / `value_mg_dl` | ✅ 已纠正 |
| 3 | 02:28 | DB 直接查询 | 值显示 `enc:v1:gAAAAA...` 密文 | 无法直接读值 | 使用 cgm_timeseries_get_points 工具解密 | ✅ 已知设计 |
| 4 | 02:30 | read_file | 中文字符路径 + 反斜杠 → "File not found" | 假阳性 | 用 terminal cat 或 search_files | ⚠️ 环境问题 |
| 5 | 02:36 | cronjob --script | "Script path must be relative to ~/.hermes/scripts/" | cron 无法直接引用跨盘符路径 | 创建 wrapper .sh 在 scripts/ 下 | ⚠️ 环境限制 |
| 6 | 02:30 | report generate | 医生版: TAR=0.0% 但叙事说"高于目标范围暴露为主" | **事实矛盾/hallucination** | 无绕过 — 需修复 LLM narrative 对齐 | ❌ **未修复** |
| 7 | 02:30 | report generate | 覆盖率警告在 5-6 个 section 中重复 | 冗余/模板化 | 需修复 report builder 去重 | ❌ **未修复** |
| 8 | 02:37 | report generate | 用户版: 4.6 mg/dL 稳态波动归因为"餐后小高峰" | 叙事归因错误 | 无绕过 — 需修复 LLM 因果推理 | ❌ **未修复** |
| 9 | 全周期 | auto_poll 每次结果 | `detected_event_count: 0` 对所有点 | 事件检测管道从未触发 | 需等待 artifact 窗口或人工验证 | ⚠️ 未确认 |

---

## 4. 未研究清楚/潜在风险清单

### 4.1 幂等性与重复投递风险

| 风险 | 当前证据 | 评级 |
|------|---------|------|
| **push_events 幂等性** | push_events 表有 `period_key` + `tier` 字段。如果同一 period_key 已存在，二次 tick 应跳过。**但未经测试。** | 🟡 结构上有保障，未验证 |
| **delivery 重复** | 当前 1 次 delivery 成功。无 `delivery_id` 防重机制在 `delivery_send` 端。 | 🟡 如果同一个 report_id 被多次 delivery_send，会创建多个 delivery 文件 |
| **auto_poll + cron 双重写入** | 两个独立进程向同一 DB 写，索引策略不同。**至今未同时触发过——cron 尚未 run** | 🔴 见风险登记册 #1 |
| 当前结论 | 尚无实际的重复推送/投递发生（push_events=0）。但基础设施未验证幂等性保障。 | |

### 4.2 旧数据污染

| 数据源 | user_id | source | 点 | 隔离方式 | 泄露风险 |
|--------|---------|--------|---|---------|---------|
| 旧 v1 测试 | demo-prediabetes-14d | virtual:aidex | 48 | **user_id 不同 ✅** | 低 — 所有 cgm_* 工具按 user_id 过滤 |
| 旧 sim 测试 | demo-prediabetes-sim | virtual:aidex | 36 | **user_id 不同 ✅** | 低 |
| 当前 v2 测试 | demo-prediabetes-14d-v2 | virtual:aidex-v2 | 10 | **user_id 不同 + source 不同 ✅** | **无** |

**风险场景：** 如果未来有工具查询时不传 `user_id` 或误传旧 user_id，会读到旧数据。但所有 cgm_* 工具调用都要求 user_id 参数。当前隔离充分。

**已确认的旧报告中混杂：** 有 1 条 report 来自 `demo-prediabetes-sim user=demo-prediabetes-sim type=daily audience=self` → 属于旧测试，不影响 v2。

### 4.3 工具返回 "ok" 但语义可疑

| 工具 | 返回 | 语义问题 |
|------|------|---------|
| `cgm_timeseries_get_realtime_snapshot` | `status=ok` | `missing_rate_1h=100%, stale_status=true` — **"ok"但数据完全陈旧**。对历史数据回放正确，但下游不知道区分"ok+stale"和"ok+fresh" |
| `cgm_scheduling_push_tick` | `status=ok` | `pushed=[]` — **"ok"但什么也没做**。无法区分"策略判定不推送"和"调度器没接线/配置错误" |
| `cgm_memory_list` | `status=ok` | `candidates=[], memories=[]` — **"ok"但完全为空**。无法区分"系统还没形成记忆"和"记忆写入断开" |
| `cgm_reports_generate` | `status=ok, generated_at=...` | 报告叙事包含 **TAR=0% 但说"高于目标范围暴露"的矛盾** — 生成成功但内容有 hallucination |
| source-poll    | `status=ok, inserted=1` | `detected_event_count=0` **始终为 0** — 无法区分"没有要检测的事件"和"检测器未运行" |

**根本问题：** 工具层缺少语义分级的返回值。一个 `status=ok` 掩盖了"正常"、"空但正确"、"空但可能故障"、"数据陈旧但服务正常"等多种状态。

---

## 5. 工具调用证据摘要

| 时间 | 工具 | 状态 | 关键字段 | 证据 |
|------|------|------|---------|------|
| 02:30:41 | cgm_timeseries_get_realtime_snapshot | ok | latest=101.5, stale=true, missing_1h=100% | audit_id=ca1c8f58 |
| 02:30:41 | cgm_timeseries_get_aggregate | ok | TIR=100%, MBG=103.52, CV=1.65%, 6pts | audit_id=af6122eb |
| 02:30:41 | cgm_timeseries_get_points | ok | 8 pts decrypted, 全部 valid | audit_id=c4f8c919 |
| 02:30:49 | cgm_context_get_l0 | ok | 296 tokens, compression_policy, 6 recent pts | audit_id=c35e7695 |
| 02:30:50 | cgm_reports_generate (clinician) | ok | report_id=a13e251c, 8 sections, 3 KB refs | audit_id=73249a46 |
| 02:31:00 | cgm_scheduling_push_tick | ok | pushed=[] (空) | audit_id=8afbaf0d |
| 02:31:00 | cgm_events_create (meal) | ok | event_id=1f1e6c51, unconfirmed candidate | audit_id=d6b40847 |
| 02:31:10 | cgm_delivery_send (local_file) | ok | delivery_id=115d8025, manifest written | audit_id=04548bba |
| 02:31:10 | cgm_memory_list (all) | ok | 0 memories | audit_id=5ff8574d |
| 02:37:01 | cgm_reports_generate (self) | ok | report_id=eeef7b41, 用户版叙事 | audit_id=9f510e09 |
| 02:39:00 | cgm_events_create (note) | ok | event_id=7e1c67f8 | audit_id=10d7fac1 |
| 02:42:00 | cgm_timeseries_get_aggregate (final) | ok | TIR=100%, MBG=104.04, CV=1.42%, 10pts | audit_id=83166d3c |

**KG/知识工具调用：**
| 02:42 | cgm_rag_authoritative_search (×3) | ok | 共返回 9 张 KB card, 全部 tier=auto, verified=false | audit_ids=4a7adb80 etc |

---

## 6. DB 最终状态摘要

### 6.1 glucose_points

| 项目 | 值 |
|------|-----|
| 总量 | 92 |
| demo-prediabetes-14d (旧) | 48 pts, source=virtual:aidex |
| demo-prediabetes-sim (旧) | 36 pts, source=virtual:aidex |
| **demo-prediabetes-14d-v2 (本测试)** | **10 pts, source=virtual:aidex-v2** |
| 最新时间戳 (v2) | 2026-04-24T18:50:00+00:00 |
| 最早时间戳 (v2) | 2026-04-24T18:05:00+00:00 |
| 间隔一致性 | ✅ 全部 5 分钟间隔 |
| 重复时间戳 | ❌ 无重复 |
| 质量标记 | 全部 valid |

### 6.2 其他表

| 表 | 行数 | 说明 |
|----|------|------|
| glucose_points | 92 | 3 个用户合计 |
| raw_cgm_records | 94 | 原始记录 |
| import_batches | 61 | 52×aidex + 9×aidex-v2 |
| reports | 3 | 1×sim + 2×v2 |
| user_events | 2 | 均在 v2 下，手工创建 |
| push_events | 0 | — |
| unread_badges | 0 | — |
| l1_episodes | 0 | — |
| l2_profile_items | 0 | — |
| l3_hypotheses | 0 | — |
| memory_candidates | 0 | — |
| memory_summaries | 0 | — |
| detected_glucose_events | 0 | — |
| audit_logs | 26 | 全部 tool_call 事件 |

### 6.3 持久化组件

| 组件 | 状态 |
|------|------|
| Feed Server PID 20180 | 🟢 运行中 (uptime ≈14min) |
| auto_poll PID 7652 | 🟢 运行中 (uptime ≈10.5min, 3 ticks) |
| Cron job cgm-14d-simulation-tick | 🟢 已创建, every 5min, 尚未首次触发 |
| Delivery file 115d8025...json | ✅ 已确认写入磁盘 (205 bytes) |
| Test log | ✅ test-logs/simulation-14d-v2-log-2026-07-02.md |
| Risk register | ✅ test-logs/simulation-14d-v2-risks-2026-07-02.md |

---

## 7. 报告样例路径 & report_id

### 已生成报告

| Report ID | Audience | 窗口 | 语言 | 路径 |
|-----------|---------|------|------|------|
| a13e251c32f0456d9b9c00b0f4329213 | clinician | 2026-04-24T00:00→04-25T00:00 | zh-CN | 通过 delivery_send 写入本地文件 |
| eeef7b41718b447aa9beb6faff5064d1 | self | 2026-04-24T18:00→19:00 | zh-CN | 同上 |

### Delivery 文件

```
C:\Users\postgres\AppData\Local\hermes\cgm-agent\deliveries\
  115d8025f6f5455e8a9b997dec811196.json  (205 bytes)
  内容: {channel, delivery_id, payload_ref, session_id, user_id}
```

### 报告生成命令

```bash
# 重新生成医生版 daily report:
cgm_reports_generate(
    user_id="demo-prediabetes-14d-v2",
    data_scope={"window_start":"2026-04-24T00:00:00Z","window_end":"2026-04-25T00:00:00Z"},
    report_type="daily",
    audience="clinician",
    retrieve_context=true
)

# 重新生成用户版 daily report:
cgm_reports_generate(
    user_id="demo-prediabetes-14d-v2",
    data_scope={"window_start":"2026-04-24T18:00:00Z","window_end":"2026-04-24T19:00:00Z"},
    report_type="daily",
    audience="self",
    language="en-US"
)
```

---

## 补充：未验证 KB 声明

本测试中所有引用的 KB 卡片属于 `kb-2026-06-auto-v2` 版本库，均为自动生成（tier=auto），**未经临床人工核验**（verified=false）。报告中已使用以下标记：

> "以下为指南摘录草稿，非医疗建议；以下医学参考仍待人工核验，仅可作为背景线索，不能作为最终医学依据"

被引用的未验证 KB 卡：
- `kb-2026-06-auto-v2:auto-cds-2024-guideline-p18-cds2024-p18-012` — CDS 2024 CSME 筛查频率 (population=elderly)
- `kb-2026-06-auto-v2:auto-ishne-2023-agp-p4-agp-daily-profile-shading` — ISHNE 2023 AGP 阴影规范 (population=general)
- `kb-2026-06-auto-v2:auto-ispad-2024-glycemic-p4-ispad2024-p004-kidney-native-american-prediabete` — ISPAD 2024 美国原住民 prediabetes 并发症 (population=pediatric Native American)

**注意：** 三张 KB 卡的人群标签（elderly, general, pediatric Native American）与测试用户（普通 prediabetes）均不完全匹配。引用时不检查人群兼容性。

---

*本报告由 CGM Agent 自动化生成，作为 14 天虚拟仿真测试的综合审计交付物。所有医学相关内容均基于用户自身数据，不构成医疗诊断或建议。具体临床决策请咨询执业医师。*
