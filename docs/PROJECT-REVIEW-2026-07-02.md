# CGM-Agent 全项目评审报告（2026-07-02）

- **评审范围**：全仓代码 + specs 001–004 + docs 决策链 + 测试基线复跑 + 外部竞品网络调研
- **评审分支**：`claude/glucose-system-review-qrmw6e`（与 `main` HEAD `4119a32` 同源）
- **本地复跑基线**：457 个单元测试，**455 通过 / 1 环境性错误 / 1 日期脆弱失败**（详见 §1.3）

---

## 1. 项目分析与进度确认

### 1.1 项目是什么（现状事实）

一个挂载在 **Hermes Agent** 上的 CGM（连续血糖监测）**能力层插件**，约 **15,600 行 Python 源码**，通过 17 个工具（`timeseries.*`、`memory.*`、`rag.*`、`reports.generate`、`scheduling.push_tick`、`delivery.send`、`kb.approve` 等）+ 一个 memory provider 注册进 Hermes。核心组件：

| 层 | 实现 | 状态 |
|---|---|---|
| 数据入口 | CSV 导入 + seed-demo；Dexcom API 已冻结 | ⚠️ 仅 CSV |
| 确定性分析 | TIR/TBR/GMI/CV + 低血糖事件检测（非 LLM） | ✅ |
| 三层记忆 | Hot（SQL 直取 L2/L3）/ Warm（consolidation 梦境合成）/ Cold（L1 BM25+可选 dense） | ✅ |
| L0 工作上下文 | 14 天渐进衰退压缩（近端点级→远端日聚合） | ✅ |
| 双轨 RAG | 权威 KB（ClaimCard+BM25+tier 护栏）⊥ 个人记忆，`assert_track_isolation` 代码级隔离 | ✅ |
| 安全 | 三区路由硬编码 + 红区恢复 2h 二次确认 + citation 硬门（strict=True 报告闸）+ Fernet PHI 加密 0600 | ✅ |
| 主动推送 | push_tick 工具化（模型零策略面）+ webhook（https-only/PHI allowlist/禁重定向） | ✅（email 为 stub） |
| 知识管线 | PDF→Hermes 抽取→质量门→review queue→kb-merge，eval-rag hit@3 CI 门禁 | ✅（0 张临床签核卡） |

### 1.2 里程碑进度

| Feature | Spec | 任务 | 状态 |
|---|---|---|---|
| F1 Hermes 运行可用性 | 001 | 28/28 | ✅ DONE（D045） |
| F2 数据来源 ADR | — | — | ❌ **OPEN，唯一未启动的战略项** |
| F3 医学安全硬化 | 002 | 29/29 | ✅ DONE（D047） |
| F4 陪伴叙事+协商交互 | 003 | 35/35 | ✅ DONE（D046 + remediation R001–R060） |
| F5 推送投递闭环 | 004 | 26/26 | ✅ DONE（D048/D049；email 除外） |
| F6 工程债 | checklist | — | 🔶 PARTIAL（executor 已拆；cli.py/builder.py 未拆；G3 文档计数漂移未收敛） |
| F7 分析深度（MAGE/MODD/CONGA、AGP） | — | — | ⏸ DEFERRED |

**结论：Stage 0–2 全部收口，项目处于「能力层功能完备、数据入口缺失」的状态。**

### 1.3 本次复跑发现的两个问题

1. **日期脆弱测试**（真实缺陷，测试层）：`test_memory_integration.test_provider_prefetch_includes_warm_and_l0_summaries` 种子数据固定在 2026-05-31，而 `L0ContextBuilder.build()` 以 `datetime.now()` 为锚取 14 天窗口——今天（7/2）种子数据已滑出窗口，断言 `[CGM L0 context]` 失败。**6 月全绿、7 月自然变红**。修法：测试注入 `anchor_at` 或种子数据相对 now 生成。同类模式建议全仓排查。
2. `test_hermes_plugin_integration` setUpClass 依赖 Hermes venv 的 `agent` 模块——纯环境问题，但 skip 条件不完整（E2E 文件会 skip，这个文件不会），建议补 skip guard。

---

## 2. Pros and Cons（严谨梳理）

### 最优势的地方（按坚固程度排序）

1. **安全工程是代码级硬门，不是 prompt 约定** —— 三区路由硬编码拦截、citation guard 在报告交付闸强制 `strict=True`（未支撑数字直接阻断交付）、`assert_kb_readonly` denylist+allowlist 双保险、webhook PHI deny-by-default allowlist、模型零策略面（push_tick 只能触发不能控制）。每一条都有守卫测试。**这是市面上 CGM AI 产品公开资料里没有任何一家做到的**。
2. **记忆架构的深度**：Hot/Warm/Cold 三层 + L0 确定性压缩 + bi-temporal L2/L3（valid_from/valid_to + lineage 回指 L1）+ 假设四状态生命周期（candidate→observing→stable→invalid）+ 候选队列确认闸门。这正是你定位的核心价值点，且已实现而非 PPT。
3. **双轨 RAG 物理隔离**：个人记忆永不写入医学库、医学卡 tier 护栏防机器草稿稀释人工卡、population 受控词表过滤不 fail-open。反向生命周期的洞察（医学=静态零容错 vs 个人=无界演变可遗忘）是真正的架构判断力。
4. **工程过程质量**：DECISION_LOG 的诚实声明（reconstructed 标记）、spec-kit 全流程、TDD、457 测试、审计-修复闭环（F4 的 F-1…F-9→R001–R060）。这个过程纪律在个人项目里罕见。

### 最劣势的地方（按致命程度排序）

1. **没有真实数据入口（F2 未决）** —— Dexcom 冻结后只剩 CSV 手动导入。**整个系统的价值前提是"持续"血糖数据，而目前数据不能持续流入**。这是唯一能让所有已建能力归零的短板。
2. **权威 KB 零张临床签核卡** —— `kb.approve` 工具就绪但无临床审核者，生产 KB 只有 6 张 curated 种子卡 + auto 卡全部 `verified=false`。citation 硬门的 backing 集因此不限 verified（KNOWN GAP），医学可信度的最后一环没闭合。
3. **无面向用户的呈现层** —— 交互 100% 依赖 Hermes chat；无 AGP 图、无趋势可视化、无移动端。竞品全部以图形化 app 为主体。
4. **单用户、进程内状态假设** —— 红区恢复状态 `_last_red_zone` 不持久化，多用户/重启场景失效；对 MVP 可接受，但要写明边界。
5. **记忆有效性未被度量** —— 有 rag hit@3 评测，但**没有任何指标证明"长期记忆让回答变好了"**（对比 with/without memory 的召回或用户任务成功率）。核心价值点缺少核心证据。

---

## 3. 开发完整度说明

**已完整开发（有实现+测试+决策记录）**：
- 数据模型与确定性分析全链（导入→归一化→指标→事件检测）
- 三层记忆全链（L0 builder、consolidation、L1/L2/L3、bi-temporal、候选评审、USER.md 单向同步）
- 双轨 RAG（权威 BM25+tier+population；个人 hybrid 可选 dense）
- KB 生产管线（ingest→质量门→review queue→merge→validate→eval CI 门禁）
- 安全栈（三区+红区恢复、citation 报告硬门、双轨隔离守卫、PHI 加密、审计无泄漏）
- F4 叙事（假设话术接线、报告 audience 分版、companion 文案守卫、升级关心 SOUL 对齐）
- F5 推送（分层调度、静默即认可、幂等、push_tick 工具、webhook 投递）
- Hermes 集成（插件注册、memory provider、cron 触发路径、Level-3 E2E 5 项）

**未完整开发**：
- **F2 数据来源**（唯一 OPEN 的战略决策：Libre/Nightscout/手动？）
- email 投递通道（stub，记 `queued`）
- 临床签核流程的"人"（0 卡核验）
- 脆弱人群早期干预触发（`vulnerable_population` 上游无写入路径，生产休眠）
- F7 高级指标（MAGE/MODD/CONGA、AGP 百分位）
- 工程债：cli.py（1252 行）/builder.py（980 行）拆分、G3 文档计数漂移、日期脆弱测试
- 语义 dense 检索默认关闭（大数据量下的记忆召回质量未验证）

---

## 4. MVP 视角评估

**作为技术 demo：优秀甚至过剩。** 安全工程、记忆架构、过程纪律都超出 MVP 标准一到两个量级。

**作为产品 MVP：不闭环。** MVP 的"V（viable）"要求一个真实用户能走完「戴传感器→数据自动进来→日常获得有记忆的陪伴叙事→按周主动推送」的循环。目前卡在第一、二步：数据靠手动 CSV，触达靠 Hermes 命令行。

**直白结论**：你把 MVP 的"技术风险"消灭得非常彻底，但"产品假设"（用户要不要一个有长期记忆的血糖陪伴者）还一次都没被真实数据验证过。当前形态更准确的定性是——**一个高质量的 agentic-memory 参考实现 + 准 MVP**，距离 MVP 只差数据入口和一个最薄的呈现面。这不是坏事：安全和记忆是最难补的，你先建了护城河；但下一步必须停止纵深、转向闭环。

---

## 5. 竞品对比分析（基于网络调研）

### 5(a) 相对已发布产品的优势与劣势

**商业竞品**：Dexcom Stelo（2024.12 起集成 Google Cloud 生成式 AI 周报叙事，首个 GenAI CGM 平台）、Abbott Lingo（app 教练）、January AI（数字孪生，可脱离传感器预测血糖）、Levels（最强分析面板）、Nutrisense（真人营养师，$179+/月）、Signos（FDA 清准的减重 CGM）；以及 **UpDoc**——2025.12 FDA 510(k) 清准的**首个 LLM 糖尿病 SaMD**（胰岛素方案指导）。开源侧：Nightscout/xDrip+（数据管道成熟，无 LLM 陪伴层）；通用记忆层 Mem0/Letta（无医学安全）。学术侧：LLM-CGM benchmark、隐私保护 CGM 问答 agent（arXiv 2604.17133）。

**你的优势**：
1. **本地优先隐私**：全部竞品都是云端 SaaS；你是本地 SQLite+Fernet+PHI allowlist，这是结构性差异而非功能差异。
2. **可验证的医学引用**：Stelo 的 GenAI 周报是"生成式建议"，没有 verbatim 引用校验；你的 citation 硬门 + ClaimCard 溯源（页码/来源/kb_version）在公开产品里没有对标物。
3. **记忆深度**：竞品的"个性化"是统计画像；你有假设生命周期 + 协商验证 + bi-temporal 演变，是真正的纵向个体建模。
4. **非指令性人格**：竞品全是 tips/recommendations（指令式）；你的 Informed Companion + 无道德评判在慢病心理负担这个真实痛点上是差异化定位。

**你的劣势**：
1. 无传感器直连（竞品全部开箱即用）；2. 无可视化（AGP 是行业标配语言）；3. 无监管路径（UpDoc 已拿到 LLM SaMD 先例，边界在收紧）；4. 无真人服务与用户基础；5. KB 体量（6 张 curated vs 竞品背后完整临床团队）；6. 绑定小众 Hermes 运行时，分发受限。

### 5(b) 盲区与未达最优解

1. **餐食/运动/睡眠上下文融合** —— 所有竞品的核心洞察都建立在"血糖×行为"关联上（拍照记餐、运动同步）；你的 UserEvent 有骨架但无低摩擦录入路径。**这是最大的产品盲区**：没有行为上下文，L3 假设（"你吃 X 会怎样"）的证据密度上不去。
2. **血糖预测/前瞻**：January AI 数字孪生、SSM-CGM 等已把"预测"作为主卖点；你完全是回顾式。回顾式定位本身成立（陪伴者不预测），但应作为**显式决策**写进 ADR，而非空白。
3. **监管边界**：FDA 2025.11 DHAC 已在划 GenAI 健康软件的 device/non-device 边界。你的非指令性话术恰好是 wellness 侧的自然防御，但没有一份文档主动论证"本产品为何不构成 SaMD"。这份论证迟早要写。
4. **记忆有效性评测**（见 §2 劣势 5）：学术界已有 LLM-CGM benchmark，可直接借来跑。
5. **多语言 KB 覆盖与中文指南**（CDS 2024 已入队列但未核验）。
6. **无障碍触达**：webhook 有了，但普通用户没有 Bark/Telegram/微信这类"最后一公里"的现成配方。

### 5(c) 核心竞争力是否 solid

**"安全工程 + 双轨记忆"这个组合是真实差异化，且在代码层面非常坚固**（守卫测试、CI 门禁、决策可追溯——大厂想抄架构容易，想抄这个过程纪律的产物不容易）。但要清醒两点：

- 它的坚固是**工程坚固**，不是**证据坚固**——没有真实用户数据流过，"长期记忆提升陪伴质量"仍是假设。
- 它不构成**数据/网络护城河**——Dexcom 有传感器和十亿级数据点，它做记忆层只是时间问题。你的可持续位置是"**本地优先、可审计、非指令性的开源陪伴层**"——这个生态位大厂结构性不会去占（他们必须云端+合规变现）。

---

## 6. 项目完善度确认

**(a) 未完善。** 能力层（记忆/RAG/安全/推送）已完善到超出 MVP 需要；**产品环未闭合**。

**(b) 缺失清单（按阻断性排序）**：
1. F2 数据入口 ADR + 实现（阻断一切真实验证）
2. 最薄呈现层（哪怕只是 Markdown 周报导出 + webhook 到手机通知）
3. KB 临床核验第一批（哪怕 20 张核心卡）
4. 记忆有效性评测基线
5. email 通道（或明确砍掉，写入决策）
6. 脆弱人群触发路径（或显式标 KNOWN GAP 保持休眠）
7. 工程债：日期脆弱测试、cli.py/builder.py 拆分、G3 文档漂移

---

## 7. 综合分析与下月开发计划

### 7(a) 总评

进度：**Stage 0–2 全部按宪法纪律收口，能力层完成度 ~85%，产品闭环完成度 ~40%**。工作深度显著高于广度——这在医疗域是正确的顺序。下一步的唯一主题应该是：**闭环**（数据进得来 → 洞察出得去 → 有证据说明记忆有用），停止一切纵深投入。

### 7(b) 一个月开发计划（2026-07-06 ~ 2026-08-02，主题：闭环月）

> 节奏假设：每天 1 个可交付单元；周六机动/补债，周日休息或复盘。每周五全量测试 + 提交周报。

**Week 1（7/6–7/12）：数据入口落地（F2）——本月脊柱**
- **周一 7/6**：写 F2 ADR 草案：枚举 Nightscout REST / LibreLinkUp 导出 / xDrip+ 数据库 / 手动 CSV 增强四个选项，按「无需官方 API、可持续自动流入、你自己能戴」三准则打分。**推荐押 Nightscout**（开源、REST API 成熟、兼容 Libre/Dexcom 硬件）。
- **周二 7/7**：ADR 定稿入 `docs/adr/ADR-0002-data-source.md`；`/speckit-specify` 出 005-data-ingestion spec（拉取器 + 去重 + 增量同步语义）。
- **周三 7/8**：TDD 实现 Nightscout client（entries API → GlucosePoint 映射、单位换算、quality_flag）。
- **周四 7/9**：实现 `data.nightscout_sync` 工具 + CLI `nightscout-sync`（复用 dexcom_sync 的工具骨架）+ 幂等/增量测试。
- **周五 7/10**：接 Hermes cron（每 15 分钟 sync），端到端验证「数据自动进库→L0 窗口滚动」；全量测试；提交。
- **周六 7/11**（机动）：修日期脆弱测试（注入 anchor_at / 相对时间种子）+ 补 plugin_integration skip guard。

**Week 2（7/13–7/19）：真实闭环跑通 + 触达最后一公里**
- **周一 7/13**：给自己（或模拟 14 天数据回放 harness）开始持续数据流；写回放脚本 `replay --speed 96x` 供无传感器时演示。
- **周二 7/14**：webhook 最后一公里配方：写 Bark/ntfy/Telegram bot 三选一的接收端文档 + 实测 daily push 到手机。
- **周三 7/15**：email 通道裁决：**建议砍掉**，DECISION_LOG 记一条 D05x（webhook 已覆盖触达，email 维护成本不值）；删 stub 或标 frozen。
- **周四 7/16**：连跑观察日 1：检查 consolidation 梦境合成、push tier 触发、幂等在真实时间流下的表现；修暴露的问题。
- **周五 7/17**：连跑观察日 2 + 全量测试 + 周报。
- **周六 7/18**（机动）：脆弱人群路径裁决：无上游写入 → 显式标 KNOWN GAP 休眠，文档化。

**Week 3（7/20–7/26）：证据与可信（KB 核验 + 记忆有效性评测）**
- **周一 7/20**：挑 20 张最高频核心卡（TIR/TBR/CV 阈值类），逐张对照原文 PDF 核验，走 `kb.approve` 签核（reviewer=你，provenance 如实记"self-reviewed, non-clinician"——诚实优于假装）。
- **周二 7/21**：citation 硬门 backing 集收紧实验：verified-only 模式跑 eval-rag，看 hit@3 是否守住 0.95。
- **周三 7/22**：搭记忆有效性评测：借 LLM-CGM benchmark 的任务形态，构造 20 个「需要历史记忆才能答对」的问答对，跑 with/without memory 对照。
- **周四 7/23**：跑评测、出报告 `eval/memory/MEMORY-EFFICACY-2026-07.md`——这是你核心价值点的第一份证据。
- **周五 7/24**：根据评测结果调优（如 dense 检索是否该默认开、top_k、prefetch 注入格式）；全量测试 + 周报。
- **周六 7/25**（机动）：中文 KB：CDS 2024 队列卡片核验第一批。

**Week 4（7/27–8/2）：MVP demo 打包 + 收尾**
- **周一 7/27**：最薄呈现层：周报 Markdown 导出美化（含 ASCII/Mermaid 趋势示意，AGP 推迟到 F7）。
- **周二 7/28**：Demo 剧本：写 15 分钟演示脚本（导入→对话回忆→假设协商→红区拦截→周推送到手机），全程录屏。
- **周三 7/29**：README 重写：现状对齐（440+→465、17 工具）、加架构图英文版、加 Quick Start ≤5 命令；G3 文档计数漂移一次性收敛。
- **周四 7/30**：监管边界一页纸：`docs/REGULATORY-POSITION.md`——论证 wellness/non-device 定位（非指令性、不给药物建议、红区只导向就医）。
- **周五 7/31**：月度复盘：更新 BACKLOG（F2 关闭、F7/多用户/AGP 重新排期）、全量测试、tag `v0.2-mvp-loop`。
- **周六 8/1–周日 8/2**：缓冲（吸收全月滑点；若有余力启动 builder.py 拆分）。

**本月不做**（防止范围蔓延）：AGP 可视化、MAGE/MODD/CONGA、多用户、移动端、血糖预测、Dexcom 解冻。

---

## 附：调研来源

- [Dexcom Launches the First Generative AI Platform in Glucose Biosensing](https://investors.dexcom.com/news/news-details/2024/Dexcom-Launches-the-First-Generative-AI-Platform-in-Glucose-Biosensing/default.aspx) · [CNBC 报道](https://www.cnbc.com/2024/12/17/dexcom-launches-generative-ai-platform-for-stelo-users.html) · [MedTech Dive](https://www.medtechdive.com/news/dexcom-gen-ai-feature-stelo-cgm/735918/)
- [Levels vs January AI vs Nutrisense](https://finvsfin.com/levels-vs-january-ai-vs-nutrisense/) · [Signos/Levels/Nutrisense/Veri/SNAQ 对比](https://www.snaq.ai/blog/comparing-the-cost-of-signos-levels-nutrisense-veri-and-snaq) · [Stelo vs Lingo](https://optimizebiomarkers.com/cgm-compare/dexcom-stelo-vs-abbott-lingo)
- [UpDoc：首个 FDA 清准的 LLM 糖尿病 SaMD](https://innolitics.com/articles/updoc-fda-cleared-ai-agent/) · [FDA AI 健康工具监管综述](https://bipartisanpolicy.org/issue-brief/fda-oversight-understanding-the-regulation-of-health-ai-tools/)
- [LLM-CGM benchmark (PubMed)](https://pubmed.ncbi.nlm.nih.gov/39670363/) · [隐私保护 CGM 问答 Agent (arXiv)](https://arxiv.org/pdf/2604.17133) · [SSM-CGM 预测模型 (arXiv)](https://arxiv.org/pdf/2510.04386)
- [xDrip+ 开源生态](https://blog.csdn.net/gitblog_00096/article/details/137036408)
