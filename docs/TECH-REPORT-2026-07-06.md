# CGM-Agent 技术报告

- **日期**：2026-07-06
- **代码基线**：`claude/project-docs-review-dev-g8xlsv` @ `9330bc9`（D052）
- **规模**：源码 ~18,300 行 / 测试 ~12,400 行（524 用例全绿，skipped=3）/ 18 个 LLM 工具
- **定位**：单用户个人部署的 CGM（动态血糖）智能陪伴 Agent，运行在 Hermes Agent 平台之上；当前唯一支持的硬件目标为微泰 MicroTech/AiDEX

---

## 1. 项目各模块内容

### 1.1 分层总览

```
设备/数据源                 能力层（本项目, ~18k 行）                Hermes 平台
┌──────────────┐   ┌──────────────────────────────────────┐   ┌─────────────┐
│ AiDEX 传感器  │→ │ sources/ 桥接拉取   data/ 导入规范化     │   │ LLM 对话     │
│ (xDrip/      │   │        ↓                              │   │ cron 调度    │
│  Juggluco 桥)│   │ storage/ 加密 SQLite（Fernet 字段级）    │← │ 18 个 cgm_* │
│ CSV 导出      │   │        ↓                              │   │   工具调用   │
└──────────────┘   │ analytics/ 确定性指标+事件               │   │ memory      │
                   │ memory/ L0–L3 记忆    safety/ 三区路由   │   │   provider  │
                   │ reports/ 双轨报告     rag/ 权威知识库     │   │   注入      │
                   │ scheduling/ 推送     simulation/ 回放    │   └─────────────┘
                   └──────────────────────────────────────┘
```

### 1.2 模块明细（按代码量排序）

| 模块 | 行数 | 职责 |
|---|---|---|
| `services/memory/` | 3,287 | **L0–L3 分层记忆**。L0：确定性工作上下文 Builder（近端高分辨率点 + 中端小时聚合 + 远端日聚合，token 预算内裁剪，D038）；L1：情景（检测事件/对话候选，确认闸门 D026/D037）；L2：信念（同型情景 ≥3 天 → 画像项，置信度演化 + 30 天衰减，双时间有效期 D032）；L3：假设状态机（candidate→observing→stable→archived，静默即认可只推进到 observing）；warm digest"做梦"合成（D034）；BM25+CJK bigram 混合检索（D035/D036）；USER.md 受管段单向同步（D039） |
| `services/tools/` | 2,479 | 18 个 LLM 工具的 registry（schema 单一来源）+ executor（按域 mixin 分发、审计日志、错误兜底、默认身份注入 D052） |
| `services/reports/` | 1,756 | 双轨报告生成：F3 临床轨（医生版纯指标直出）与 F4 陪伴轨（生活语言叙事）严格隔离；引用硬闸（未支撑医学数字阻断交付）；红区整报替换；脆弱人群免责声明闸 |
| `services/data/` | 1,152 | CSV/JSON 导入（单位别名宽容 D052）、规范化（时区/去重/质量标记 warmup/suspect/valid）、SQLite 仓库（naive==UTC 规范 D051） |
| `services/dexcom/` | 1,158 | Dexcom OAuth/同步（保留为受测兼容代码，非 MVP 路线） |
| `knowledge/` + `services/rag/` | 1,979 | 权威医学知识库：逐字论断卡（双语、页码溯源、tier 分级 curated/auto、verified 签核位）+ BM25 可信优先检索 + hit@3 CI 门禁（D040–D044） |
| `domain/` | 895 | Pydantic 域模型：血糖点/事件/聚合/报告/记忆各层/DataScope；全局约定 naive==UTC（`ensure_utc`）、单位别名解析（`parse_glucose_unit`） |
| `services/simulation/` | 866 | 设备无关 CSV 回放管线：SimClock 加速/实时、流式逐点摄取、采样节奏自动推断、审计不变量（幂等/确定性/计数守恒）、Hermes preflight（exit 2）|
| `services/analytics/` | 743 | 确定性指标（TIR/TAR/TBR/GMI/CV/LBGI/HBGI/MAGE/MODD/CONGA）与事件检测（低/高血糖、速率、数据缺口；速率最小跨度门 D052）。**数值永不来自 LLM**（D015/D022） |
| `services/sources/` | 572 | xDrip/Juggluco/Nightscout 兼容 HTTP 采集桥（AiDEX 当前接入通道）：URL 安全策略（HTTPS 或本地私网）、原始载荷存档、去重入库 |
| `storage/` | 518 | 字段级 Fernet 加密 SQLite；key 与 DB 同目录、0600 权限；解密失败显式报错 |
| `services/safety/` | 467 | 三区安全路由（红 <54/>300 整报医疗转介、黄 70–250 外告警前缀、绿正常）；红区 2h 恢复复查窗；引用闸；记忆写保护 guard |
| `services/scheduling/` | 453 | 分层推送（日 30 字/周模式/月报告）：无常驻进程，Hermes cron 触发 `push_tick`；幂等（周期键唯一约束）；日推限流；陪伴文案渲染 + 硬校验 |
| `integrations/hermes/` | 381 | 双插件：`cgm`（唯一 LLM 工具通道）+ `cgm_memory`（记忆 provider：prefetch 注入/对话候选捕获/会话末巩固）；DB 路径单一解析器防裂脑 |

### 1.3 支撑体系

- **规格工程**：5 个 speckit 特性目录（spec/plan/tasks/contracts），宪法 7 原则（`.specify/memory/constitution.md`），DECISION_LOG 52 条决策（代码注释 Dxxx 引用可解析性由守卫测试强制）。
- **测试**：524 用例，含守卫类测试（双轨隔离、citation 闸、companion 文案黑名单、插件 manifest==运行时注册、决策引用可解析）。
- **验收管线**：`cgm-agent simulate` 14 天 1 分钟节奏全量回放实测 `ok / 0 issues / 全不变量为真`。

---

## 2. 实现方向上的价值

1. **"LLM 只做叙事，数值全部确定性"的医疗 AI 架构范式**。所有血糖指标、事件、安全判定由可复现代码计算，LLM 仅承担生活语言表达与协商式交互。这直接回应了医疗 LLM 应用最大的落地障碍（幻觉数值），并且是**可审计**的：每次工具调用带证据引用链（evidence_refs）与审计日志。
2. **本地优先的隐私架构**。全部健康数据在本机加密 SQLite，出站仅 PHI 白名单过滤后的聚合元数据（deny-by-default 代码级边界），对比云端 CGM 产品（Dexcom Clarity、LibreView）是差异化立足点；与 2026 年学界方向（隐私保护 CGM 问答 Agent）同频。
3. **面向真实中国用户的工程化**：微泰 AiDEX（1 分钟节奏、mmol/L 生态）作为一等公民——采样节奏自适应、噪声门、单位偏好、中文生活语言叙事，这些是国际开源方案（面向 Dexcom/Libre 5 分钟节奏）没有覆盖的。
4. **记忆沉淀而非会话记忆**。L1→L2→L3 阈值门控巩固 + 遗忘 + 双时间有效期，将"和用户长期相处"落成可验证的数据结构，而不是 prompt 里的一段历史。

## 3. 项目核心优点

1. **安全设计是代码而非提示词**：三区路由硬编码、引用闸不可绕过、陪伴文案黑名单 raise、双轨物理隔离、KB 只读断言——全部有守卫测试锁定（宪法 I/II/III 落地为 CI）。
2. **决策可追溯性罕见地完整**：52 条 DECISION_LOG + 3 份 ADR + speckit 全套工件，且"代码引用的决策必须存在"是一条会跑的测试。
3. **验收以真实运行为准**：14 天全链路回放管线带审计不变量（推送幂等、分析确定性、计数守恒），发现过单元测试永远发现不了的整链缺陷（本轮修复的 coverage 崩溃即由它捕获）。
4. **平台边界克制**：不改 Hermes 安装树、无常驻进程、调度归 cron、策略/内容/状态归能力层——升级 Hermes 不破坏本项目。
5. **测试密度**：测试代码量 ≈ 源码的 68%，含大量行为级守卫。

## 4. 重点可介绍的方向

对外介绍（技术分享/README/演示）建议主打：

1. **"确定性医学 + 协商式叙事"双轨架构**——一张图讲清 F3/F4 隔离 + 引用闸 + 三区路由，这是最有辨识度的设计。
2. **L0–L3 记忆金字塔实测演示**——14 天回放后现场展示：warm digest、假设状态机推进、静默即认可、prefetch 注入文本（本报告 §1.2 memory 行）。
3. **噪声→假记忆的踩坑与修复故事**（D052）——"1 分钟设备的 ±3.5 mg/dL 抖动如何变成 0.95 置信度的假规律"，工程叙事真实有说服力。
4. **回放验收管线**——`cgm-agent simulate` 一条命令跑完摄取→分析→记忆→推送→报告并输出不变量审计，适合作为 demo 入口。

---

## 5. 下一阶段建议（按优先级）

| # | 事项 | 依据 | 阻塞 |
|---|---|---|---|
| 1 | **Damocles 签核 D052 速率门** + 复核"suspect 点计入红区"、"引用闸 70.0≠70" 两项医学策略 | 宪法 I 人审要求 | 人 |
| 2 | **Live Hermes LLM 对话验收**：DeepSeek 凭证 + 真 Hermes 环境，按 `HERMES-14D-REALTIME-E2E-PROMPT.md` 清单过一轮（重点：工具调用纪律、人格保真、红区话术） | 发布前检查 §二 | 凭证/环境 |
| 3 | **推送最后一公里**：`push_tick` 在 `CGM_WEBHOOK_URL` 配置时自动触发 webhook 投递，或固化 cron 双步模板 + E2E 用例 | 发布前检查 P1 | 无（可开发） |
| 4 | **节奏参数贯通**：入库时按 source 记录设备节奏，scheduler/L0/builder 的 detector 从 DB 读取（消除 5 分钟默认残留） | 发布前检查 P1 | 无（可开发） |
| 5 | AiDEX 实机验证（厂商 API → BLE PoC，按 ADR-0002 顺序） | F2 主线 | 设备 |
| 6 | 机会性技术债：cli.py(1,477)/builder.py(1,214) 拆分；渲染层单位偏好全覆盖 | F6 | 无 |

其中 #3、#4 无外部依赖，是自动化开发可直接推进的下一步。
