# CGM Agent — 14 天 Hermes 虚拟仿真准备工作清单

> 生成日期：2026-07-01
> 目标：使用 Hermes + 虚拟数据源完成一次 **14 天不间断模拟调用**，验证全链路完整性（数据采集 → 分析 → 记忆 → 报告 → 推送交付）
> 排查方式：逐模块阅读源码、运行测试套件（476 OK）、比对已安装插件与源码差异、检查 ADR / MEM-ARCH / DECISION_LOG / PRD-SUPPLEMENT 等设计文档

---

## 如何阅读本清单

- **优先级**：`🔴 P0`（必须修，否则仿真不可启动）`→` `🟡 P1`（建议修，否则仿真体验严重受损）`→` `⚪ P2`（可延后，但影响评估完整性）
- **依赖**：`⛓ → P0-x` 表示此问题修复前必须先完成 P0-x
- **状态**：标记为 `❓`（待修复 Agent 确认）
- **涉及文件**：指向需要修改的核心文件路径

---

# 目录

1. P0-1: 数据库路径分裂（A1 脑分裂）
2. P0-2: Hermes 已安装插件与源码不同步
3. P0-3: 报告观测段仍混用临床术语
4. P1-4: 假设验证协商流程缺少用户话术
5. P1-5: Email 投递通道未实现
6. P1-6: 无自动化连续轮询机制
7. P1-7: Warm 合成无 cron 触发
8. P1-8: 连续异常升级关切未接入调度/报告
9. P1-9: 高级变异性指标（MAGE/MODD/CONGA）未实现
10. P2-10: E2E 测试未覆盖虚拟源+轮询全链路
11. P2-11: 大模块拆分（cli.py / builder.py）
12. P2-12: 权威 KB 仅 6 张卡且全部未核验
13. P2-13: SOUL.md 未注入 Hermes system_prompt_block
14. 附录 A: 问题依赖关系图
15. 附录 B: 修复推荐顺序
16. 附录 C: 各 Hermes cgm_* 工具状态

---

# 🔴 P0 级：必须修复

---

## P0-1：数据库路径分裂（A1 脑分裂）

| 字段 | 值 |
|------|-----|
| **优先级** | 🔴 **P0 — 最高** |
| **依赖** | 无（独立修复） |
| **影响范围** | ⛓ → P1-6, P2-13 |
| **涉及文件** | `src/hermes_cgm_agent/config.py:9-13`（`DEFAULT_DB_PATH`）、`src/hermes_cgm_agent/cli.py:280-282`（`AppConfig.from_env()`）、`src/hermes_cgm_agent/services/tools/handlers/push_tick.py`、所有 CLI 命令 |
| **已安装位置** | `~/.hermes/cgm-agent/app.db`（Hermes 插件的运行数据库） |
| **源码默认位置** | `.runtime/app.db`（`PROJECT_ROOT / ".runtime" / "app.db"`） |

### 现状

`config.py` 存在两套路径解析逻辑：

```
源码默认（DEFAULT_DB_PATH）     →  .runtime/app.db           [CLI 写入此处]
Hermes 插件（resolve_database_path） →  ~/.hermes/cgm-agent/app.db  [Hermes 对话读取此处]
```

当用户执行 `python -m hermes_cgm_agent seed-demo` 而不带 `--db-path` 参数时：

```
seed-demo 数据写入  →  .runtime/app.db  ← Hermes 对话看不见
Hermes 对话读取      →  ~/.hermes/cgm-agent/app.db  ← 空库
```

这就是「脑分裂」——CLI 演示链和 Hermes 产品链各走各的数据库。`seed-demo` 当前提供了一个 `--db-path` 参数做绕过，但：

1. Hermes 对话调用的 `cgm_*` 工具使用的是 `resolve_database_path(_runtime_hermes_home())`，会读取 `~/.hermes/cgm-agent/app.db`
2. `seed-demo --csv examples/cgm_test_dataset/cgm_14d_1min.csv --user-id demo-user --db-path ~/.hermes/cgm-agent/app.db` 可以绕开，但这只是手动权宜之计

### 对仿真测试的影响

**严重**。如果你在 Hermes 对话中尝试 `cgm_timeseries_get_points` 或 `cgm_reports_generate`，它们读取的是**空数据库**，而所有数据都在 `.runtime/app.db` 里。14 天仿真的第一个动作就是导入数据，这一步断了，后续全断。

### 修复要求

1. `config.py`：`DEFAULT_DB_PATH` 不再硬编码为 `.runtime/app.db`，改为通过 `resolve_database_path()` 派生
2. `cli.py:main()`：所有子命令默认使用 `resolve_database_path(hermes_home)`（从 `HERMES_HOME` 环境变量或 Hermes 默认路径推导）
3. `cli.py:_seed_demo()`：移除对 `AppConfig.from_env()` 的默认依赖，改为使用 `resolve_database_path()`
4. 现有 `.runtime/app.db` 中的数据需要迁移到 `~/.hermes/cgm-agent/app.db`
5. 该修复涉及的 CLI 命令：`import-cgm`、`seed-demo`、`source-poll`、`push-tick`、`context-build`、`synthesize`

### 验收标准

```bash
# 无 --db-path 参数时
python -m hermes_cgm_agent seed-demo --user-id demo-user
# 数据应写入 ~/.hermes/cgm-agent/app.db
ls ~/.hermes/cgm-agent/app.db  # 应存在且 > 1MB
# Hermes 对话中可以查到
cgm_timeseries_get_points(user_id="demo-user", data_scope={...})
# → 应返回非空 points 数组
```

---

## P0-2：Hermes 已安装插件与源码不同步

| 字段 | 值 |
|------|-----|
| **优先级** | 🔴 **P0** |
| **依赖** | 无 |
| **影响范围** | 所有通过 Hermes 调用的 `cgm_*` 工具 |
| **涉及文件** | `integrations/hermes/cgm/plugin.yaml`（源码）、`~/.hermes/plugins/cgm/plugin.yaml`（已安装） |
| **源码版本** | 22 个工具（含 `cgm_timeseries_get_realtime_snapshot`） |
| **已安装版本** | 21 个工具（缺少 `cgm_timeseries_get_realtime_snapshot`） |

### 现状

比对两个 `plugin.yaml`：

```diff
--- integrations/hermes/cgm/plugin.yaml  [源码，22 tools]
+++ ~/.hermes/plugins/cgm/plugin.yaml    [已安装，21 tools]
@@ -9,6 +9,7 @@
   - cgm_reports_generate
   - cgm_context_get_l0
   - cgm_timeseries_get_points
+  - cgm_timeseries_get_realtime_snapshot  ← 缺失
   - cgm_timeseries_get_aggregate
   - cgm_events_create
```

`cgm_timeseries_get_realtime_snapshot` 是新加入的实时信号快照工具，已在 `executor.py` 中注册（行 66：`"timeseries.get_realtime_snapshot": "_get_realtime_snapshot"`），handler 在 `services/analytics/realtime.py` 中实现。但已安装到 Hermes 的 `plugin.yaml` 未更新，因此 Hermes 对话中此工具**不可用**。

### 对仿真测试的影响

14 天仿真需要一个完整的数据周期：采集 → 实时快照 → 聚合分析 → 事件检测 → 报告。缺少实时快照工具意味着无法在特定时刻获取「当前血糖是多少、趋势如何」的快照——这是虚拟设备模拟的核心能力之一。

### 修复要求

将 `integrations/hermes/cgm/plugin.yaml` 复制到 `~/.hermes/plugins/cgm/plugin.yaml`，或提供一条 CLI 命令完成同步：

```bash
python -m hermes_cgm_agent hermes-install --reinstall
# 或
cp integrations/hermes/cgm/plugin.yaml ~/.hermes/plugins/cgm/plugin.yaml
```

### 验收标准

```bash
grep "realtime_snapshot" ~/.hermes/plugins/cgm/plugin.yaml
# 应输出： - cgm_timeseries_get_realtime_snapshot
```

---

## P0-3：报告观测段（observations）仍混用临床术语

| 字段 | 值 |
|------|-----|
| **优先级** | 🔴 **P0** |
| **依赖** | 无 |
| **涉及文件** | `src/hermes_cgm_agent/services/reports/builder.py:617-700`（`_observations_section`）、`services/reports/narrative_templates.py:15-26`（`_BLACKLIST_ABBRS`） |
| **测试文件** | `tests/test_f4_companion_narrative.py` |

### 现状

大部分报告章节（日报卡片、概览、指标、生活事件、检测事件、假设叙事）已经实现**受众分裂**（SELF/FAMILY 走中文生活语言，CLINICIAN 走技术指标），代码质量很高。例如 `_daily_card_text()` 产出：

```
SELF:  "今天整体平稳，曲线大多顺着走，暂时没有看到特别突出的波动。"
FAMILY: "今天整体平稳，没有看到需要特别担心的波动。"
CLINICIAN: "今日整体平稳，TIR 85%，数据覆盖率 92%。"
```

但 **`_observations_section`** 对 SELF 受众仍保留临床缩写：

```python
# builder.py:634
elif (aggregate.tar or 0) > (aggregate.tbr or 0) and (aggregate.tar or 0) > 0:
    if audience == ReportAudience.CLINICIAN:
        observations.append("本窗以高于目标范围时间为主，偏高负担高于偏低负担。")
    elif audience == ReportAudience.FAMILY:
        observations.append("今天主要是偏高多一点，不过还在可回看的范围里。")
    else:
        observations.append("本窗高于目标范围的时间多于低于目标范围的时间。")  # ← SELF 路径仍含术语
```

这里的 SELF 路径 `"本窗高于目标范围的时间多于低于目标范围的时间"` 仍然使用 `目标范围` 等生活化术语可以接受，但和其他章节的「看起来像某个时段短暂滑下去」风格不一致。更准确的表述应该是 `"今天偏高的时候比偏低的时候多一些"`。

### 对仿真测试的影响

14 天仿真中，日报告和观察段会输出不一致的风格——日报卡片是口语化的，但观察段偶尔回到半临床语气。评测者会注意到这种「人格切换」。

### 修复要求

重写 `_observations_section` 中 SELF 路径的文本，使之与 `_daily_card_text` 保持一致的中文生活语言风格。删去所有非 CLINICIAN 路径中的缩写引用。确保 `narrative_templates.py` 的 `check_companion_text()` 验证通过。

### 验收标准

```python
from hermes_cgm_agent.services.reports.narrative_templates import check_companion_text
text = "今天偏高的时候比偏低的时候多一些"
assert check_companion_text(text) == []  # 零违规
```

---

# 🟡 P1 级：建议修复

---

## P1-4：假设验证协商流程缺少用户话术

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | ⛓ → P0-1（需要统一 DB 路径使 hypothesis 持久化可验证） |
| **涉及文件** | `src/hermes_cgm_agent/domain/memory.py:109-123`（`L3Hypothesis` 状态机）、`services/reports/narrative_templates.py:86-172`（已有中文模板）、`services/tools/handlers/push_tick.py` |
| **当前状态** | 4 状态机已实现但缺少各状态的**对话模板**和**互动协议** |

### 现状

L3 Hypothesis 状态机已在领域层完整实现：

```
candidate → observing → stable → archived
                ↑（矛盾证据）
            invalid → archived
```

`narrative_templates.py` 的 `render_hypothesis_narrative()` 已根据 hypothesis state 提供基础中文话术：

```python
if state == "candidate":
    → "注意到一个规律：午餐后血糖偏高。你觉得和你那几天的午餐有关系吗？"
if state == "observing":
    → "继续观察中：午餐后血糖偏高。最近几次的餐后波动好像稳定了一些。"
if state == "stable":
    → "看起来这个规律基本确定了：午餐后血糖偏高。"
```

但缺失以下内容：

1. **协商式对话协议**：`hypothesis.update` 工具已注册但无对话上下文说明如何「邀请用户验证」
2. **确认/拒绝反馈模板**：用户说「是的」或「不是这样」后回应的模板
3. **渐进关切话术**：假设从 candidate → observing → stable，话术应渐进式递进，而非每次一样
4. **沉默升级**：当 hypothesis 在 `candidate` 状态下沉默超过 N 天，应使用更主动的话术

### 对仿真测试的影响

仿真运行中假设会积累和演变，但当 Hermes 模型想要邀请用户验证某个模式时，缺少配套模板 → 交互要么突兀（直接说「我发现了这个模式」），要么错失「邀请验证」的产品体验。

### 修复要求

1. 为每个 Hypothesis 状态增加**两个对话模板变体**（首次提醒 / 再次提醒）：
   - `candidate`：`"注意到一个规律：{summary}。你留意到过这个吗？"` / `"之前提到过的那个{summary}，最近几次数据里又出现了，你感觉有变化吗？"`
   - `observing`：`"继续观察中：{summary}。过去{N}次数据里出现了{M}次。"` / `"持续观察的{summary}，这周频率跟上周差不多。"`
   - `stable`：`"看起来这个模式基本稳定了：{summary}。你同意这个观察吗？"` / `"已经确认的{summary}，最近的走势挺一致。"`
   - `archived`：`"之前关注的{summary}，最近已经很少出现了。"`
2. 在 `provider.sync_turn` 或 `on_turn_start` 中添加状态转换时的话术注入
3. 写入 `services/memory/hypothesis_dialogue.py` 或扩充 `narrative_templates.py`

---

## P1-5：Email 投递通道未实现

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | ⛓ → P0-1（需要统一 DB 路径使 `delivery.send` 可访问报告） |
| **涉及文件** | `src/hermes_cgm_agent/services/tools/handlers/delivery.py:133-134` |
| **当前状态** | `email` 通道返回 `delivery_status="queued"`，无实际 SMTP 发送 |

### 现状

`delivery.py:117-134` 中 Email 通道的实际代码：

```python
if channel == "local_file":
    # ... 实际写入文件，delivery_status = "sent"
else:
    delivery_status = "queued"  # ← email 和任何未来通道都落在这里
```

Webhook 通道有完整的 HTTP POST 实现（含 PHI 过滤、https-only、无重定向安全策略）。但 Email 通道只是一个存根。Hermes 的后续网关可以接续发送，但**在这个项目的能力层中，Email 输出从未被验证过**。

### 对仿真测试的影响

14 天仿真中，`delivery.send(channel="email")` 返回 `"queued"`，而非实际生成或发送。如果要验证推送闭环，只能使用 `local_file` 或 `webhook`。但如果用户期望通过电子邮件接收每日摘要，则此功能缺失。

### 修复要求

1. 添加 SMTP 发送功能（使用 `smtplib`，Python 标准库）
2. `delivery.py` 中增加 SMTP 配置环境变量（`CGM_SMTP_HOST`、`CGM_SMTP_PORT`、`CGM_SMTP_USERNAME`、`CGM_SMTP_PASSWORD`、`CGM_SMTP_TO_ADDRESS`）
3. Email 内容使用与 webhook 同样的 PHI 过滤策略
4. 添加 SMTP 连接超时处理（10s）和错误回退（→ `local_file`）

### 验收标准

```bash
export CGM_SMTP_HOST=smtp.example.com
export CGM_SMTP_PORT=587
export CGM_SMTP_USERNAME=user
export CGM_SMTP_PASSWORD=pass
export CGM_SMTP_TO_ADDRESS=user@example.com

python -m hermes_cgm_agent delivery-send --channel email --payload-ref <report_id>
# 应返回 delivery_status="sent"
```

---

## P1-6：无自动化连续轮询机制

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | ⛓ → P0-1（轮询写入统一 DB），⛓ → P0-2（实时快照工具可用） |
| **影响范围** | 14 天仿真自动化运行 |
| **涉及文件** | `src/hermes_cgm_agent/services/sources/poller.py`、`examples/cgm_test_dataset/virtual_cgm_feed.py` |

### 现状

- ✅ `virtual_cgm_feed.py`：可启动一个 HTTP 服务器，以 xDrip 格式模拟 14 天数据集（支持 1/5/15 分钟间隔）
- ✅ `source-poll` CLI：可手动拉取 N 条数据并导入 SQLite
- ❌ **无自动化持续轮询**：Hermes cron 的 `no_agent=true` 脚本模式可以解决，但无现成的注册命令或模板

`poller.py` 实现了 `SourcePollService.poll()` 方法，支持去重和游标管理。但 `virtual_cgm_feed.py` 的状态机（`VirtualCGMFeedState.cursor`）是进程内变量——每次 HTTP 请求推进游标，但 `source-poll` 每次调用会重新启动 feed 连接，游标不会持久化。

### 对仿真测试的影响

14 天仿真的核心是「自动化」。如果每 5 分钟需要手动执行一次 `python -m hermes_cgm_agent source-poll --count 12`，仿真不可行。需要：

1. 一个 Hermes cron job 注册机制（每 5 分钟轮询一次）
2. 或者一个本地脚本（Windows Task Scheduler），持续从虚拟源拉取数据
3. `poller.py` 需要能持久化游标位置（基于 `received_at` 时间戳），避免重复导入

### 修复要求

**方案 A（推荐——14 天仿真专用）**：
1. 在 `examples/cgm_test_dataset/` 下提供 `auto_poll.py`，持续循环以可配置间隔调用 `SourcePollService.poll()`
2. 使用 `received_at` 时间戳判断已导入的边界（`poller.py` 已有 `_deduplicate` 基础）
3. 提供 Windows Task Scheduler 的 XML 模板文件

**方案 B（通用——接入 Hermes cron）**：
1. 注册一条 Hermes cron job：`cronjob action=create schedule="every 5m" prompt="执行 CGM 数据轮询" no_agent=true script="poll.sh"`
2. `poll.sh` 调用 `python -m hermes_cgm_agent source-poll --count 12`

### 验收标准

```bash
# 启动虚拟源
python examples/cgm_test_dataset/virtual_cgm_feed.py --emit-interval-min 5 &

# 自动轮询持续 1 小时
python examples/cgm_test_dataset/auto_poll.py --interval-min 5 --count 12 --duration-hours 1

# 验证数据已导入
python -m hermes_cgm_agent dev-status --db-path ~/.hermes/cgm-agent/app.db
# 应输出 glucose_point_count > 0，且随时间增长
```

---

## P1-7：Warm 合成无 cron 触发

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | ⛓ → P0-1（合成写入统一 DB），⛓ → P1-6（轮询到足够数据后才能合成） |
| **涉及文件** | `src/hermes_cgm_agent/services/memory/consolidation.py`（`synthesize_state` 方法）、`docs/MEM-ARCH.md §3` |
| **当前状态** | 核心 ✅，cron 触发 ❌ |

### 现状

`synthesize_state()` 方法已实现完整逻辑：

1. 读取 L0 数据 + L1 情景 + 聚合指标
2. 合成结构化用户状态摘要（例如："本周 TIR 72%，环比 +3%；近期晚餐后偏高"）
3. 写入 `memory_summaries` 表
4. `provider.prefetch()` 注入到 Hermes 上下文

但 `synthesize_state` 的触发仅发生在：

- CLI：`python -m hermes_cgm_agent synthesize`
- `consolidation.consolidate()`：在 session end 时触发（但每次触发的是 `consolidate`，不是 `synthesize`）
- `scheduling.scheduler.py`：内置 `PushSchedulerService` 管理推送策略，但**未调用 `synthesize_state`**

MEM-ARCH 明确标注：

> P4 做梦 → ✅ 核心（cron 未接）

### 对仿真测试的影响

14 天仿真中，预期 Warm 摘要每日更新。如果没有 cron 触发，Warm 摘要要么不存在，要么只在手动调用时更新。prefetch 注入的上下文可能过时，LLM 看到的「用户近况」摘要与实际数据不一致。

### 修复要求

1. 在 `PushSchedulerService` 的 `decide_due_tiers()` 逻辑中，增加 `daily` 级别的 `synthesize_state` 自动触发
2. 或者在 `scheduler.py` 中新增 `PushSchedulerService.synthesize_if_due()` 方法
3. 配置：`timezone`, `hour`（默认 08:00 本地时间）
4. 测试：`test_push_scheduler.py` 增加定时合成测试

### 验收标准

```bash
# 手动触发合成
python -m hermes_cgm_agent synthesize --user-id demo-user

# 验证 memory_summaries 表有记录
python -m hermes_cgm_agent dev-status --db-path ~/.hermes/cgm-agent/app.db
# audit_logs 中应有 event_type='state_synthesized' 的记录
```

---

## P1-8：连续异常升级关切未接入调度/报告

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | ⛓ → P0-1（需要统一 DB 路径才能读取历史数据做连续天数计算） |
| **涉及文件** | `src/hermes_cgm_agent/domain/memory.py:45-69`（`EscalationState.derive`）、`services/scheduling/scheduler.py`、`services/reports/builder.py` |
| **当前状态** | 领域模型 ✅，调度/报告接入 ❌ |

### 现状

`EscalationState` 枚举和 `EscalationState.derive()` 已实现，支持两套阈值：

```python
# 脆弱人群（is_vulnerable=True）：CONCERN 第一天，EXTERNAL_SUPPORT 第五天
# 一般人群（is_vulnerable=False）：CONCERN 第三天，EXTERNAL_SUPPORT 第七天
```

但推导出的状态**从未被消费**：
- `PushSchedulerService`: 推送内容生成未检查 `EscalationState`
- `ReportService._daily_card_text()`: 未接收 `EscalationState`，因此不会根据连续异常调整措辞
- `PushTickHandler`: 未查询 `l2_profile_items` 中的 `is_vulnerable` 标志

### 对仿真测试的影响

仿真数据集包含连续高/低血糖事件，14 天中必然出现连续异常日。如果 escalate 状态不接入报告和推送，LLM 生成的每日卡片会「今天偏高多一点」连续说 7 天，而不会渐进式地「这几天偏高比较频繁，要不要一起看看？」→「这是连续第 5 天偏高，可能需要多留意一下」的变化。

### 修复要求

1. `PushSchedulerService` 增加 `escalation_state` 查询方法：读取最近 N 天的 aggregate → 判断连续异常天数
2. `_build_push_message()` 按 `EscalationState` 选择话术模板
3. `builder.py:_daily_card_text()` 增加 `escalation_state` 参数，在连续异常场景下使用更关切的话术
4. 话术示例：`normal` → "今天偏高比较多，要不要留意一下午饭？" → `concern` → "这几天偏高有点频繁，是不是最近的饮食或活动有变化？" → `external_support` → "偏高的情况持续一周了，数据我帮你整理好了，复诊时可以带给医生看。"

---

## P1-9：高级变异性指标（MAGE/MODD/CONGA）未实现

| 字段 | 值 |
|------|-----|
| **优先级** | 🟡 **P1** |
| **依赖** | 无（独立计算模块） |
| **涉及文件** | `src/hermes_cgm_agent/services/analytics/metrics.py` |
| **当前状态** | 基础指标 ✅，高级指标 ❌ DEFERRED |

### 现状

已实现的指标：
| 指标 | 公式 | 用途 |
|------|------|------|
| TIR | 3.9–10.0 mmol/L 时间占比 | 达标率 |
| TAR | >10.0 mmol/L 占比 | 偏高暴露 |
| TBR | <3.9 mmol/L 占比 | 偏低暴露 |
| MBG | 均值 | 中心趋势 |
| CV | 标准差/均值 × 100% | 变异系数 |
| GMI | 回归公式 | 预估 HbA1c |
| LBGI | 低血糖风险指数 | 低血糖风险 |
| HBGI | 高血糖风险指数 | 高血糖风险 |

未实现的（标记 DEFERRED）：
| 指标 | 公式 | 用途 | 为什么对 14 天仿真重要 |
|------|------|------|----------------------|
| MAGE | 平均血糖波动幅度 | 日内波动 | 14 天数据可展示每天波动幅度的变化趋势 |
| MODD | 日间平均差值 | 日间模式稳定度 | 14 天数据的核心应用——"这周和上周的规律一致吗" |
| CONGA | 连续重叠净血糖作用 | 每 N 小时差异 | 餐后反应分析 |

### 对仿真测试的影响

14 天仿真的周报和月报中，将无法回答「这周和上周比有什么变化」——只能输出静态的 TIR/TAR/TBR，缺乏动态对比的能力。MODD 是 14 天数据集的**核心指标**，因为 14 天的主要优势在于看出日间模式是否稳定。

### 修复要求

1. 在 `metrics.py` 中添加 `MAGE` 计算（`compute_mage(points, scope)`)
2. 添加 `MODD` 计算（`compute_modd(points, scope)`——按日分组后计算相邻日的平均绝对差）
3. 添加 `CONGA` 计算（`compute_conga(points, interval_hours)`)
4. 将这三个指标集成到 `CGMAnalyticsService.compute_aggregate()` 的可选输出中
5. `GlucoseAggregate` 模型增加可选字段 `mage`, `modd`, `conga`

---

# ⚪ P2 级：优化项

---

## P2-10：E2E 测试未覆盖虚拟源+轮询全链路

| 字段 | 值 |
|------|-----|
| **优先级** | ⚪ P2 |
| **依赖** | ⛓ → P0-1（测试数据写入统一 DB 路径），⛓ → P1-6（自动轮询代码可用） |
| **涉及文件** | `tests/test_source_poll.py`、`tests/test_virtual_cgm_dataset.py` |

### 现状

- `test_source_poll.py`：使用 `FakeHTTPClient` 硬编码 2 条记录，未与 `virtual_cgm_feed.py` 集成
- `test_virtual_cgm_dataset.py`：测试数据集生成和默认参数，但未测试完整链路：轮询 → 导入 → 指标 → 事件检测 → 报告
- 现有的 E2E 测试（`test_hermes_e2e.py`、`test_g0_g7_e2e.py`）测试了 CLI 全链路，但使用的是内部构件而非真实的虚拟 HTTP 源

### 修复要求

新增测试 `test_virtual_source_e2e.py`：

```python
# 伪代码
def test_virtual_feed_to_report_e2e(self):
    # 1. 启动 VirtualCGMFeedState（无需 HTTP 服务器）
    state = VirtualCGMFeedState(load_points(CSV_PATH), ...)
    
    # 2. 模拟 N 次轮询（替代 source-poll）
    for _ in range(5):
        payload = state.next_payload(count=12)
        service.poll(user_id="demo-user", kind="xdrip", ...)
    
    # 3. 计算指标
    points = repository.list_glucose_points(scope)
    aggregate = analytics.compute_aggregate(points, scope)
    
    # 4. 检测事件
    events = detector.detect(points, scope)
    
    # 5. 生成报告
    report = report_service.generate(scope, audience="self")
    
    # 6. 断言
    assert aggregate.tir is not None
    assert len(detected_events) > 0
    assert report.rendered_markdown
    assert narrative_templates.check_companion_text(report.sections[0].content) == []
```

---

## P2-11：大模块拆分（cli.py / builder.py）

| 字段 | 值 |
|------|-----|
| **优先级** | ⚪ P2 |
| **依赖** | 无 |
| **涉及文件** | `src/hermes_cgm_agent/cli.py`（1372 行 / 50KB）、`services/reports/builder.py`（1204 行 / 54KB） |

### 现状

| 文件 | 行数 | 文件大小 | 包含的功能 |
|------|------|----------|-----------|
| `cli.py` | 1372 | 50KB | 参数解析 + 子命令 x 12 + `_seed_demo` 全链路 + `_hermes_status` + `_warn_legacy_store` |
| `builder.py` | 1204 | 54KB | 报告生成 + 所有受众分裂逻辑 + 模式检测 + 模式信号 + 事件标签 |

### 对仿真测试的影响

不直接影响功能执行，但：
- 修复 Agent 在定位代码时需要跨越数百行找对应函数
- 模块边界模糊（`cli.py` 包含完整的数据处理逻辑，而不仅是 CLI 编排）
- 不利于单元测试的精确性

### 修复要求

`cli.py` 拆分建议：
```
cli/
  __init__.py    # main() 和解析器
  cmd_import.py  # import-cgm
  cmd_dexcom.py  # dexcom-auth, dexcom-sync
  cmd_seed.py    # seed-demo
  cmd_report.py  # synthesize, context-build, push-tick
  cmd_kb.py      # kb-ingest, kb-ingest-llm, kb-ingest-batch, kb-merge
  cmd_eval.py    # eval-rag
  cmd_install.py # hermes-install
  cmd_status.py  # status, dev-status
```

`builder.py` 拆分建议：
```
reports/
  builder.py            # ReportService 编排
  companion_builder.py  # 面向 SELF/FAMILY 的叙事构建
  clinician_builder.py  # 面向 CLINICIAN 的叙事构建
  pattern_detector.py   # 模式检测逻辑（从 builder.py 抽出）
```

---

## P2-12：权威 KB 仅 6 张卡且全部未核验

| 字段 | 值 |
|------|-----|
| **优先级** | ⚪ P2 |
| **依赖** | 无（独立的人工/机器串流程） |
| **涉及文件** | `src/hermes_cgm_agent/knowledge/authoritative_kb.json`、`knowledge/ingest/`、`knowledge/review_queue/` |
| **当前 KB 数量** | 6 张双语论断卡（全部 `tier=curated, verified=false`） |

### 现状

`authoritative_kb.json` 当前包含 6 张人工编撰的中英双语论断卡：
1. `battelino-2019-tir-adults`：TIR 目标 >70%
2. `ada-2025-hypo-level1`：低血糖 1 级定义
3. `ada-2025-hypo-level2`：低血糖 2 级定义（<54 mg/dL）
4. `cds-2024-tir-chinese`：中文 TIR 目标
5. `ishne-2023-agp-landmark`：AGP 关键指标
6. `tir-consensus-tir-targets`：TIR 共识目标

PDF 已解析到 `review_queue/` 但**未合入生产 KB**（D042 决策：329 张 sentence 引擎卡导致 hit@3 从 100% 掉到 84.4%，退回到仅保留 6 张种子卡）。

### 对仿真测试的影响

仿真中调用 `cgm_rag_authoritative_search` 仅能检索到 6 张卡。对于糖尿病患者的常见知识问题（如「我应该控制在什么范围」「什么叫低血糖」「我的 GMI 表示什么」），KB 回答覆盖面很窄。但这不影响仿真功能的验证——只是知识库内容的不完整。

### 修复要求

1. 用 Hermes 引擎重新抽取高优先级 PDF（至少 ada-2025, CDS, Battelino 共识）：
   ```bash
   python -m hermes_cgm_agent kb-ingest-llm --pdf knowledge/pdfs/ada-2025-abridged.pdf --engine hermes --mode vision
   ```
2. 质量过滤后合入生产 KB：
   ```bash
   python -m hermes_cgm_agent kb-merge --kb-version kb-2026-07-auto-v1 --into knowledge/authoritative_kb.json
   ```
3. 运行 RAG eval 验证 hit@3 保持 ≥ 90%：
   ```bash
   python -m hermes_cgm_agent eval-rag --kb knowledge/authoritative_kb.json
   ```

---

## P2-13：SOUL.md 未注入 Hermes system_prompt_block

| 字段 | 值 |
|------|-----|
| **优先级** | ⚪ P2 |
| **依赖** | ⛓ → P0-1（统一 DB 路径后 memory provider 才稳定读取配置） |
| **涉及文件** | `src/hermes_cgm_agent/services/memory/provider.py`（`system_prompt_block`）、`~/.hermes/memories/default/cgm_memory/` |
| **当前状态** | `provider.py` 有 `system_prompt_block` 返回文本，但未引用 SOUL.md 内容 |

### 现状

- SOUL.md（251 行）已完整定义了知情陪伴者人格、交互原则、句式禁忌、不确定性表达规范、升级关切阶梯
- `provider.py` 的 `system_prompt_block()` 方法返回以英文为主的简短指令（"must be cited with uncertainty, never as authoritative medical fact"）
- SOUL.md **未被 provider 读取和注入**到 Hermes 的 system prompt 中

### 对仿真测试的影响

14 天仿真期间，LLM 在 Hermes 对话中生成回复时，**没有接收到 SOUL.md 的人格规范**。LLM 默认行为是医学术语 + 建议式语气，而非「知情陪伴者」风格。仿真中看到的对话体验将不同于 SOUL.md 定义的产品人格。

### 修复要求

1. `provider.py` 的 `initialize()` 方法中读取 `SOUL.md` 文件（`PROJECT_ROOT / "SOUL.md"`）
2. `system_prompt_block()` 返回 SOUL.md 中的人格定义和交互原则（精简版），而非只有一句话的指令
3. 如果需要控制 token 开销，可预编译为浓缩版，保留核心人格特征

---

# 附录 A：问题依赖关系图

```
P0-1 (DB 路径分裂) ──→ P1-6 (自动轮询)
     │                    ↓
     ├──→ P1-4 (假设话术)   P1-7 (Warm 合成)
     ├──→ P1-5 (Email)       ↓
     ├──→ P1-8 (升级关切)   P2-10 (E2E 测试)
     └──→ P2-13 (SOUL.md)
     
P0-2 (插件同步) ──→ P1-6 (自动轮询——需要实时快照工具)
     
P0-3 (观察段术语) —— 独立，不依赖其他项

P1-9 (高级指标) —— 独立，不依赖其他项

P2-11 (大模块拆分) —— 独立，不依赖其他项

P2-12 (KB 扩容) —— 独立，不依赖其他项
```

**关键路径**（Critical Path for 14-day simulation launch）：

```
P0-1 → P1-6 → P1-7 → P2-10
  +                          → 仿真可启动
P0-2
  +
P0-3
```

**修复 Agent 的执行顺序建议**：

```
第 1 批（并行，无依赖）：P0-2, P0-3, P1-9, P2-11, P2-12
第 2 批（等待 P0-1）：P0-1
第 3 批（等待第 2 批）：P1-6, P1-4, P1-5, P1-8, P2-13
第 4 批（等待第 3 批）：P1-7, P2-10
```

---

# 附录 B：修复推荐顺序（按启动仿真）

| 阶段 | 修复项 | 期望结果 |
|------|--------|----------|
| **Phase 1：基础修复** | P0-1, P0-2, P0-3 | DB 统一 + 工具完整 + 报告可读 |
| **Phase 2：数据管道** | P1-6, P1-7 | 自动轮询 + Warm 合成 → 14 天可自动化运行 |
| **Phase 3：交互增强** | P1-4, P1-5, P1-8 | 假设话术 + 邮件投递 + 升级关切 |
| **Phase 4：质量保障** | P2-10, P1-9, P2-12, P2-13 | E2E 测试 + 高级指标 + KB 扩容 + 人格注入 |
| **Phase 5：代码整理** | P2-11 | 大模块拆分 |

---

# 附录 C：各 Hermes cgm_* 工具状态

| 工具名称 | 状态 | 对应 Handler | 备注 |
|----------|------|-------------|------|
| `cgm_timeseries_get_points` | ✅ Active | `timeseries.py:_get_points` | |
| `cgm_timeseries_get_aggregate` | ✅ Active | `timeseries.py:_get_aggregate` | |
| `cgm_timeseries_get_realtime_snapshot` | 🔴 **未安装** | `timeseries.py:_get_realtime_snapshot` | 需 P0-2 |
| `cgm_events_create` | ✅ Active | `events.py:_create_event` | |
| `cgm_events_confirm` | ✅ Active | `events.py:_confirm_event` | |
| `cgm_context_get_l0` | ✅ Active | `context.py:_get_l0_context` | |
| `cgm_reports_generate` | ✅ Active | `reports.py:_generate_report` | 产出见 P0-3 |
| `cgm_memory_list` | ✅ Active | `memory.py:_memory_list` | |
| `cgm_memory_delete` | ✅ Active | `memory.py:_memory_delete` | |
| `cgm_memory_confirm` | ✅ Active | `memory.py:_memory_confirm` | |
| `cgm_memory_correct` | ✅ Active | `memory.py:_memory_correct` | |
| `cgm_hypothesis_update` | ✅ Active | `memory.py:_hypothesis_update` | 话术见 P1-4 |
| `cgm_rag_authoritative_search` | ✅ Active | `rag.py:_rag_search` | KB 规模见 P2-12 |
| `cgm_rag_verify_quotes` | ✅ Active | `rag.py:_verify_quotes` | |
| `cgm_kb_approve` | ✅ Active | `memory.py:_kb_approve` | |
| `cgm_delivery_send` | ✅ Active | `delivery.py:_delivery_send` | Email 见 P1-5 |
| `cgm_data_dexcom_sync` | ✅ Active | `dexcom.py:_dexcom_sync` | 仿真使用虚拟源，非 Dexcom |
| `cgm_scheduling_push_tick` | ✅ Active | `push_tick.py:_push_tick` | 升级关切见 P1-8 |
