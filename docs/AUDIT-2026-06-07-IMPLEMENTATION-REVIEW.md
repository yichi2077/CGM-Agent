# CGM-Agent 项目功能与实现审计报告

> **日期**：2026-06-07  
> **范围**：项目功能与实现审计（非前端 UI 审计）  
> **基线**：HEAD `f1ce23d`（`merge: integrate tender-sammet audit closure`）  
> **方法**：对照 PM 蓝图 DOCX、策略文档（STATUS / DECISION_LOG / MEM-ARCH / ADR-0001）、持续审计闭环 R1–R6 计划，系统性审阅 `src/`、`tests/`、`integrations/hermes/`、`skills/`、`docs/`  
> **约束**：只读审阅；本文件为唯一新增产出

---

## 1. 执行摘要

### 1.1 健康度与下一阶段就绪度

| 维度 | 评分 (1–10) | 说明 |
|------|-------------|------|
| **能力层工程成熟度** | **8.5** | G0–G8 结构完整；15 个 active 工具；记忆 L0–L3 + 双轨 RAG + 推送调度已实现；337 项单测全绿 |
| **声明↔实现↔Hermes 面对齐** | **8.0** | R1–R6 已修 population fail-open、verify_quotes、plugin 漂移守卫、phantom D 引用等；仍有 skill 契约 vs 运行期强制之隙 |
| **产品蓝图一期 MVP 覆盖** | **6.5** | 日报/周报、事件写回、医生报告、安全路由核心能力在代码层具备；缺 App 卡片/微信触达/AGP 可视化/PDF 导出等交付面 |
| **生产就绪（真实数据 E2E）** | **5.5** | `seed-demo` + E2E 测试可证闭环，但默认 runtime DB 为空（`glucose_point_count=0`）；KB 578 卡全 `verified=false` |
| **综合下一阶段就绪度** | **7.0 / 10** | 适合进入 **P3 生产 Hermes 安装固化 + 可发布** 与 **P4/P5 增强**；距「可灰度发布产品」仍差真实数据常驻、外部投递、KB 临床签核 |

**一句话结论**：仓库已从「能力层 spike」推进到「分层推送产品闭环已实现」的工程态；核心架构与 PM 蓝图、ADR 决策高度同构。主要差距在 **交付面（UI/渠道）**、**分析深度（MAGE/AGP）**、**KB 可信度（临床签核）** 与 **默认 runtime 空库**，而非模块缺失。

### 1.2 取证命令（本次实测）

```bash
# 全量单测 — 337 tests OK（2026-06-07）
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests

# 开发状态 — 15 tools, glucose_point_count=0, push_scheduler_present=true
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m hermes_cgm_agent dev-status

# KB 校验 — valid
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m hermes_cgm_agent kb-validate

# RAG 评测 — hit@3 = 1.0 (44/44), kb-2026-06-auto-v2
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m hermes_cgm_agent eval-rag

# PM 蓝图 DOCX 提取（pandoc 不可用，改用 Python zip/xml）
python3 -c "..."  # → .runtime/blueprint-extract.txt, 874 段

# GitHub CI — gh 未认证，无法拉取 run 列表（本地 workflow 文件已审阅）
gh run list  # → 需 gh auth login
```

---

## 2. PM 蓝图对齐矩阵（愿景 vs 代码）

**蓝图来源**：`微泰CGM_AI_Agent产品蓝图与一期落地报告.docx`（2026-05，874 段提取）

| 蓝图能力 / 一期模块 | 蓝图期望 | 当前实现 | 对齐度 | 证据 |
|---------------------|----------|----------|--------|------|
| **Agent 非聊天框主形态** | 事件/周期/复诊触发，少认知负担 | Hermes 主壳 + `push-tick` 日/周/月 + `reports.generate`；`push-tick` 为 CLI-only | 🟡 部分 | `services/scheduling/scheduler.py`, `cli.py:push-tick` |
| **L0–L3 记忆** | 14 天工作记忆 + 情节/语义/假设 + 用户可纠错 | L0 Builder、`memory.*` 工具、consolidation、USER.md 受管段同步 | ✅ 高 | `l0_builder.py`, `consolidation.py`, `user_md_sync.py` |
| **知情陪伴人格** | 非监督者、短叙事、协商式 | 报告 builder 受众分支、黄区前缀、pattern 信号 | ✅ 高 | `reports/builder.py` audience 分支 |
| **绿/黄/红安全路由** | 用药/诊断/剂量 → 安全模板 | `SafetyRouter` 三区 + 红区 sections 整体替换 | ✅ 高 | `services/safety/router.py`, `reports/builder.py` |
| **双轨 RAG** | 权威医学 vs 个人记忆隔离 | BM25 authoritative + personal L1 hybrid；`assert_track_isolation` | ✅ 高 | `authoritative.py`, `assembler.py` |
| **① AI 日报/周报** | 30–80 字生活语言卡片 | `synthesize_state(daily/weekly/monthly)` + `PushSchedulerService` | 🟡 部分 | 内容生成有；App/微信卡片面无 |
| **② 餐食/运动事件写回** | 拍照/语音/标签 + 低风险归因 | `events.create` / `events.confirm`；无拍照/语音管线 | 🟡 部分 | `registry.py` events 工具 |
| **③ 复诊医生报告** | AGP、TIR/TAR/TBR、异常、可导出 PDF | 医生版 `audience=clinician` + appendix；纯 Markdown 文本，无 AGP 百分位图、无 PDF | 🟡 部分 | `renderer.py` ~65 行纯文本 |
| **④ 安全路由与审计** | 红线拦截 + 留痕 | router + audit + `rag.verify_quotes` + cgm-safety SKILL | ✅ 高 | `citation_guard.py`, `skills/cgm-safety/SKILL.md:149` |
| **分层推送 + 静默即认可** | 日 30 字→周模式→月报告 | `PushSchedulerService` + `push_events` 幂等 + `apply_silent_consent` | ✅ 高 | `scheduler.py`, `test_push_scheduler.py` |
| **微信触达** | 二期试点 | 未实现；`delivery.send` 仅 `local_file` 真发送 | ❌ 缺 | `executor.py:842-863` |
| **多 Profile（用户/医生/家属）** | 同源异构输出 | `ReportAudience` user/clinician/family 分支 | ✅ 高 | `builder.py` |
| **MAGE/MODD/AGP 分析深度** | 临床可读、模式发现 | TIR/TAR/TBR/GMI/CV/LBGI/HBGI 齐全；无 MAGE/MODD/AGP 百分位计算 | ❌ 缺 | `metrics.py` 无 MAGE/AGP |
| **Dexcom 实时接入** | 蓝图长期；一期 mock | 代码完整（OAuth/sync/mapper）；本阶段决策为 mock/CSV only | 🟡 搁置 | `services/dexcom/` |
| **开放式医疗问答** | 一期明确不做 | 经安全路由约束，无独立开放 QA 模块 | ✅ 符合范围 | 设计一致 |
| **记忆用户控制面板** | 可查看/纠正/删除 | `memory.list/delete/correct/confirm` + candidates 层 | ✅ 高 | `MemoryToolService` |

---

## 3. 策略文档对齐（STATUS / DECISION_LOG / ADR）

### 3.1 与 STATUS-REPORT-2026-06-07 的对照

| STATUS 声明 | 实测 | 对齐 |
|-------------|------|------|
| 314 tests OK | **337 tests OK** | 🟡 文档略旧（+23） |
| tool_count: 14 | **dev-status: 15** | 🟡 文档未更新（+`rag.verify_quotes`） |
| memory.list layer=candidates | 插件集成测试断言 candidates enum | ✅ |
| 大模块需边界审查（cli/executor/builder/repository） | cli 1252 / executor 1010 / builder 980 行；已部分提取 ToolService | 🟡 进行中 |
| Hermes RAG smoke 成功 | 本审计未重跑 `hermes chat`；STATUS 有记录 | ✅ 可信 |

### 3.2 与 DECISION_LOG D027–D044 的对照

| 决策 | 实现状态 | 备注 |
|------|----------|------|
| D027 医学/个人策略隔离 | ✅ `assert_track_isolation` + KB 只读 guard | `memory_guard.py`, `authoritative.py __init__` |
| D028/D040/D041 论断卡 + ingest | ✅ 578 卡，`kb-validate` + eval 门禁 | 全 `verified=false` |
| D029 Hot SQL 直取 | ✅ assembler + provider prefetch | |
| D036 不对称检索 | ✅ authoritative BM25-only；L1 hybrid 阈值 | |
| D037 报告候选自动入队 | ✅ `MemoryToolService.ingest_report_candidates` | |
| D038 L0 确定性 Builder | ✅ `l0_builder.py`, `context.get_l0` | |
| D039 L2→USER.md 单向同步 | ✅ 受管段替换 | B1 已修 summary 可读性 |
| D042 tier 护栏 + CI eval | ✅ hit@3=1.0, `kb-quality.yml` | |
| D043 population 受控词表 | ✅ 已修 fail-open | `authoritative.py` |
| D044 verify_quotes 工具 | ✅ 工具+SKILL 契约 | Hermes 侧调用非代码强制 |

### 3.3 与路线图计划（bug-rag-refactored-pnueli.md）阶段状态

| 阶段 | 计划状态 | 审计确认 |
|------|----------|----------|
| P0 审计基线提交 | ✅ 完成 | HEAD `f1ce23d` 已 merge |
| P1 数据+记忆 E2E | ✅ 完成 | `seed-demo`, `test_e2e_memory_recall.py` |
| P2 推送/调度闭环 | ✅ 完成 | `push-tick`, `test_push_scheduler.py` (8 tests) |
| P3 生产安装+可发布 | ⏳ 待做 | `prototype_limit` 仍提及 KB 签核与外部投递 |
| P4 分析深度 MAGE/AGP | ⏳ 增强 | 未开始 |
| P5 KB 可信化 | ⏳ 增强 | 578 卡 0 verified |

---

## 4. 分模块架构审阅

### 4.1 工具层与 Hermes 插件（G0）

- **注册表**：15 个 active 工具（`registry.py`）：timeseries×2, events×2, context, reports, memory×4, hypothesis, rag×2, dexcom, delivery。
- **Hermes 暴露面**：`cgm` 插件动态注册 13 个工具（排除 `memory.confirm/correct`，改由 `cgm_memory` provider）；`plugin.yaml` 与运行期一致（`test_hermes_plugin_integration.py` R2-1 守卫）。
- **Executor 瘦身**：已提取 `MemoryToolService`、`ReportToolService`、`AuthoritativeRAGToolService`、`DexcomSyncToolService`、`EventToolService`；executor 仍 1010 行，分发 if-chain 未大改。
- **参数硬化**：strict bool/int/enum 解析已落地并有回归测试（STATUS-2026-06-07 详述）。

### 4.2 数据管线（G1–G2）

- **导入**：CSV/JSON，`import-cgm` CLI；要求显式单位，无静默 mg/dL 兜底。
- **存储**：SQLite + Fernet 字段级加密；`chmod 0o600`；审计表完整。
- **现状**：默认 `.runtime/app.db` **空库**（0 points / 0 reports）。需 `import-cgm` 或 `seed-demo` 才有真实链路。

### 4.3 分析与事件（G3–G6）

- **指标**：TIR/TAR/TBR（70/180 含端）、GMI(Bergenstal)、Kovatchev LBGI/HBGI — R3 审计确认正确。
- **缺失**：MAGE、MODD、CONGA、按时段 AGP 百分位（10/25/50/75/90）— 蓝图与 P4 计划点名。
- **事件检测**：确定性规则 + `GlucoseEvent`；`events.confirm` 晋升用户事件。

### 4.4 报告（G7）

- **能力**：daily/weekly/monthly/doctor；受众 user/clinician/family；红区整体替换 sections；空窗不再渲染 `None mg/dL`、不产 G8 候选。
- **记忆闭环**：`g8_memory_candidates` → 候选队列 → `memory.confirm`。
- **差距**：`renderer.py` 纯 Markdown 文本；无 PDF、无 AGP 带状附录。

### 4.5 记忆 L0–L3（G8）

| 层 | 实现 | 质量 |
|----|------|------|
| L0 | `L0ContextBuilder` 14 天压缩窗口 | ✅ |
| L1 | episodes + hybrid 检索（规模阈值） | ✅ |
| L2 | profiles + bi-temporal + USER.md 投影 | ✅ B1 已修 |
| L3 | hypotheses 状态机 + silent-consent→observing | ✅ |
| Warm | `ConsolidationService.synthesize_state` 日/周/月 | ✅ |
| 巩固 | 阈值门控 L1→L2→L3 | ✅ |

**设计债（P1 记录）**：
1. `review.py:132` — `occurred_at=now` 导致历史回填多日坍缩为一天，L2/L3 无法跨日形成；`seed-demo` 用检测事件真实 `ts_start` 绕过。
2. detected-event→L1 仅在 `seed-demo` CLI 路径，非报告候选通用生产路径。

### 4.6 双轨 RAG

- **权威轨**：578 卡（6 curated + 572 auto），BM25 + tier 优先 + population 受控类；`eval-rag` 44/44 hit@3=1.0。
- **个人轨**：L2/L3 Hot SQL；L1 Cold hybrid。
- **安全**：`rag.verify_quotes` + `query_number_coverage`；KB 构造期 `assert_kb_readonly`。
- **缺口**：全部 `verified=false`；SKILL 强制 verify_quotes 但 Hermes 生成层是否每次调用无法由本仓保证。

### 4.7 推送调度（P2）

- **架构**：无仓内常驻进程；`PushSchedulerService` 策略+状态；外部 cron/Hermes 调 `push-tick` CLI。
- **幂等**：`push_events` UNIQUE(user,tier,period_key)。
- **静默即认可**：仅 candidate→observing；不自动 accept 记忆候选、不触 stable/archived。
- **缺口**：`push-tick` 未包装为 Hermes tool；投递内容生成后不自动 `delivery.send`（刻意外置）。

### 4.8 安全

- **路由**：绿/黄/红三区；红区不泄漏指标/RAG。
- **引用守卫**：`assert_authoritative_quotes` 整数精确匹配；经 `rag.verify_quotes` 暴露。
- **记忆守卫**：双轨隔离、KB 只读 — 已接线。

### 4.9 Dexcom

- **代码**：`auth.py` OAuth、`tokens.py` 加密、`sync.py` 分页去重、`mapper.py` 读 API unit — R4/R6 审计无 bug。
- **阶段决策**：本阶段仅 mock/CSV；live Dexcom 属后续。
- **低危**：OAuth `state` 未生成/校验（CLI 粘贴流，CSRF 威胁低）。

### 4.10 Hermes 安装与可发布性（P3）

- **installer**：`hermes_plugins/installer.py` 有单测（symlink、marker、pip install、plugins enable）。
- **缺口**：无安装后 smoke test（dev-status + tool-call 走真实 Hermes 面）；`prototype_limit` 仍自报 workflow-dependent。

---

## 5. 详细发现（按严重度）

### P0 — 阻断生产发布 / 正确性风险

*本轮未发现新的 P0 级代码正确性 bug。* R1–R6 已修复的 population fail-open、安全单位、黄区、phantom D 引用等均有守卫测试。  
**残留风险（非新 bug，属产品门槛）**：

| ID | 问题 | 位置 | 影响 | 建议 |
|----|------|------|------|------|
| **P0-R1** | 默认 runtime DB 无数据，产品链路在真实 Hermes 会话中可能「空窗」 | `dev-status` glucose_point_count=0 | 用户首次体验无报告/记忆 | 安装/ onboarding 文档强制 `seed-demo` 或 `import-cgm`；或 installer 可选种子 |
| **P0-R2** | KB 578 卡全部 `verified=false` | `authoritative_kb.json` | 医学零容错产品门槛未达 | P5 临床签核 SOP + 分批 verified=true |

### P1 — 高优先级设计/对齐缺口

| ID | 问题 | 位置 | 影响 | 建议 |
|----|------|------|------|------|
| **P1-1** | `memory.confirm` 接受候选时 `occurred_at=now`，历史回填坍缩 | `review.py:130-133` | 多日模式无法巩固为 L2/L3 | 接受路径支持数据窗口/事件真实时间；或报告候选带 `occurred_at` |
| **P1-2** | `rag.verify_quotes` 依赖 SKILL 契约，非 Hermes 运行期硬强制 | `skills/cgm-safety/SKILL.md:149` | 未加载 skill 时医学数字可能未校验 | P3 smoke 断言 skill 加载；或 Hermes hook 后置校验 |
| **P1-3** | `push-tick` 仅 CLI，非 Hermes tool | `cli.py:155-167` | cron 须调本地 CLI，非 agent 原生 | 评估 `push_tick` tool 包装 + plugin.yaml 同步 |
| **P1-4** | `delivery.send` email/webhook 仅 `queued`，无实现 | `executor.py:842-863` | 外部触达未闭环 | gateway 实现或文档明确 Hermes 侧职责 |
| **P1-5** | detected-event→L1 仅 `seed-demo` 演示路径 | `cli.py _seed_demo` | 生产数据驱动记忆源未统一 | D026 评审：是否提升为 consolidation 输入 |
| **P1-6** | STATUS 报告 tool/test 计数过时 | `docs/STATUS-REPORT-2026-06-07.md` | 文档漂移 | 更新为 15 tools / 337 tests |

### P2 — 中优先级 / 蓝图差距

| ID | 问题 | 位置 | 影响 | 建议 |
|----|------|------|------|------|
| **P2-1** | 无 MAGE/MODD/CONGA 波动指标 | `services/analytics/metrics.py` | 模式发现说服力不足 | P4 按计划扩展 |
| **P2-2** | 无 AGP 百分位渲染（医生版） | `services/reports/renderer.py` | 复诊场景临床可读性差距 | P4 文本带状或图表附录 |
| **P2-3** | 无 PDF 报告导出 | 全仓 | 蓝图③「可导出 PDF」未达 | 渲染后接 PDF 工具链 |
| **P2-4** | 大模块可维护性 | cli 1252 / executor 1010 / builder 980 行 | 后续功能改动成本高 | 继续 ToolService 提取（REFACTOR-PLAN） |
| **P2-5** | 大批量导入性能 | Fernet 逐条 insert（计划记 P3 perf） | 12k+ 点 >60s | 批量插入路径 |
| **P2-6** | `gh` 未认证，无法验证远端 CI 绿 | 环境 | 审计无法确认 GitHub Actions 历史 | 维护者 `gh auth login` 后复核 |

### P3 — 低优先级 / 技术债

| ID | 问题 | 位置 | 建议 |
|----|------|------|------|
| **P3-1** | executor 分发 if-chain 大重构暂缓 | `executor.py` | 有测试覆盖，收益/风险比低时不动 |
| **P3-2** | Dexcom OAuth state 未校验 | `services/dexcom/auth.py` | CLI 流可接受；live 前补 state |
| **P3-3** | 无微信/App 卡片交付面 | 范围外能力层 | 二期由客户端/Hermes 集成 |
| **P3-4** | 旧审计文档计数过时 | `AUDIT-2026-06-06-蓝图实现差异审计.md` 等 | 标注历史快照或归档 |

---

## 6. 测试与 CI 评估

### 6.1 测试覆盖

| 指标 | 数值 |
|------|------|
| 测试文件 | 52 |
| 测试用例 | **337 OK**（~2.1s） |
| 源码 .py | 70 |
| 关键 E2E | `test_g0_g7_e2e.py`, `test_e2e_memory_recall.py` |
| 守卫测试 | plugin↔registry 漂移、`test_decision_log_citations.py`、population 不 fail-open、KB 只读 |
| Hermes 集成 | `test_hermes_plugin_integration.py`, `test_hermes_installer.py` |
| 推送调度 | `test_push_scheduler.py`（幂等、静默即认可 8 场景） |

**缺口**：
- 无安装后 **真实 Hermes smoke** 自动化测试（P3 待补）。
- 无 `rag.verify_quotes` 在真实 `hermes chat` 端到端强制验证。
- Dexcom live 集成测试为 mock 路径（符合阶段决策）。

### 6.2 CI 工作流

| Workflow | 触发路径 | 内容 | 评估 |
|----------|----------|------|------|
| `tests.yml` | src, tests, pyproject | `pip install -e .` + 全量 unittest | ✅ C1 已补 |
| `kb-quality.yml` | knowledge, rag, scripts | kb-validate + eval-rag ≥0.95 | ✅ |

**缺口**：两 workflow 路径不重叠 — 改 knowledge 不触发 unittest（反之亦然）。建议增加 `workflow_dispatch` 或扩大 paths 交叉，或定期全量 CI。

---

## 7. 积极发现（Wins）

1. **架构与业界最佳实践同构**：记忆三层 + validity-window 巩固、BM25+dense+RRF 检索期零 LLM、单 SQLite 统一存储 — 计划内联网校验已确认合理。
2. **G0–G8 能力层结构性完成**：15 工具全 active，无 planned 残留；domain 模型 10 个齐备。
3. **持续审计闭环有效**：R1–R6 七项实改 + 永久守卫；phantom doc 债已还（D010/D015/D022/D024 重建 + 扫描测试）。
4. **记忆/RAG 产品闭环可测**：`seed-demo` → L2/L3 非空 → prefetch 召回 — `test_e2e_memory_recall.py` 4 测试覆盖。
5. **推送调度符合 AGENTS.md**：策略+触发面、无常驻进程；静默即认可刻意收窄到 observing。
6. **安全链路扎实**：红区零泄漏、单位管线统一 `value_mg_dl`、加密 at-rest、三区路由与报告门一致。
7. **KB 工程质量高**：tier 护栏 + eval 门禁 hit@3=1.0；ingest/merge/validator 管线完整。
8. **Hermes 集成边界清晰**：cgm 工具插件 vs cgm_memory provider 分工明确；DB path 共享有测试。
9. **工具参数硬化**：杜绝 Python truthiness/string 强制转换类隐蔽 bug — 大量回归测试。
10. **文档治理改善**：ADR-0001、MEM-ARCH、DECISION_LOG D027–D044 已落盘且可被代码引用解析。

---

## 8. 推荐下一阶段优先级（有序）

与路线图计划 `bug-rag-refactored-pnueli.md` 对齐：

1. **P3 — 生产 Hermes 安装固化 + 可发布**  
   - installer 端到端 smoke（安装 → dev-status 非空 → `hermes chat` tool-call）  
   - `pip wheel` 构建 + 冒烟  
   - 收窄 `prototype_limit` 表述至真实残留项  

2. **P3 附属 — 默认数据/onboarding**  
   - 文档或 installer 可选 `seed-demo` / `import-cgm examples/cgm_test_dataset/cgm_3x14.csv`  
   - 解决 P0-R1 空库体验  

3. **P1-1 — 记忆时间戳修复**  
   - `review._accept` 支持真实 `occurred_at`；统一 report-candidate 与 seed-demo 路径  

4. **P1-3/P1-4 — 推送投递面**  
   - 评估 `push_tick` Hermes tool；gateway 接 `delivery.send` local_file 或 webhook  

5. **P5 — KB 可信化（策展）**  
   - 临床签核批次 verified=true；扩 eval 覆盖报告  

6. **P4 — 分析增强（非门槛）**  
   - MAGE/MODD + AGP 百分位文本附录  

7. **工程卫生**  
   - 更新 STATUS 计数；CI 路径交叉或 scheduled 全量跑  
   - 继续 executor/builder 边界提取（仅当有清晰切面+测试）  

8. **显式不做（除非用户授权）**  
   - Dexcom live 默认接入、微信全量推送、App UI、胰岛素剂量建议  

---

## 9. 附录：工具清单与 Hermes 映射

| 内部工具 | Hermes 外部名 | 暴露插件 |
|----------|---------------|----------|
| reports.generate | cgm_reports_generate | cgm |
| context.get_l0 | cgm_context_get_l0 | cgm |
| timeseries.get_points | cgm_timeseries_get_points | cgm |
| timeseries.get_aggregate | cgm_timeseries_get_aggregate | cgm |
| events.create | cgm_events_create | cgm |
| events.confirm | cgm_events_confirm | cgm |
| memory.list | cgm_memory_list | cgm + cgm_memory |
| memory.delete | cgm_memory_delete | cgm + cgm_memory |
| memory.confirm | — | cgm_memory only |
| memory.correct | — | cgm_memory only |
| hypothesis.update | cgm_hypothesis_update | cgm |
| rag.authoritative_search | cgm_rag_authoritative_search | cgm |
| rag.verify_quotes | cgm_rag_verify_quotes | cgm |
| data.dexcom_sync | cgm_data_dexcom_sync | cgm |
| delivery.send | cgm_delivery_send | cgm |

**CLI 命令（非 Hermes tool）**：`import-cgm`, `seed-demo`, `push-tick`, `kb-validate`, `eval-rag`, `context-build`, `hermes-install`, 等。

---

## 10. 相关文档索引

- 持续审计闭环：`docs/AUDIT-2026-06-07-持续审计闭环-R1-R6.md`
- 路线图与阶段验收：`~/.claude/plans/bug-rag-refactored-pnueli.md`
- 当前状态：`docs/STATUS-REPORT-2026-06-07.md`
- 决策日志：`docs/DECISION_LOG.md`
- 记忆架构：`docs/MEM-ARCH.md`, `docs/adr/ADR-0001-memory-and-knowledge-architecture.md`
- PM 蓝图提取：`.runtime/blueprint-extract.txt`（本审计生成，非正式交付物）

---

*本报告由 2026-06-07 功能与实现只读审计产出；未修改任何代码文件（除本文档）。*
