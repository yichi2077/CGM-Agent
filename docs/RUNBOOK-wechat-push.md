# Runbook — 主动推送经 Hermes 微信触达（F5 最后一公里）

- **面向**：运维 / 集成者（在 Hermes 侧注册 cron）
- **关联决策**：[D048](DECISION_LOG.md)（push_tick 工具化）· [D049](DECISION_LOG.md)（webhook 投递）· [D052](DECISION_LOG.md)（推送→投递桥）
- **宪法约束**：原则 VII（Hermes 边界）——**微信发送发生在 Hermes 侧，能力层不驻留调度进程、不改 Hermes 安装树**。

---

## 1. 职责边界（谁做什么）

| 关注点 | 归属 | 说明 |
|---|---|---|
| 节奏 cadence（何时推） | **Hermes cron** | 标准 cron 表达式 + 时区 |
| 渠道 channel（发到微信） | **Hermes** | 微信入口是 Hermes 能力，本层不接触微信 API/凭证 |
| 策略/内容/状态（推什么、是否到期、幂等） | **CGM 能力层** | `PushSchedulerService` 内部完成；`content` 已过 companion 校验（≤100 字、无缩写、无断言） |
| 投递审计（delivery_id 回写） | **CGM 能力层** | `cgm_delivery_send` → `_link_push_event` 回写 `push_events.delivery_id` |

**关键不变量**：模型/cron 只能**触发** `push_tick`，无法干预分层选择、内容生成、静默即认可——全在 `PushSchedulerService` 内（D048）。

---

## 2. Hermes cron 注册

在 Hermes 侧注册每日一次的 cron（**建议 09:05 Asia/Shanghai**，晚于调度器 `daily_hour=9`，确保 daily tier 已到期）：

```yaml
# Hermes cron 条目（示意）
- name: cgm-daily-wechat-push
  schedule: "5 9 * * *"           # 每日 09:05
  timezone: "Asia/Shanghai"
  enabled_toolsets: [cgm]         # 需 cgm_scheduling_push_tick + cgm_delivery_send
  prompt: |                       # 见 §3 逐字提示词
    ...
```

需要暴露给 cron agent 的工具集 `cgm` 已含：`cgm_scheduling_push_tick`、`cgm_delivery_send`。

---

## 3. cron 提示词（逐字块）

> 把下面这段作为 cron 的 prompt。它只指挥 agent 做三件确定性的事：触发→转发→落审计。

```
你是 CGM 主动推送的投递员。执行以下步骤，不要添加任何医学建议或数字：

1. 调用工具 cgm_scheduling_push_tick，参数 user_id="<USER_ID>"。
2. 读取返回的 pushed 列表。若为空，本次结束，不做任何事（静默是正常的：限流或未到阈值）。
3. 对 pushed 中的每一项：
   a. 把该项的 content 字段【逐字】转发到用户的微信。禁止改写、扩写、翻译或补充任何数字——content 已是经过安全校验的陪伴文案。
   b. 微信发送成功后，调用工具 cgm_delivery_send，参数：
      channel="local_file", user_id="<USER_ID>", payload_ref=<该项的 push_id>,
      tier=<该项的 tier>, period_key=<该项的 period_key>。
      （这一步落地投递审计并回写 delivery_id，不会再次发送。）
4. 不要重试失败的微信发送——投递是 at-most-once，重复由 push_events 幂等兜底。
```

把 `<USER_ID>` 替换为实际用户 id。

---

## 4. 失败与幂等语义

- **at-most-once**：微信发送不重试；`push_events` 的 `UNIQUE(user_id, tier, period_key)` 保证同一周期不会重复推送，即使 cron 被重复触发。
- **content 已预校验**：`enforce_companion_text` 在生成时硬拦截缩写/断言并截断超长，故 agent「逐字转发」是安全的。
- **静默即正常**：`pushed` 为空可能是日限流（`_already_pushed_any_non_urgent_today`）或未过 `_should_trigger_daily_trend` 阈值——不是错误。

---

## 5. 验证

推送跑过一轮后：

```sql
-- push_events 应出现 delivery_id 非空的行（回写成功）
SELECT push_id, tier, period_key, delivery_id, pushed_at
FROM push_events WHERE user_id = '<USER_ID>' ORDER BY pushed_at DESC LIMIT 5;
```

- `delivery_id` 非空 → 桥闭合（D052 生效）。
- `<db_dir>/deliveries/<delivery_id>.json` manifest 文件存在 → local_file 审计落地。
- 无传感器时用回放引擎预置数据 + 触发推送做演示：见 README 的 `replay` 命令。
