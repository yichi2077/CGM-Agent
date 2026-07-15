# MVP 综合审计报告（更新于 2026-07-16）

> 基于 develop 分支最新提交 `cd05f37` / `bbecf1d` 的代码状态复核

---

## 一、本次代码更新摘要

### 新增提交（2026-07-16）

| 提交 | 类型 | 内容 |
|------|------|------|
| `cd05f37` | feat | **D063/D064**: 删除 Dexcom/AiDEX 官方 API 适配器，统一采用 xDrip Companion 模式；新增 bridge freshness watchdog |
| `bbecf1d` | chore | 更新 Claude 本地权限白名单 |

### 代码量变化

- **删除**: ~4509 行（废弃的 vendor API 集成代码、CLI、测试）
- **新增**: ~836 行（watchdog、sources 模块重构、文档更新）
- **净减少**: ~3673 行代码，架构显著简化

### 测试状态

```
Ran 821 tests in 95.605s
OK (skipped=3)
```
全部离线确定性测试通过。

---

## 二、P0 问题修复状态核查

| ID | 问题 | 状态 | 位置 | 备注 |
|----|------|------|------|------|
| P0-1 | 红区模板无急救指引 | ❌ **未修复** | [router.py#L49-L52](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L49-L52) | 仍只有免责声明，无低血糖自救指引 |
| P0-2 | 夜间低血糖单次触发被静默 | ❌ **未修复** | [scheduler.py#L387-L440](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L387-L440) | `_should_trigger_daily_trend` 仍需连续2天同时段异常才触发 |
| P0-3 | README 阈值文档漂移 | ❌ **未修复** | [README.md#L45-L78](file:///workspace/README.md#L45-L78) | **代码阈值已正确，但文档仍错误！** |

---

## 三、P0 问题详细说明

### P0-1: 红区模板无急救指引 ❌

**当前代码**（router.py 第49-52行）：
```python
RED_ZONE_TEMPLATE = (
    "这个问题涉及医疗判断，我无法代替医生给出建议。"
    "我可以帮你整理相关数据，你可以在复诊时带给医生。需要我生成报告吗？"
)
```

**问题**：用户处于严重低血糖（<54 mg/dL）时，这个模板只说"无法代替医生"，却没有提供任何即时自救指引（如15-15法则）。在深夜等无法立即就医的场景下，这是严重的安全缺陷。

**建议修复方案**：
- 按严重程度分流模板：
  - **轻度/中度低血糖 (54-70 mg/dL)**: 黄区，提示补充碳水
  - **重度低血糖 (<54 mg/dL)**: 红区，提供 15-15 法则指引："立即服用15g快速碳水（如半杯果汁、3-4块方糖），15分钟后复测；若仍<54或出现意识模糊，请立即寻求帮助/使用胰高血糖素"
  - **严重高血糖 (>250 mg/dL)**: 红区，提示检查酮体、补充水分、及时就医
- 免责声明保留，但放在自救指引之后

---

### P0-2: 夜间低血糖被静默 ❌

**当前逻辑**（scheduler.py 第404-438行）：
日推送触发条件仅包含：
1. TIR 较昨日波动 ≥5%
2. 24小时内有新的 L3 candidate 假设
3. **连续2天同一6小时时段出现异常**

**问题**：单次夜间（00:00-06:00）严重低血糖事件不会触发次日早晨推送——必须连续两天同一时段低才会推。夜间低血糖是最危险的场景（用户可能在睡梦中无法感知），"静默即认可"在此处是致命的设计错误。

**建议修复方案**：
在 `_should_trigger_daily_trend` 开头增加短路条件：
```python
# 夜间低血糖/高血糖单次即触发，无需连续两天
if self._has_overnight_severe_event(points, events):
    return True
```
- 检测窗口：当日 00:00-06:00（本地时区）
- 触发条件：任意红区事件（<54/>250 mg/dL 持续 ≥10分钟）或严重黄区（<60/>240）
- 推送内容需明确标记"夜间血糖异常"

---

### P0-3: README 阈值文档漂移 ❌

**代码实际值**（router.py 第39-46行）：
```python
#   green   70 - 180
#   yellow  low side 54 - 70,  high side 180 - 250
#   red     low side < 54,     high side > 250
RED_ZONE_LOW_MGDL = 54.0
RED_ZONE_HIGH_MGDL = 250.0
YELLOW_ZONE_LOW_MGDL = 70.0
YELLOW_ZONE_HIGH_MGDL = 180.0
```

**README 错误描述**：
- 第45行 mermaid 图：红区 `"<54 或 >300 mg/dL"` ❌
- 第46行 mermaid 图：黄区 `"<70 或 >250 mg/dL"` ❌
- 第75行文字：`"RED_ZONE_LOW/HIGH = 54/300"` ❌
- 第77行文字：`"黄区 (54 ≤ 值 < 70 或 250 < 值 ≤ 300 mg/dL)"` ❌
- 第78行文字：`"红区 (<54 或 >300 mg/dL)"` ❌

**必须修正**：README 中的阈值描述需要与代码完全一致。

---

## 四、新引入功能评估 ✅

### 4.1 xDrip Companion Mode（D064）✅ 优秀

**变更内容**：
- 删除了未使用/无权限的 Dexcom v3 和 AiDEX Open API 适配器（共删除 ~2700 行代码）
- 统一数据路径：`AiDEX X 传感器 → 厂商App（保持BLE/告警） → 系统通知 → xDrip+ Companion模式 → 局域网HTTP → bridge-poll`
- 厂商App继续作为告警权威（用户已习惯），CGM Agent 只做AI增强层

**评估**：✅ **这是非常正确的架构决策**
- 避免了维护没有API entitlement的第三方集成
- 复用了成熟的xDrip生态，减少BLE维护负担
- 符合AGENTS.md中"保持本项目为能力层"的定位
- 文档（ANDROID-CGM-BRIDGE.md、README、.env.example）已同步更新

### 4.2 Bridge Freshness Watchdog ✅ 设计良好

**位置**：[watchdog.py](file:///workspace/src/hermes_cgm_agent/services/sources/watchdog.py)

**功能**：
- 边缘触发告警：只在健康状态↔不健康状态**边界切换**时发webhook，不是每分钟都发
- PHI-free 载荷：仅发送 `alert/state/newest_reading_age_minutes/at`，不包含血糖值、用户ID
- 安全加固：HTTPS-only、禁止3xx重定向、10秒超时不重试
- 最佳努力（best-effort）：告警失败只审计，不中断数据采集
- 复用现有 audit_logs 表，不新增表

**评估**：✅ **设计严谨**
- 解决了"夜间桥接静默挂掉无人知"的问题
- 安全边界清晰，PHI保护到位
- 有完整单元测试（test_bridge_watchdog.py，254行测试）
- 已集成到 bridge CLI 的正常路径和异常路径

---

## 五、其他已确认正常的功能

| 功能 | 状态 | 备注 |
|------|------|------|
| 三区安全路由（除模板外） | ✅ 正常 | 10分钟持续时间门控、瞬态降级逻辑正确 |
| 红区恢复二次确认 | ✅ 正常 | 2小时窗口、SQLite持久化、原子事务 |
| PHI Fernet 加密 | ✅ 正常 | 文件权限0600、WAL模式支持 |
| 静默即认可 | ✅ 正常 | candidate→observing 3天窗口，可逆 |
| 推送幂等性 | ✅ 正常 | UNIQUE约束兜底，(user,tier,period)不重复 |
| 双轨RAG物理隔离 | ✅ 正常 | 引用守卫、track隔离 |
| L0上下文压缩 | ✅ 正常 | 3/7/14天渐进衰退 |

---

## 六、下一步优先级

### 🔥 P0（必须立即修复）

1. **P0-3 修正 README 阈值文档** — 5分钟工作量，无代码风险
2. **P0-1 红区模板分流** — 增加低血糖/高血糖自救指引
3. **P0-2 夜间严重事件单次触发** — 增加OVERNIGHT_LOW/OVERNIGHT_HIGH短路逻辑

### 🟡 P1（近期修复）

4. **入库值 range 校验** — repository 层增加生理范围检查（如 20-600 mg/dL），异常值标记但不丢弃
5. **知识库卡片临床签核流程** — 工具链已就绪，但尚无卡片完成签核（KNOWN_ISSUES #1）
6. **用户端显示单位切换** — CGM_AGENT_DISPLAY_UNIT 配置已存在，需确认全链路生效

### 🟢 P2（后续迭代）

7. 移动端/微信推送适配（降低使用门槛）
8. 报告模板的情感化语调优化（SOUL.md落地）
9. 回放引擎（replay engine）和 eval_recall.py 合并（之前在claude/*分支未合入）

---

## 七、代码索引（最新）

| 模块 | 文件 |
|------|------|
| 安全路由 | [src/hermes_cgm_agent/services/safety/router.py](file:///workspace/src/hermes_cgm_agent/services/safety/router.py) |
| 调度/推送 | [src/hermes_cgm_agent/services/scheduling/scheduler.py](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py) |
| Bridge Watchdog | [src/hermes_cgm_agent/services/sources/watchdog.py](file:///workspace/src/hermes_cgm_agent/services/sources/watchdog.py) |
| SQLite存储 | [src/hermes_cgm_agent/storage/sqlite.py](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py) |
| 记忆Provider | [src/hermes_cgm_agent/services/memory/provider.py](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py) |
| Bridge CLI | [src/hermes_cgm_agent/cli/bridge.py](file:///workspace/src/hermes_cgm_agent/cli/bridge.py) |

---

## 八、复核结论

**本次更新整体评价**：✅ **架构改进显著，安全功能仍需补全**

- 正面：D063/D064 数据源架构简化是正确决策，删除了大量无用代码，新增的watchdog设计严谨、安全边界清晰
- 负面：前次审计提出的三个P0安全问题**仍未修复**，尤其是红区无急救指引和夜间低血糖静默问题，在真实使用场景中存在安全隐患
- 建议：优先处理P0-3（README文档修正，无风险），然后集中精力修复P0-1和P0-2两个安全相关问题
