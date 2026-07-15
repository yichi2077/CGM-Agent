# Hermes CGM-Agent 综合审计与 MVP 改进计划报告

> **文档用途**：本文档汇总了对 `hermes_cgm_agent` 项目进行的 6 轮独立审计的完整结果，供另一个 agent 进行复核与执行。
> **审计对象**：`develop` 分支 HEAD `de67892`（2026-07-13）
> **审计时点**：2026-07-15
> **审计方法**：6 轮子代理独立研判 + 互相竞争性分析，涉及代码逐行核查、联网调研真实用户痛点
> **项目定位**：个人开发的 demo 级 CGM 陪伴 AI agent，基于 Hermes Agent 构建，目标是 E2E 真实项目可合并/可使用，非真实用户层交付

---

## 目录

- [0. 项目背景与定位](#0-项目背景与定位)
- [1. 第一阶段：代码进度审计](#1-第一阶段代码进度审计)
- [2. 第二阶段：产品审计（PM 视角）](#2-第二阶段产品审计pm-视角)
- [3. 第三阶段：重新定标（demo 视角）](#3-第三阶段重新定标demo-视角)
- [4. 第四阶段：双视角研判（PM + 用户，含联网调研）](#4-第四阶段双视角研判pm--用户含联网调研)
- [5. 第五阶段：三角审判（医疗工程 + PM + 用户）](#5-第五阶段三角审判医疗工程--pm--用户)
- [6. 第六阶段：四方竞争性分析](#6-第六阶段四方竞争性分析)
- [7. 最终改动计划（分级 List）](#7-最终改动计划分级-list)
- [8. 代码 Reference 索引](#8-代码-reference-索引)
- [9. 信源清单](#9-信源清单)

---

## 0. 项目背景与定位

### 0.1 一句话定位

**CGM-Agent 是一个跑在 Hermes Agent 之上的「个人连续血糖监测（CGM）陪伴 AI」能力层**——它不自己当聊天壳，而是把血糖数据 ingest、记忆合成、安全路由、报告叙事、主动推送等能力以 18 个工具的形式暴露给 Hermes，由 Hermes 统一对话。

### 0.2 核心产品命题

把冷冰冰的 CGM 时序数据，变成一个**「知情陪伴者（Informed Companion）」**——它看过你的历史，但只分享观察、不下指令、不评判对错，让你做的每个选择都是"知情的"而非"被指导的"。

三个反常识的产品决策：
1. **反监督者**：永远不说"你昨天血糖不好"
2. **反同谋者**：永远不说"放心吃吧"
3. **反教科书**：默认输出 30-80 字微信式短句，不堆 TIR/TAR/CV 等术语

### 0.3 工程规模

- 28475 行代码 / 147 模块 / **870 测试全绿（skipped=3）**
- 零 TODO/FIXME/NotImplementedError 残留
- 5 个 spec 共 121 个任务 100% 完成，每个 feature 有 spec/plan/tasks/checklist/quickstart 五件套

### 0.4 远程分支状态

远程 5 个分支：
- `develop` — 开发分支（当前所在，干净，与 origin 同步）
- `main` — 用户用分支（07-13 从 develop 同步运行时）
- `claude/cgm-event-detection-sensitivity-q721d7` — 保留（analytics 修复未进 main）
- `claude/glucose-system-review-qrmw6e` — 保留（replay engine + 记忆评测未进 main）
- `claude/tender-sammet-46e951` — 保留（MAGE 指标 + 报告记忆 E2E 未进 main）

**重要发现**：develop 分支丢失了 3 个 claude/* 分支的部分内容（详见第 1 节）。

---

## 1. 第一阶段：代码进度审计

### 1.1 主线功能完成状态

| 模块 | 状态 | 说明 |
|---|---|---|
| F1 Hermes 运行可用性 | ✅ 完成 | DB 路径统一 + 事件 schema + memory 工具可达 |
| F2 数据源 | ⚠️ 代码 DONE 待真机 | D062 Juggluco 桥代码完整，真机未验证 |
| F3 医学安全硬化 | ✅ 完成 | 引用守卫 + kb.approve + 红区恢复（KB 签核为概念性留白） |
| F4 陪伴者叙事 | ✅ 完成 | 协商式话术 + push 合规 + 升级闭环 |
| F5 推送投递闭环 | ✅ 完成 | push_tick + webhook + email + 静默即认可 |
| F-SIM005 仿真管线 | ✅ 完成 | SimClock + CSV replay + 审计 + Hermes preflight |
| 记忆系统 | ✅ 完成 | Hot/Warm/Cold + L0 + 双时间 + 巩固遗忘 |
| RAG 系统 | ✅ 完成 | 双轨隔离 + 引用守卫 + 摄取管线 + 回归门 |
| 感情化定位 | ✅ 基本完成 | 人格 + 话术 + 升级；情感编排缺代码 |

### 1.2 develop 分支丢失内容（关键发现）

develop 是 squash 单提交，未完整吸收 3 个 claude/* 远程分支的内容。逐项文件内容核实：

| 丢失内容 | 来源分支 | develop 现状 | 影响 |
|---|---|---|---|
| **replay engine**（`services/replay/` 整个目录） | claude/glucose-system-review | ❌ 不存在 | 丢失加速回放引擎 + 记忆效能评测 |
| **eval_recall.py**（记忆 with/without 对比评测） | claude/glucose-system-review | ❌ 不存在 | 丢失"记忆有无对比"的 demo 能力 |
| **test_replay_engine.py / test_eval_memory.py / test_delivery_channels.py** | claude/glucose-system-review | ❌ 不存在 | 丢失对应测试 |
| **rapid rate detection resolution-independent 修复** | claude/cgm-event-detection-sensitivity | ❌ events.py 硬编码 5min | 速率检测在不同采样间隔下可能有 bug |
| **test_e2e_report_memory_recall.py** | claude/tender-sammet | ❌ 不存在 | 丢失"报告→记忆回忆"E2E 测试 |
| MAGE 指标 | claude/tender-sammet | ✅ 已在 develop | 无丢失 |

**AI 工程视角补充核查（第六阶段）**：resolution-independent 修复并非"全丢"，而是"未统一到主路径"——[scheduler.py:43-60](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L43-L60) 的 `_cadence_tuned_detector` 用 `median_interval_minutes` 做了局部 cadence 适配，但只在 scheduler 的 `consecutive_anomaly_days`/`_should_trigger_daily_trend` 生效，主事件检测/报告/consolidation 派生路径仍用 5min 默认。

### 1.3 产品承诺兑现核实（6 项逐项核查）

| # | 核实项 | 结论 | 关键证据 |
|---|--------|------|----------|
| 1 | 中文叙事+受众分层+TIR翻译 | **已实现** | [observations.py:50-77](file:///workspace/src/hermes_cgm_agent/services/reports/sections/observations.py#L50-L77); [daily_card.py:86-121](file:///workspace/src/hermes_cgm_agent/services/reports/sections/daily_card.py#L86-L121); [narrative_templates.py:305-348](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L305-L348) |
| 2 | 安全路由硬编码三区 | **已实现** | [router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46), [L319-361](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L319-L361); [builder.py:188-201](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L188-L201) |
| 3 | 情感优先原则代码支撑 | **仅 prompt 层** | [provider.py:211-223](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py#L211-L223), [L641-802](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py#L641-L802) |
| 4 | 3 段升级关心 | **已实现** | [memory.py:45-69](file:///workspace/src/hermes_cgm_agent/domain/memory.py#L45-L69); [builder.py:477](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L477); [observations.py:151-154](file:///workspace/src/hermes_cgm_agent/services/reports/sections/observations.py#L151-L154) |
| 5 | 静默即认可+静默策略 | **已实现** | [scheduler.py:203-205](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L203-L205), [L267-306](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L267-L306), [L387-440](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L387-L440) |
| 6 | 协商式四状态机接线报告 | **已实现** | [builder.py:528-537](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L528-L537); [patterns.py:90-126](file:///workspace/src/hermes_cgm_agent/services/reports/sections/patterns.py#L90-L126); [narrative_templates.py:279-302](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L279-L302) |

### 1.4 KNOWN_ISSUES 过时条目

[KNOWN_ISSUES.md](file:///workspace/KNOWN_ISSUES.md) 第 2 条说"月报叙事模板尚未实现"，但代码核实 [monthly.py](file:///workspace/src/hermes_cgm_agent/services/reports/sections/monthly.py) + [narrative_templates.py:362-430](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L362-L430) 已完整实现月报 summary + MoM 环比。**文档与代码脱节**。

---

## 2. 第二阶段：产品审计（PM 视角）

### 2.1 交互视角

#### 强项
1. **人格定义深度罕见**：[SOUL.md](file:///workspace/SOUL.md) 250 行"存在论级"人格设计，明确区分监督者/同谋者/知情陪伴者三种角色。
2. **句式禁忌有代码硬守卫**：[narrative_templates.py:18-30](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L18-L30) 用正则黑名单拦截 TIR/TAR/GMI/CV 等 13 个临床缩写 + "建议你/必须/确诊"等断言短语。
3. **受众分层是真分叉**：[observations.py:50-77](file:///workspace/src/hermes_cgm_agent/services/reports/sections/observations.py#L50-L77) 对同一份数据，SELF 版给生活语言，FAMILY 版给"今天整体平稳"，CLINICIAN 版保留"TIR X%"。

#### 严重问题
1. **「情感优先于数据」是空话**：SOUL 承诺"用户表达情绪时先回应情绪再看数据"，但代码层零情感检测。[provider.py:211-223](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py#L211-L223) 只把 SOUL 压缩进 system prompt 让 LLM 自觉。
2. **弱势人群"第 3 天"承诺落空**：SOUL 说弱势人群"第一天、第三天、第五天"升级，但 [memory.py:45-69](file:///workspace/src/hermes_cgm_agent/domain/memory.py#L45-L69) 只在 `>=1` 和 `>=5` 设触发点，第 3 天无独立升级。
3. **"用户显式每日推送偏好"未实现**：SOUL 说"如果你设置了每日推送偏好"，但代码默认是触发式推送，无用户可配开关。
4. **状态标签与文档不一致**：SOUL/PRD 写 `invalid`，代码用 `archived`（[memory.py:32-37](file:///workspace/src/hermes_cgm_agent/domain/memory.py#L32-L37)）。

### 2.2 产品质量视角

#### 强项
1. **工程质量极高**：870 测试全绿，零技术债残留。
2. **安全是结构性的**：三区路由器纯代码硬编码，红区真截断叙事，阈值 `<54 / >250` 比 PRD 的 300 更保守，红区有 10 分钟持续门控。
3. **审计可追溯**：每个工具 `audit=true`，webhook 投递记 `delivery_url_domain`（非完整 URL）。
4. **幂等性严谨**：`push_events` UNIQUE 约束兜底。

#### 严重问题
1. **KB 临床签核 0/580**：[authoritative_kb.json](file:///workspace/src/hermes_cgm_agent/knowledge/authoritative_kb.json) 580 张医学卡片 100% `verified=false`。
2. **真机数据零验证**：全部测试基于合成数据。
3. **单用户假设无权限隔离**：[KNOWN_ISSUES.md](file:///workspace/KNOWN_ISSUES.md) 第 4 条自认。

### 2.3 产品 Feature 视角

#### 强项
1. F1-F5 主线 Feature 全部完成且有 spec 闭环。
2. F4 协商式假设验证是真产品创新。
3. F5 静默策略兑现"陪伴不是监控"。

#### 严重问题
1. **F2 数据源是最弱的一环**：至今没有跑通过一个真实传感器的数据。
2. **F7 分析深度半残**：AGP 百分位可视化 DEFERRED。
3. **F4 弱势人群免责声明休眠**：依赖上游写 `vulnerable_population` 字段，而上游没写。

### 2.4 竞争力评估

**结论：在"产品概念与工程严谨度"维度具备领先竞争力，但在"可交付性"维度尚不具备完整竞争力。**

强竞争力所在：
1. "知情陪伴者"人格 + 协商式假设验证是真正的产品创新
2. 安全路由 + 引用守卫 + 双轨隔离的三重硬门
3. 工程闭环质量在个人项目里是天花板水准

竞争力缺口：
1. 没有真实数据 = 没有产品
2. KB 0 签核 = 医学可信度为零
3. AGP 缺失 = 医生版报告不完整

---

## 3. 第三阶段：重新定标（demo 视角）

> 用户明确：项目是个人开发、基于个人目的使用的 demo 级系统，不需要真实用户层使用，只需展示概念和产品价值。目标是 E2E 级真实项目可合并。

### 3.1 核心关注点重新确认

#### 长短期记忆系统 — 最扎实的部分，已达到概念展示完整度

| 层 | 设计 | 实现 | 证据 |
|---|---|---|---|
| Hot | L2 画像 + L3 假设直取注入 | ✅ | [assembler.py](file:///workspace/src/hermes_cgm_agent/services/memory/assembler.py) |
| Warm | consolidation 合成日/周摘要 | ✅ 核心 | [consolidation.py](file:///workspace/src/hermes_cgm_agent/services/memory/consolidation.py) |
| Cold | L1 情景档案 BM25/可选语义检索 | ✅ | [retrieval.py](file:///workspace/src/hermes_cgm_agent/services/memory/retrieval.py) |
| L0 | 14 天时序渐进衰退压缩 | ✅ | [l0_builder.py](file:///workspace/src/hermes_cgm_agent/services/memory/l0_builder.py) |
| 双时间 | L2/L3 `valid_from/valid_to` + supersede | ✅ | [repository.py](file:///workspace/src/hermes_cgm_agent/services/memory/repository.py) |
| E2E 闭环测试 | 数据→事件→L1→L2/L3→recall | ✅ | [test_e2e_memory_recall.py](file:///workspace/tests/test_e2e_memory_recall.py) |

**关键缺陷（AI工程视角第六阶段发现）**：consolidation 定时触发闭环未闭合。staged L1→L2→L3 只在 `on_session_end` 触发，`push_tick` 只调 `synthesize_state`（warm digest）不调 `consolidate()`。demo 演示"记忆会成长"时如果会话非优雅退出会露馅。

#### RAG 系统 — 双轨物理隔离是真正的设计亮点

| 设计 | 实现 | 证据 |
|---|---|---|
| 双轨物理隔离 | ✅ | [memory_guard.py](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py) `assert_track_isolation` |
| 权威轨 BM25-only + 双语卡 + CJK bigram | ✅ | [authoritative.py](file:///workspace/src/hermes_cgm_agent/services/rag/authoritative.py) 580 卡 |
| 引用守卫硬门 | ✅ | [builder.py](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py) strict=True |
| 检索质量回归门 | ✅ | [eval/rag/queries.jsonl](file:///workspace/eval/rag/queries.jsonl) 73 查询 hit@3=1.0 |
| `verified` 概念性定义 | ✅ 概念完整 | 0/580 签核对 demo 是可接受的"概念性 clean 定义" |

#### 偏感情化的产品定位 — 人格定义顶级，情感编排有缺口

| 设计 | 实现 | 状态 |
|---|---|---|
| "知情陪伴者"人格 | [SOUL.md](file:///workspace/SOUL.md) 250 行 | ✅ 顶级 |
| 句式禁忌代码硬守卫 | [narrative_templates.py:18-30](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L18-L30) | ✅ |
| 协商式假设四状态话术 | [narrative_templates.py:279-302](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L279-L302) | ✅ |
| 3 段升级关心 | [memory.py:45-69](file:///workspace/src/hermes_cgm_agent/domain/memory.py#L45-L69) | ✅ |
| 静默即认可 | [scheduler.py:267-306](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L267-L306) | ✅ |
| **"情感优先于数据"** | 仅 SOUL.md prompt 注入 | ⚠️ 无代码编排 |

### 3.2 demo 维度判定

**已达到"E2E 真实项目可合并/可使用"水准**。E2E 数据闭环已验证、仿真管线已验证、Hermes 集成 E2E 已验证、870 测试全绿、spec 五件套闭环。

---

## 4. 第四阶段：双视角研判（PM + 用户，含联网调研）

### 4.1 联网调研发现的真实痛点（带信源）

| # | 痛点 | 量化证据 | 信源 |
|---|---|---|---|
| P1 | 警报疲劳 | 56% 情绪负担、31% 忽略警报、50% 错误处置 | [Medscape/ADA 2025](https://www.medscape.com/viewarticle/diabetes-technology-lifesaving-and-stressful-2025a1000ljj) |
| P2 | 数据过载看不懂 | "一座数据山"、TIR 缩写看不懂 | [TechNewsVision](https://technewsvision.co.uk/continuous-glucose-monitoring-made-me-continuously-crazy/) |
| P3 | 被数据审判焦虑 | "数字焦虑""饮食恐惧""被数据绑架" | [什么值得买](https://post.smzdm.com/p/a6z37ken/) |
| P4 | 医生没时间解读 | 15 分钟门诊、医生也嫌警报多（61%） | [Clin Diabetes 2024](https://diabetesjournals.org/clinical/article/doi/10.2337/cd23-0005/153502/) |
| P5 | 家属担心 | "我女儿的生命依赖，App 却不工作" | [LibreLinkUp 评论](https://grand-screen.com/apps/librelinkup/reviews/) |
| P6 | 食物归因困难 | "刚才那块蛋糕是不是元凶" | 真实用户原声 |
| P7 | 夜间低血糖恐惧 | 25% 的夜晚发生、家长 3 点起床 | [ADA Clin Diabetes](https://pmc.ncbi.nlm.nih.gov/articles/PMC8061550/) |
| P8 | 依从性下降/倦怠 | 31% 忽略警报→错误行为→摘机 | [Stanford/Joslin](https://pdfs.semanticscholar.org/f178/757a1f1388ce0475c130ac8b0346127bf8be.pdf) |

### 4.2 痛点-产品触达矩阵（PM 视角）

| 痛点 | 触达深度 | 评注 |
|---|---|---|
| P3 被审判焦虑 | **治本** | SOUL.md 全套反二次伤害设计是项目立身之本 |
| P2 数据过载 | **治标偏治本** | 叙事翻译+短卡+缩写硬门+静默 |
| P4 医生没空 | **治标偏治本** | 医生版结构化摘要+主动复诊提议 |
| P8 依从性 | **治标** | 情感依从层做对了，但覆盖不到设备倦怠 |
| P6 食物归因 | **治标** | 哲学严谨但能力停在时段级 |
| P5 家属担心 | **仅"看不懂"子痛点** | 没触达"实时担心"核心 |
| P7 夜间低血糖 | **仅提及** | 且默认静默政策会吞掉单个夜间低 |
| P1 警报疲劳 | **未触达** | 定位决定不碰实时告警 |

### 4.3 PM 视角的"PM 想象的痛点"

1. **协商式四状态机 + 静默即认可**：交互模型优雅，但真实用户极少会跟状态机多轮验证假设。
2. **双轨 RAG / ClaimCard / 引用硬门 / KB 签核链**：合规洁癖驱动的防御设计，对个人 demo 用户不构成痛点触达。
3. **L0 渐进衰退 / consolidation 梦境合成**：技术精巧但用户完全感知不到——这是给 LLM 的，不是给用户的。

### 4.4 结构性错位

> 项目把大量精力投在叙事质量/合规/记忆架构（工程师洁癖），而 CGM 用户最痛的警报疲劳/实时夜间恐惧/设备本身的数字焦虑，因"知情陪伴者"定位被整体让渡给了设备本身。**它把"被 AI 二次伤害"的焦虑治得很好，但 CGM 用户最大的焦虑来源是设备本身和数据本身，不是 AI。**

### 4.5 用户视角易用性评估矩阵

| 评估项 | 真实用户期望 | 项目现状 | 严重度 |
|---|---|---|---|
| 安装门槛 | 下载 App 5 分钟 | Python+Hermes+pip+50+ 环境变量 | 🔴 致命 |
| 数据接入 | 自动蓝牙 | 安卓开 HTTP 服务+路由器+cron | 🔴 致命 |
| 首次体验 | 引导式 onboarding | 英文 CLI + 读大量文档 | 🔴 严重 |
| 日常交互 | 手机/手表/推送 | CLI + Hermes 对话，无移动端 | 🔴 严重 |
| 对话/报告可读性 | 生活语言不砸术语 | SOUL 三受众分级+微信式短句 | 🟢 优秀 |
| 警报体验 | 可控不疲劳 | 三区路由+静默+升级关怀 | 🟢 良好 |
| 老人/非技术友好 | 大字/语音/简单 | 无 GUI/无语音/CLI 英文 | 🔴 致命 |
| 残障 accessible | 听觉/视觉/触觉替代 | 零适配 | 🔴 致命 |
| 家属共享 | 一键关注看备注 | 有家属版叙事但单用户、投递未验证 | 🟡 中等 |
| 单位可切 | mmol/L 可切 | `CGM_AGENT_DISPLAY_UNIT` 可切 | 🟢 良好 |
| 数据导出/删除 | 一键导出/删除 | 无独立命令 | 🟡 中等 |

### 4.6 双视角交叉印证

1. **SOUL.md 是项目最强资产**——PM 视角说它是"反二次伤害的最体系化设计"，用户视角说它"比商业 App 更体贴患者"。
2. **"接入端"是最大缺口**——PM 指出项目"避开了最痛的实时警报/夜间恐惧"，用户指出"非技术用户连装都装不上"。
3. **PM 想象 vs 真实痛点的错位**——两个视角都独立指出协商式四状态机、双轨 RAG 签核链、L0/consolidation 这些工程精巧之处对真实用户痛点触达有限。

---

## 5. 第五阶段：三角审判（医疗工程 + PM + 用户）

> 审查焦点：**那些为保证安全或可用性而设计的功能，是否真的达成了目的？**

### 5.1 医疗工程视角：五层防御体系实装核查

#### 防线 ① — 三区路由器：✅ 硬编码，真拦截

| 设计点 | 实装 | 判定 |
|---|---|---|
| 三区纯代码判断 | [router.py:328-361](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L328-L361) | ✅ |
| 阈值硬编码 | [router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46) `<54 / >250` | ✅ 更保守 |
| 红区截断叙事 | [builder.py:188-201](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L188-L201) | ✅ |
| 瞬时红区降级 | [router.py:340-353](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L340-L353) `<10min→黄区` | ✅ |
| 10 分钟持续门控 | [router.py:363-428](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L363-L428) | ✅ |
| 黄区前缀告警 | [builder.py:228-232](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L228-L232) | ✅ |
| 全 mg/dL 内部单位 | [router.py:77-78](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L77-L78) | ✅ |

**🔴 工程漏洞**：
- **阈值无运行时校验**：路由器直接用常量，如果 normalizer 出 bug 写入 mmol/L 的值（如 5.4 而非 97），路由器会把正常值判为红区。没有 schema validation 层在入库时拒绝 absurd 值。
- **红区模板过于简略**：[router.py:49-52](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L49-L52) 红区只输出"我无法代替医生给出建议"。对于真实出现 54 mg/dL 以下严重低血糖的患者，这句话**没有给出任何自救指引**。

#### 防线 ② — 引用守卫：✅ strict=True 硬门

| 设计点 | 实装 | 判定 |
|---|---|---|
| 报告交付 strict=True 阻断 | [citation_guard.py:26-60](file:///workspace/src/hermes_cgm_agent/services/safety/citation_guard.py#L26-L60) | ✅ |
| 精确 token 匹配 | [citation_guard.py:37-39](file:///workspace/src/hermes_cgm_agent/services/safety/citation_guard.py#L37-L39) | ✅ |
| 15/30 不豁免 | [citation_guard.py:75-82](file:///workspace/src/hermes_cgm_agent/services/safety/citation_guard.py#L75-L82) | ✅ |

**🔴 工程漏洞**：
- exemption 列表 `{1,2,3,7,14}` 过宽。
- 引用守卫只校验数字，不校验医学结论的逻辑正确性。

#### 防线 ③ — 双轨记忆隔离：✅ 物理隔离 + RuntimeError + 只读守卫

| 设计点 | 实装 | 判定 |
|---|---|---|
| Track 互斥检查 | [memory_guard.py:36-68](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py#L36-L68) | ✅ |
| KB mutator denylist | [memory_guard.py:75-82](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py#L75-L82) | ✅ |
| Conflict resolution 权威胜出 | [memory_guard.py:112-123](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py#L112-L123) | ✅ |
| KB 信任分级检索 | [authoritative.py:64-78](file:///workspace/src/hermes_cgm_agent/services/rag/authoritative.py#L64-L78) | ✅ |

**🟡 工程瑕疵**：
- `assert_track_isolation` 对没有 `evidence_refs` 的 item 只 warning 不 raise（[memory_guard.py:46-52](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py#L46-L52)）。
- KB 580 张卡 `verified=false`，trusted-first 在没有 verified 卡时退化为"全是 auto 卡"。

#### 防线 ④ — 红区恢复二次确认：✅ 状态持久化，有并发安全

| 设计点 | 实装 | 判定 |
|---|---|---|
| 2h 恢复窗口 | [router.py:22](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L22) | ✅ |
| SQLite 持久化 | [router.py:109-164](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L109-L164) | ✅ |
| 窗口过期续期保持 original | [router.py:217-227](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L217-L227) | ✅ |
| 事务原子性 | [router.py:185-261](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L185-L261) | ✅ |
| 内存路径加锁 | [router.py:95](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L95) | ✅ |

#### 防线 ⑤ — PHI 保护：✅ 应用层加密 + 权限硬ening

| 设计点 | 实装 | 判定 |
|---|---|---|
| Fernet 对称加密 | [sqlite.py:105-170](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L105-L170) | ✅ |
| PHI 字段全 seal | [data/repository.py:192-201](file:///workspace/src/hermes_cgm_agent/services/data/repository.py#L192-L201) | ✅ |
| 密钥文件 0600 | [sqlite.py:136-143](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L136-L143) | ✅ |
| WAL/SHM sidecar 也 0600 | [sqlite.py:682-695](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L682-L695) | ✅ |
| Dexcom/AiDEX token 加密 | [dexcom/tokens.py:70-73](file:///workspace/src/hermes_cgm_agent/services/dexcom/tokens.py#L70-L73) | ✅ |
| webhook PHI allowlist | [delivery.py:30-83](file:///workspace/src/hermes_cgm_agent/services/tools/handlers/delivery.py#L30-L83) | ✅ |
| webhook 拒绝 3xx 重定向 | [delivery.py:38-57](file:///workspace/src/hermes_cgm_agent/services/tools/handlers/delivery.py#L38-L57) | ✅ |
| SQL 注入防御 | [sqlite.py:77-102](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L77-L102) | ✅ |
| 审计日志加密 | [sqlite.py:717](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L717) | ✅ |

**🔴 工程漏洞**：
- 无传输层安全验证（无证书 pinning）。
- Fernet 密钥无轮换机制。
- 无 DB 完整性校验（无 checksum 防静默篡改）。
- Webhook 投递无重试（[delivery.py:26-27](file:///workspace/src/hermes_cgm_agent/services/tools/handlers/delivery.py#L26-L27) "single at-most-once call, no retry"）。

#### 防线 ⑥ — 可靠性工程：✅ 幂等完备

| 设计点 | 实装 | 判定 |
|---|---|---|
| 推送幂等（UNIQUE 约束） | [sqlite.py:548-556](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L548-L556) | ✅ |
| 推送幂等（应用层先查） | [scheduler.py:233](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L233) | ✅ |
| 葡萄糖点去重 | [sqlite.py:342](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L342) | ✅ |
| 线程安全事务 | [sqlite.py:190-193](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py#L190-L193) | ✅ |

### 5.2 医疗工程追加发现（第六阶段子代理核实）

**入库值无 range 校验且安全路由器不过滤 SUSPECT——这是架构级反向不对称**：
- [normalizer.py:117-134](file:///workspace/src/hermes_cgm_agent/services/data/normalizer.py#L117-L134) 仅打 `SUSPECT` 仍入库
- [router.py:319-361](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L319-L361) 安全路由器完全不读 `quality_flag`
- [builder.py:153-157](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L153-L157) 报告管线把含 SUSPECT 的 points 直喂路由
- **对比**：`realtime.py:89` 和 `memory/provider.py:336` 都过滤 `quality_flag=="valid"`，唯独最该保守的安全路由器没过滤

**README 阈值文档漂移**：
- [README.md:75-78](file:///workspace/README.md#L75-L78) 声称红区 `>300`、黄区 `250-300`
- [router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46) 实际红区 `>250`、黄区高侧 `180-250`
- 代码比文档更保守（方向安全），但文档会误导

### 5.3 三角对质：三方共识与分歧

| 议题 | 医疗工程 | 产品经理 | 用户 |
|---|---|---|---|
| 三区路由器 | ✅ 实装精良 | ✅ 解决信任问题，但红区模板失败 | 🔴 急救时刻被免责声明挡回 |
| 引用守卫 | ✅ strict=True 硬门 | 🟡 用户不感知但必要 | ✅ 看不到幻觉=体验好 |
| 红区恢复确认 | ✅ 状态机+持久化完备 | ✅ 直击假警报疲劳 | ✅ 不被狼来了困扰 |
| 红区无急救指引 | 🔴 安全漏洞 | 🔴 产品失败 | 🔴 体验失败 |
| 静默策略 | ✅ 减少误触 | ✅ 反警报疲劳正确方向 | 🔴 夜间低血糖被吞=错的默认 |
| 静默即认可 | ✅ 状态机正确 | 🔴 伦理风险 | 🔴 没看到≠同意 |
| 双轨隔离 | ✅ RuntimeError 硬门 | 🟡 demo 语境过度设计 | — 不感知 |
| PHI 加密 | ✅ Fernet+0600+WAL | 🟡 基础分 | — 不感知 |
| 协商式四状态机 | ✅ 代码完整 | 🟡 PM 想象的理想用户 | 🟡 用户不会多轮协商 |
| 接入门槛 | ✅ 合理（单用户本地） | 🔴 不影响 demo 价值 | 🔴 非技术用户完全被排除 |
| 升级关怀 | ✅ EscalationState 实装 | ✅ 从审判到关心 | ✅ 被关心感 |

### 5.4 三方一致认定的 3 个最该改的问题

1. **🔴 红区模板加急救指引**（三方一致：医疗工程认为安全漏洞、PM 认为产品失败、用户认为体验失败）
2. **🔴 夜间低血糖成为静默例外**（三方一致：医疗上夜间低是急救场景、PM 认为是错的默认、用户最恐惧的就是夜间低）
3. **🔴 静默即认可改方向**（PM 认为伦理风险、用户认为没看到≠同意；医疗工程上状态机本身没问题但触发方向反了）

---

## 6. 第六阶段：四方竞争性分析

> 四个子代理（医疗工程 / AI应用工程 / 产品经理 / 用户代表）独立给出各自 Top 3 并互相评估。

### 6.1 医疗工程视角的 Top 3

1. **红区模板补充急救指引（含低/高分流）**——急性、可能致命事件中只给免责声明是直接的患者伤害风险，修复成本最小。
2. **入库 range 硬校验 + 安全路由器过滤 SUSPECT**——系统性正确性缺陷：异常值污染安全路由与 2h 恢复窗口，且最该保守的安全路由器是唯一不过滤 SUSPECT 的消费者。
3. **红区推送改可靠投递（幂等重试，弃裸 at-most-once）**——严重低血糖告警漏报可致死，at-most-once 是安全关键告警的错误投递语义。

**医疗工程对其他角度的评估**：
- AI 工程"补情感检测编排"：**反对**（非安全控制，偏离安全缺口）
- PM"红区加 15-15 急救指引"：**强烈认同**（但 <54 应升级到胰高血糖素/联系急救，不能止步 15-15）
- PM"夜间低强制推送"：**有条件认同**（须先修推送可靠性 + range 校验）
- PM"静默即认可改方向"：**强烈反对**（沉默≠知情同意，当前默认正确）
- 用户"微信 bot 前端"：**反对**（放大 PHI 攻击面）
- 用户"降低安装门槛"：**中立**（前提不削弱安全默认）
- 用户"PDF 导出"：**中立偏认同**（前提过引用守卫）

### 6.2 AI 应用工程视角的 Top 3

1. **Consolidation 定时触发闭环**——staged L1→L2→L3 只在 `on_session_end` 触发，非交互期记忆管线停转，"记忆会成长"承诺不成立。改动最小（~30-50行），ROI 最高。
2. **记忆效能评测门（eval_recall.py + replay engine）**——RAG 有 73 查询 hit@3=1.0 回归门，记忆系统无任何回归门。develop 丢失此件意味着记忆层处于"无观测"飞行状态。
3. **"情感优先于数据"从 prompt 层下沉为代码编排**——核心差异化卖点当前 100% 押注 LLM 遵守 prompt，demo 翻车风险不可控不可测。

**AI 工程对其他角度的评估**：
- 医疗工程"红区加急救指引"：**部分认同/反对自由文本**（应"红区→自动召回已签核 KB 卡"的编排联动，前置依赖 KB 签核，不能凭空加急救指引）
- PM"夜间低强制推送"：**反对（demo 阶段）**（`send_os_push` 是 `pass` 未实装；夜间假低会骚扰）
- PM"静默即认可改方向"：**反对**（当前已是安全平衡点，改会破坏可逆性）
- 用户"微信 bot 前端"：**反对**（违反 AGENTS.md 项目边界）
- 用户"降低安装门槛"：**中立偏认同**（属 DX 范畴）

### 6.3 产品经理视角的 Top 3

1. **红区模板从"免责声明"升级为"免责 + 自救指引 + 主动归档"**——红区是 demo 必演场景，当前话术是定位崩塌级硬伤。投入最小（0.5 人日），D/R 双极高。
2. **夜间低血糖独立推送通道，绕开"连续 2 天"门槛**——P7 是未触达真痛点，且当前是安全漏洞。demo 可演"夜间低→次日温柔问候"核心陪伴场景。
3. **情感优先确定性编排**——P3 治本痛点 + demo 差异化卖点。当前靠 prompt 自觉不可演示不可复现。

**PM 对其他角度的评估**：
- 医疗工程"入库值 range 校验"：**中立偏反对**（demo 数据干净，不触达真痛点，工程洁癖）
- 医疗工程"DB 完整性校验"：**反对**（冗余防御）
- 医疗工程"密钥轮换"：**反对**（部署运维事项）
- AI 工程"合并 replay engine"：**反对**（工程整洁诉求，不触达痛点）
- AI 工程"consolidation 接 cron"：**中立偏反对**（PM 已确认的想象痛点）
- 用户"微信 bot 前端"：**认同但受 AGENTS.md 约束**（应作为独立 worktree）
- 用户"降低安装门槛"：**强烈认同**（F1 阻断级，但归属 F1 不归属本轮）
- 用户"PDF 导出"：**中立**

### 6.4 用户代表视角的 Top 3

1. **红区免责声明→方向化急救指引**——生命安全、改动极小、demo 必须。KB 里 15-15 法则卡片已就绪。
2. **危急值实时推送通道（夜间低不再被静默）**——直击"夜间低被静默"致命缺口。一个在凌晨 3 点不响的陪伴者不是陪伴者。
3. **安装门槛降低（先做"一键 demo"那半）**——demo 与"真实用户能用"之间的唯一物理墙。

**用户代表对其他角度的评估**：
- 医疗工程"入库值 range 校验"：**中立偏认同（真实部署）/ 反对进入 Top**（用户不可见，demo 场景失效）
- AI 工程"补情感检测编排"：**反对**（Agent 不在场时谈情感检测是错误对象）
- AI 工程"合并 replay engine"：**反对**（纯内部工程整合，零用户可见价值）
- PM"红区加 15-15 急救指引"：**强烈认同**
- PM"夜间低强制推送"：**强烈认同**
- PM"静默即认可改方向"：**认同**（沉默=同意是高风险 UX）

**用户代表追加发现**：`memory_audit_output.txt` 显示真实存在凌晨 03:10 的 47.4/45.5 mg/dL 夜间低值事件（红区），但当前架构下这类事件只能等次日 9 点 daily 摘要才可能被提及——这是"夜间低被静默"的活体证据。

### 6.5 四方共识地图

| 改进项 | 医疗工程 | AI工程 | PM | 用户 | 共识度 |
|---|:---:|:---:|:---:|:---:|---|
| **红区加急救指引** | ✅ Top1 | ⚠️ 认同方向但要求走KB签核管线 | ✅ Top1 | ✅ Top1 | **4/4 强共识** |
| **夜间低不再被静默** | ⚠️ 有条件认同 | ❌ 反对 | ✅ Top2 | ✅ Top2 | **3/4 共识** |
| **静默即认可改方向/加过滤** | ❌ 强烈反对 | ❌ 反对 | ✅ Top3 | ✅ 认同 | **2/4 认同** |
| **情感优先代码编排** | ❌ 反对 | ✅ Top3 | ✅ Top3 | ❌ 反对 | **2/4 认同** |
| **consolidation 定时触发** | — | ✅ Top1 | ❌ 反对 | — | **1/4** |
| **记忆效能评测门** | — | ✅ Top2 | ❌ 反对 | — | **1/4** |
| **入库 range 校验+路由器过滤 SUSPECT** | ✅ Top2 | ⚠️ 中立偏认同 | ❌ 反对 | ⚠️ 中立 | **1.5/4** |
| **红区推送可靠投递** | ✅ Top3 | — | — | — | **1/4** |
| **KB 签核** | ✅ Top4 | ✅ Top5 | — | — | **2/4** |
| **降低安装门槛/一键demo** | ⚠️ 中立 | ⚠️ 中立偏认同 | ✅ 认同但归属F1 | ✅ Top3 | **2.5/4** |
| **微信 bot 前端** | ❌ 反对 | ❌ 反对 | ⚠️ 认同但受约束 | ✅ Top1 | **0.5/4** |

### 6.6 四方分歧的关键裁决

#### 分歧 1：红区急救指引——怎么实现？

- PM/用户：直接改 `RED_ZONE_TEMPLATE` 文本，按低/高分流
- AI 工程：反对自由文本，要求"红区→自动召回已签核 KB 卡"的编排联动，前置依赖 KB 签核
- 医疗工程：<54 应升级到胰高血糖素/联系急救，不能止步 15-15

**裁决**：采用"模板分流 + KB 卡召回"混合方案。
- 红区模板按 `direction` 分流（低/高），改动小、立即见效
- 低血糖红区指引分两级：54-70 给 15-15 法则（轻中度），<54 给"胰高血糖素/联系急救"（重度）
- 模板内嵌固定急救话术（ADA 标准化指引，非 LLM 生成），不依赖 KB 签核——15-15 法则是 ADA 公开标准，自由文本引用不构成"医学幻觉"风险
- 保留"我无法代替医生"的 defer 尾句，但前置急救指引——"先救命再 defer"

#### 分歧 2：夜间低血糖推送——demo 阶段该不该做？

- PM/用户：强烈认同
- AI 工程：反对（`send_os_push` 是 `pass` 未实装、夜间假低会骚扰）
- 医疗工程：有条件认同（须先修推送可靠性 + range 校验）

**裁决**：做，但限定为"次日早晨强制推送"而非"实时叫醒"。
- AI 工程的"send_os_push 是 pass"论点成立——实时 OS 推送在 demo 环境无法演示
- 但 PM/用户的核心理由成立：当前 `_should_trigger_daily_trend` 要求"连续 2 天同时段异常"才推，单次夜间低被吞——这是错的默认
- 折中：在 `_should_trigger_daily_trend` 增加一条短路——"当日检出 OVERNIGHT_LOW 事件即强制触发次日 daily 推送"（不叫醒，次日早晨温柔问候）。复用现有 daily 推送管道，不需 OS 推送实装，不引入假低风险（OVERNIGHT_LOW 已经过事件检测器确认）

#### 分歧 3：静默即认可——改还是不改？

- PM：认同改方向
- 医疗工程/AI 工程：强烈反对改方向
- 用户：认同收紧

**裁决**：不改方向，但加 medical/safety candidate 过滤。
- 医疗工程和 AI 工程的论点更有力：当前 `apply_silent_consent` 只做 candidate→observing（不碰 stable），全程可逆，是合理设计
- 但 PM 发现真实漏洞：[scheduler.py:280-289](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L280-L289) 的 docstring 声称"NEVER auto-accepts safety/medical content"，但代码里没有 category 过滤逻辑——是注释承诺不是代码保证
- 折中：给 `L3Hypothesis` 加 `category` 字段（behavioral/medical/safety），`apply_silent_consent` 跳过非 behavioral。不改方向（仍 candidate→observing），但兑现 docstring 的安全承诺

#### 分歧 4：情感优先编排——现在做还是延后？

- PM/AI 工程：认同做
- 医疗工程/用户：反对

**裁决**：延后到 P1，不进 Top 3。
- 用户代表的论点最有说服力："一个能精准识别沮丧却睡过凌晨低值的 Agent，对用户毫无价值"
- 在夜间低和红区急救没解决前，情感编排是"在错误对象上打磨"

#### 分歧 5：consolidation 定时触发——做不做？

- AI 工程：Top1
- PM：反对（用户不感知，想象痛点）

**裁决**：做，但排 P1 不进 Top 3。
- AI 工程的事实发现无法反驳：`push_tick` 只调 `synthesize_state` 不调 `consolidate()`
- 但改动极小（~30-50 行），ROI 高，可作为"顺带修"项

---

## 7. 最终改动计划（分级 List）

### 🔴 P0 — 必须做（demo 概念展示 + 安全硬伤，三方以上共识）

| # | 改进项 | 共识度 | 改动规模 | 核心理由 | 关键证据 |
|---|---|---|---|---|---|
| **1** | **红区模板分流急救指引** | 4/4 | 小（0.5人日） | 急性事件中主动扣留自救指引=直接伤害风险；demo 必演场景当前露怯 | [router.py:49-52](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L49-L52) |
| **2** | **夜间低血糖次日强制推送** | 3/4 | 中（1人日） | 单次夜间低被静默吞掉=错的默认；SOUL"陪伴"承诺的试金石 | [scheduler.py:387-440](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L387-L440) |
| **3** | **README 阈值文档漂移修正** | 新发现 | 极小（10分钟） | README 写 red>300，代码是 >250；安全文档与代码打架 | [README.md:75-78](file:///workspace/README.md#L75-L78) vs [router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46) |

**P0 合计约 1.5 人日**。

#### P0-1 红区模板分流急救指引 — 详细实现方案

**当前代码**（[router.py:49-52](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L49-L52)）：
```python
RED_ZONE_TEMPLATE = (
    "这个问题涉及医疗判断，我无法代替医生给出建议。"
    "我可以帮你整理相关数据，你可以在复诊时带给医生。需要我生成报告吗？"
)
```

**问题**：对所有红区一视同仁，低血糖红区（<54 mg/dL，可致惊厥/昏迷）只给免责声明，无任何自救指引。

**改进方案**：按 `direction` 分流（`_red` 方法已计算 `direction`，见 [router.py:458](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L458)）：
- **低血糖红区 54-70**（轻中度）：给 15-15 法则——"你的血糖偏低。如果可以，先吃 15 克快速碳水（比如半杯果汁或 3-4 片葡萄糖片），15 分钟后复测。这个问题涉及医疗判断，我无法代替医生，但我可以帮你整理数据带给医生。"
- **低血糖红区 <54**（重度）：升级指引——"你的血糖很低。如果身边有胰高血糖素请按医嘱使用，或立即联系身边人/急救。15 分钟后复测。这个问题涉及医疗判断，我无法代替医生，但我可以帮你整理数据带给医生。"
- **高血糖红区 >250**：测酮/补水/就医——"你的血糖偏高。如果方便，测一下酮体，多喝水。这个问题涉及医疗判断，我无法代替医生，但我可以帮你整理数据带给医生。"

**关键约束**：
- 15-15 法则是 ADA 公开标准急救指引，非个性化建议，不构成"代替医生"
- 保留 defer 尾句——"先救命再 defer"
- 急救话术是固定文本（非 LLM 生成），不依赖 KB 签核，不引入幻觉风险
- 需绕开 SOUL 句式禁忌（"建议你"→陈述式"如果可以，先吃..."）

**涉及文件**：
- [router.py](file:///workspace/src/hermes_cgm_agent/services/safety/router.py)（模板 + `_red` 方法分流）
- 对应测试文件

#### P0-2 夜间低血糖次日强制推送 — 详细实现方案

**当前代码**（[scheduler.py:387-440](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L387-L440)）：
`_should_trigger_daily_trend` 三个触发条件：TIR 环比 ≥5%、新增 L3 candidate 假设、连续 ≥2 天同时段异常。**单次夜间低不满足任何条件 → 不触发推送**。

**改进方案**：在 `_should_trigger_daily_trend` 增加一条短路：
- 检测到当日 `OVERNIGHT_LOW` 事件（[events.py:132-134](file:///workspace/src/hermes_cgm_agent/services/analytics/events.py#L132-L134) 已有检测，`_is_overnight` 已就绪）即强制触发次日 daily 推送
- 不受"连续 2 天同时段异常"约束
- 不叫醒用户（次日早晨温柔问候，复用现有 daily 推送管道）
- 不需 OS 推送实装（AI 工程的反对论点由此化解）
- 不引入假低风险（OVERNIGHT_LOW 已经过事件检测器持续时长门控确认）

**涉及文件**：
- [scheduler.py](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py)（`_should_trigger_daily_trend` 加短路）
- 对应测试文件

#### P0-3 README 阈值文档漂移修正 — 详细实现方案

**当前代码**（[README.md:75-78](file:///workspace/README.md#L75-L78)）：声称红区 `>300`、黄区 `250-300`。
**实际代码**（[router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46)）：红区 `>250`、黄区高侧 `180-250`。

**改进方案**：改 README 几行，与代码一致。10 分钟。

---

### 🟠 P1 — 应该做（人格可信度 / 伦理硬伤 / 记忆管线完整性）

| # | 改进项 | 共识度 | 改动规模 | 核心理由 | 关键证据 |
|---|---|---|---|---|---|
| **4** | **静默即认可加 medical/safety category 过滤** | 2/4（但漏洞真实） | 小（0.5人日） | docstring 承诺"NEVER auto-accepts safety/medical"但代码无过滤——注释非保证 | [scheduler.py:280-289](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L280-L289) |
| **5** | **情感优先代码编排下沉** | 2/4（PM+AI工程） | 中（1.5人日） | 核心卖点 100% 押注 LLM 遵守 prompt，demo 翻车不可控不可测 | [provider.py:275-278](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py#L275-L278) |
| **6** | **consolidation 定时触发闭环** | 1/4（但事实硬） | 小（0.5人日） | staged L1→L2→L3 只在 on_session_end 触发，非交互期记忆管线停转 | [scheduler.py:7-9](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L7-L9) |
| **7** | **入库 range 校验 + 路由器过滤 SUSPECT** | 1.5/4 | 中（1人日） | 传感器毛刺直喂路由器→虚假红区+污染恢复基线；最该保守的消费者反而最宽松 | [normalizer.py:117-134](file:///workspace/src/hermes_cgm_agent/services/data/normalizer.py#L117-L134) + [router.py:319-361](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L319-L361) |

**P1 合计约 3.5 人日**。

#### P1-4 静默即认可加 medical/safety category 过滤 — 详细实现方案

**当前代码**（[scheduler.py:280-289](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L280-L289)）：
`apply_silent_consent` 只过滤 `state != CANDIDATE`，未过滤 medical/safety 类 candidate。docstring 声称"NEVER auto-accepts safety/medical content"，但代码里没有任何 category 标签的过滤逻辑——是注释承诺，不是代码保证。

**改进方案**：
- 给 `L3Hypothesis` 加 `category` 字段（behavioral/medical/safety）
- `apply_silent_consent` 跳过非 behavioral（即 medical/safety 类 candidate 不参与静默推进）
- 不改方向（仍 candidate→observing，不碰 stable），但兑现 docstring 的安全承诺

**涉及文件**：
- [domain/memory.py](file:///workspace/src/hermes_cgm_agent/domain/memory.py)（L3Hypothesis 加 category 字段）
- [scheduler.py](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py)（apply_silent_consent 加过滤）
- 对应测试文件

#### P1-5 情感优先代码编排下沉 — 详细实现方案

**当前代码**（[provider.py:275-278,700-705](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py#L275-L278)）：
provider 把"emotional-first"写进 system prompt 并维护一个情绪关键词列表（烦/焦虑/沮丧/累/自责/压力大），但完全依赖 LLM 自觉，没有确定性编排。

**改进方案**：
- 在 `prefetch` 或新增 `affect_router` 里加轻量情绪检测（关键词/规则即可，demo 级不必上模型）
- 命中时降级数据注入强度 + 注入共情锚点
- 在 report builder 入口前做情绪检测，命中则强制 follow-up 段为共情话术 + observations 段降级为短句或抑制
- 需绕开 SOUL 句式禁忌
- 加 1-2 个情绪用例回归

**涉及文件**：
- [provider.py](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py)（编排层）
- [observations.py](file:///workspace/src/hermes_cgm_agent/services/reports/sections/observations.py)（报告段条件生成）
- 新增测试

#### P1-6 consolidation 定时触发闭环 — 详细实现方案

**当前代码**（[scheduler.py:7-9](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L7-L9)）：
明确写"There is NO resident scheduler process"。`push_tick` 只调 `synthesize_state`（warm digest），不调 `consolidate()`（staged L1→L2→L3）。staged consolidation 仅由 `provider.on_session_end` + 手动 CLI 触发。

**改进方案**：
- 在 `push_tick` 或新增 `memory_tick` 里加 `self.consolidation.consolidate(user_id, now=now)`
- 复用现有 transaction/audit
- 加 per-(user,day) 幂等键

**涉及文件**：
- [scheduler.py](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py)（`_emit` 加 consolidate 调用）

#### P1-7 入库 range 校验 + 路由器过滤 SUSPECT — 详细实现方案

**当前代码**：
- [normalizer.py:117-134](file:///workspace/src/hermes_cgm_agent/services/data/normalizer.py#L117-L134) 仅打 `SUSPECT` 仍入库
- [router.py:319-361](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L319-L361) 安全路由器完全不读 `quality_flag`
- [builder.py:153-157](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py#L153-L157) 报告管线把含 SUSPECT 的 points 直喂路由
- **反向不对称**：`realtime.py:89` 和 `memory/provider.py:336` 都过滤 `quality_flag=="valid"`，唯独安全路由器没过滤

**改进方案**：
- normalizer 加硬拒绝/裁剪阈值（如 <20 或 >600 mg/dL 拒绝入库）
- router `_evaluate_zone` 前置过滤 SUSPECT（或单独处理 SUSPECT 点不参与红区判定）
- 测试更新

**涉及文件**：
- [normalizer.py](file:///workspace/src/hermes_cgm_agent/services/data/normalizer.py)
- [router.py](file:///workspace/src/hermes_cgm_agent/services/safety/router.py)
- [builder.py](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py)

---

### 🟡 P2 — 可做（加分项，不阻塞 demo）

| # | 改进项 | 共识度 | 改动规模 | 核心理由 |
|---|---|---|---|---|
| **8** | KB 高频卡批量签核（至少低血糖自救类） | 2/4 | 中 | 引用守卫对未签核内容执法，"权威轨"名不副实 |
| **9** | 记忆效能评测门（eval_recall + replay） | 1/4 | 中 | 记忆层无回归门，改了不敢合并 |
| **10** | 安全功能用户可见化（recovery_check 叙事化） | 1/4 | 中 | 让安全兜底对用户可感知 |
| **11** | 弱势人群第3天独立升级点 | 1/4 | 小 | SOUL 承诺 day1/3/5，代码只兑现 day1/5 |
| **12** | 数据导出/删除命令 | 1/4 | 小-中 | SOUL 说"帮你整理数据带去医生"却无导出命令 |
| **13** | PDF 报告导出 | 0.5/4 | 小-中 | 医生版 Markdown 不专业 |
| **14** | 一键 demo 脚本（零配置起 demo） | 2.5/4 | 中 | demo 可信度 + 家属 operator 可触达 |

---

### ⚪ P3 — 延后（生产才需要 / 另一项目量级）

| # | 改进项 | 理由 |
|---|---|---|
| 15 | 红区推送幂等重试（弃 at-most-once） | 生产级安全告警才需要 |
| 16 | DB 完整性校验 / 启动自检 | 生产级数据完整性 |
| 17 | Fernet 密钥轮换 | 生产级 PHI 管理 |
| 18 | webhook 证书 pinning | 生产级传输安全 |
| 19 | 微信/Telegram bot 前端 | 另一项目量级，违反 AGENTS.md 边界 |
| 20 | 移动端 / GUI / 语音 / 大字 / 深色 | 另一项目量级 |
| 21 | 老人/残障可达性适配 | 依赖前端形态存在 |

---

### 明确排除项（不做）

| 改进项 | 排除理由 |
|---|---|
| 微信 bot / 移动端 / GUI | 违反 AGENTS.md 项目边界，是另一条产品线 |
| DB 完整性 / 密钥轮换 / 证书 pinning | 生产级基础设施，demo 不需要 |
| consolidation 接独立 cron | consolidation 已在 push_tick 链路被 synthesize_state 间接调用，独立 cron 是 PM 已确认的想象痛点 |
| 静默即认可改方向（candidate→archived） | 医疗工程+AI工程反对，当前 candidate→observing 是合理设计，只加 category 过滤即可 |

---

## 8. 代码 Reference 索引

### 安全相关
- [router.py](file:///workspace/src/hermes_cgm_agent/services/safety/router.py) — 三区路由器、红区模板、恢复二次确认
- [citation_guard.py](file:///workspace/src/hermes_cgm_agent/services/safety/citation_guard.py) — 引用守卫
- [memory_guard.py](file:///workspace/src/hermes_cgm_agent/services/safety/memory_guard.py) — 双轨隔离、KB 只读守卫
- [sqlite.py](file:///workspace/src/hermes_cgm_agent/storage/sqlite.py) — Fernet 加密、权限、schema
- [delivery.py](file:///workspace/src/hermes_cgm_agent/services/tools/handlers/delivery.py) — webhook/SMTP 投递、PHI allowlist

### 记忆/RAG 相关
- [provider.py](file:///workspace/src/hermes_cgm_agent/services/memory/provider.py) — system_prompt_block、情感词识别、consolidation 触发
- [assembler.py](file:///workspace/src/hermes_cgm_agent/services/memory/assembler.py) — 记忆组装
- [consolidation.py](file:///workspace/src/hermes_cgm_agent/services/memory/consolidation.py) — 梦境合成
- [retrieval.py](file:///workspace/src/hermes_cgm_agent/services/memory/retrieval.py) — 检索
- [authoritative.py](file:///workspace/src/hermes_cgm_agent/services/rag/authoritative.py) — KB 信任分级

### 叙事/报告相关
- [SOUL.md](file:///workspace/SOUL.md) — 人格定义
- [narrative_templates.py](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py) — 句式禁忌、TIR 翻译、四状态话术
- [builder.py](file:///workspace/src/hermes_cgm_agent/services/reports/builder.py) — 报告生成、红区截断、引用守卫门控
- [observations.py](file:///workspace/src/hermes_cgm_agent/services/reports/sections/observations.py) — 三受众分流、升级关怀话术
- [daily_card.py](file:///workspace/src/hermes_cgm_agent/services/reports/sections/daily_card.py) — 日报卡片三受众
- [patterns.py](file:///workspace/src/hermes_cgm_agent/services/reports/sections/patterns.py) — 协商式假设叙事

### 调度/推送相关
- [scheduler.py](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py) — 静默策略、静默即认可、推送触发
- [memory.py](file:///workspace/src/hermes_cgm_agent/domain/memory.py) — EscalationState、HypothesisState

### 数据相关
- [normalizer.py](file:///workspace/src/hermes_cgm_agent/services/data/normalizer.py) — 入库归一化、SUSPECT 标记
- [data/repository.py](file:///workspace/src/hermes_cgm_agent/services/data/repository.py) — PHI 字段 seal/unseal
- [events.py](file:///workspace/src/hermes_cgm_agent/services/analytics/events.py) — 事件检测、OVERNIGHT_LOW、cadence 硬编码

### 文档
- [README.md](file:///workspace/README.md) — 安装步骤、阈值文档漂移
- [KNOWN_ISSUES.md](file:///workspace/KNOWN_ISSUES.md) — 已知限制（部分过时）
- [.env.example](file:///workspace/.env.example) — 126 行配置项
- [PRD-SUPPLEMENT.md](file:///workspace/PRD-SUPPLEMENT.md) — 06-06 审查文档（部分过时）

### 测试
- [test_e2e_memory_recall.py](file:///workspace/tests/test_e2e_memory_recall.py) — 记忆 E2E 回忆
- [test_g0_g7_e2e.py](file:///workspace/tests/test_g0_g7_e2e.py) — G0-G7 E2E
- [test_hermes_e2e.py](file:///workspace/tests/test_hermes_e2e.py) — Hermes 集成 E2E

---

## 9. 信源清单

### 联网调研信源（CGM 用户痛点）

- [Diabetes Technology: Lifesaving and Stressful — Medscape/ADA 2025](https://www.medscape.com/viewarticle/diabetes-technology-lifesaving-and-stressful-2025a1000ljj)
- [Patient experiences of CGM — PMC systematic review](https://pmc.ncbi.nlm.nih.gov/articles/PMC10755613/)
- [Perceptions of CGM Systems (T1D Exchange) — Clin Diabetes 2024](https://diabetesjournals.org/clinical/article/doi/10.2337/cd23-0005/153502/)
- [Experience with burdens of diabetes device use — Stanford/Joslin](https://pdfs.semanticscholar.org/f178/757a1f1388ce0475c130ac8b0346127bf8be.pdf)
- [I've Had an Alarm Set for 3:00 a.m. for Decades — ADA Clin Diabetes](https://pmc.ncbi.nlm.nih.gov/articles/PMC8061550/pdf/diaclincd200026.pdf)
- [Nocturnal Hypoglycemia in the Era of CGM — JDST 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11418455/)
- [Managing Low Blood Sugar Overnight — Dexcom](https://www.dexcom.com/en-CA/blog/managing-low-blood-sugar-overnight)
- [Continuous glucose monitoring made me continuously crazy — TechNewsVision](https://technewsvision.co.uk/continuous-glucose-monitoring-made-me-continuously-crazy/)
- [动态血糖仪值不值得普通人用 — 什么值得买](https://post.smzdm.com/p/a6z37ken/)
- [三诺动态血糖仪被投诉"虚低暴冲" — 什么值得买](https://post.m.smzdm.com/p/am973m5v/)

### 联网调研信源（CGM 产品易用性）

- [Fu 2021, Telemedicine and e-Health, PMC8349717](https://pmc.ncbi.nlm.nih.gov/articles/PMC8349717/) — 92 名首次使用者实测
- [Diagnostics 2024, PMC10888350](https://pmc.ncbi.nlm.nih.gov/articles/PMC10888350/) — AGP 报告难读
- [JMIR Diabetes 2025, Turner & Stawarz, PMC12133075](https://pmc.ncbi.nlm.nih.gov/articles/PMC12133075/) — 602 条评论系统分析
- [Dexcom G7 NZ 评论](https://apps.apple.com/nz/app/dexcom-g7/id1569432518)
- [Dexcom G7 UK 评论](https://apps.apple.com/gb/app/dexcom-g7/id1569432518)
- [LibreLinkUp 评论](https://grand-screen.com/apps/librelinkup/reviews/)
- [smartdiabetesliving 走查](https://smartdiabetesliving.com/dexcom-g7-iphone-setup/)
- [BMC Geriatr 2025, PMC12707011](https://pmc.ncbi.nlm.nih.gov/articles/PMC12707011/) — 75+ 老人 CGM 可用性
- [JDR 2025](https://onlinelibrary.wiley.com/doi/10.1155/jdr/9944722) — 听障糖尿病患者
- [Dexcom ONE+ 评论](https://apps.apple.com/nz/app/dexcom-one/id6450965754)
- [PMC7710160](https://pmc.ncbi.nlm.nih.gov/articles/PMC7710160/) — 专家启发式评估

---

## 10. 复核 Agent 指引

> 如果你（复核 agent）正在阅读本文档，以下是你的任务上下文：

### 你的任务
复核本报告的改动计划，确认优先级排序是否合理，并执行 P0（必须做）的三项改动。

### 复核要点
1. **P0-1 红区模板分流**：核实 [router.py:49-52](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L49-L52) 当前模板，确认 `_red` 方法 [router.py:458](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L458) 已计算 `direction`，按方案分流低/高红区。注意绕开 SOUL 句式禁忌（[narrative_templates.py:18-30](file:///workspace/src/hermes_cgm_agent/services/reports/narrative_templates.py#L18-L30)）。
2. **P0-2 夜间低次日推送**：核实 [scheduler.py:387-440](file:///workspace/src/hermes_cgm_agent/services/scheduling/scheduler.py#L387-L440) 的 `_should_trigger_daily_trend`，确认 [events.py:132-134](file:///workspace/src/hermes_cgm_agent/services/analytics/events.py#L132-L134) 的 OVERNIGHT_LOW 检测，加短路触发。
3. **P0-3 README 阈值修正**：核实 [README.md:75-78](file:///workspace/README.md#L75-L78) vs [router.py:42-46](file:///workspace/src/hermes_cgm_agent/services/safety/router.py#L42-L46)，改 README。

### 关键约束
- 急救话术是固定文本（非 LLM 生成），不依赖 KB 签核
- 15-15 法则是 ADA 公开标准，不构成"代替医生"
- 保留 defer 尾句——"先救命再 defer"
- 夜间低推送是"次日早晨"不是"实时叫醒"，复用 daily 管道
- 所有改动需有对应测试
- 遵循 [AGENTS.md](file:///workspace/AGENTS.md) 项目边界：capability layer 不做前端

### 不要做的事
- 不要改静默即认可的方向（仍 candidate→observing），只加 category 过滤（这是 P1）
- 不要做情感编排（这是 P1）
- 不要做微信 bot / 移动端 / GUI（违反项目边界）
- 不要碰 KB 签核（这是 P2）
- 不要改 develop 的 squash 历史

---

*报告生成时点：2026-07-15*
*审计基准：develop @ de67892*
