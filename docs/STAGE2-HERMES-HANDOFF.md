# Stage 2 交接给 Hermes — F3 / F4 / F5 并行实施计划

> 这是一份**自包含**的编排交接说明。你（Hermes，作为 Main / 总编排）拿到它即可接手，
> 不需要任何先前对话上下文。目标：用人物团队（Caesar / Apollo / Damocles / Luna / Ark / QA）
> 驱动 speckit SDD 流程，**并行**交付三个 feature，逐个人审合并。

---

## 0. 你的角色与团队协议

- 你是 **Main / 总编排**。按 SOUL 协议调度：
  **Caesar**（需求+架构+计划）→ **Apollo**（评审 + 写 PRD）→ **Damocles**（安全审计，SEC-###）
  → **Luna**（设计/伦理，DSG-###）→ **Ark**（实现+测试）→ **QA**（独立验收）。
- **硬约束（来自 Ark SOUL）**：写代码**必须**由 Ark 用 `write_file` 直接落盘，
  **不得**用 `delegate_task` 嵌套子代理写代码（会 hang）。子代理用于"短小聚焦的只读/分析子任务"。
- 三个 feature **彼此独立**，应**分派到不同的隔离工作区（worktree）并行推进**；
  feature **内部**串行走完 speckit 管线。

---

## 1. 项目背景与当前状态（务必先读）

- **仓库**：`/Users/yichizhang/code/CGM-Agent`（git）。这是 **Hermes CGM Agent** —— 个人血糖 AI
  能力层，挂在 Hermes Agent 外壳之后。Hermes CLI 是主外壳,本仓库是 CGM 能力层。
- **权威约束（不可协商）**：
  - 宪法 `.specify/memory/constitution.md`（7 条原则，下面 §4 列出与各 feature 相关的）
  - `docs/BACKLOG.md`（唯一 backlog 事实源）
  - `docs/adr/ADR-0001-memory-and-knowledge-architecture.md` + `docs/DECISION_LOG.md`（架构"为什么"）
- **已完成**：
  - **F1 Hermes 运行可用性**已合并进 `main`（统一 DB 路径 / 事件 schema 展平+强制 provenance /
    记忆工具单一通道）。spec 在 `specs/001-hermes-runtime-usability/`。
  - **G1 拆 `executor.py`**：`ToolExecutor` 已从 1019 行拆成 `src/hermes_cgm_agent/services/tools/handlers/`
    mixin 包（每个域一个文件）。**这是 Stage 2 并行的使能器** —— F3 改 `handlers/rag.py`、
    F4 改 `handlers/reports.py`、F5 改 `handlers/delivery.py`，互不冲突。
- **⚠️ 前置条件（第一步必须做）**：G1 在 **PR #3**（分支 `refactor/g1-split-executor`）尚未合并。
  **Stage 2 的三个分支必须从"已合并 G1 的 main"切出**，否则会拿不到 `handlers/` 拆分、产生大量冲突。
  → **先合并 PR #3 到 main，再开始 Stage 2。**

### 关键代码地图（post-G1）

```
src/hermes_cgm_agent/
  services/
    tools/
      executor.py            # ToolExecutor：仅 __init__ + execute 分发表(_DISPATCH)
      registry.py            # 工具 schema 注册（共享文件，见 §5 争用）
      handlers/
        base.py              # BaseToolHandler(_error_response) + ToolExecutionResponse
        helpers.py           # 纯函数 helper
        rag.py               # ← F3：authoritative_search / verify_quotes
        reports.py           # ← F4：reports.generate
        delivery.py          # ← F5：delivery.send
        memory.py events.py timeseries.py context.py dexcom.py
    rag/                     # ← F3：检索 + 引用校验
    safety/                  # ← F3：红/黄/绿安全路由
    reports/builder.py       # ← F4：报告叙事层（980 行）
    scheduling/scheduler.py  # ← F5：tiered-push 调度（策略/静默即认可核心已完成）
  knowledge/                 # ← F3-B2：authoritative_kb.json 等
integrations/hermes/cgm/
  plugin.yaml                # provides_tools 清单（共享文件，有漂移守卫测试）
```

### 测试与验证命令（统一用 Hermes venv）

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests
```

- **当前绿基线 = 374 测试**（合并 G1 后）。任何 feature 合并前必须 **≥ 该基线且全绿**。
- speckit `analyze` 必须 **0 个 CRITICAL** 才能进 `implement`。

---

## 2. 总目标

并行完成 **F3 / F4 / F5** 三个 feature，每个都走完整 speckit SDD 周期，过 Constitution Check，
独立可测、逐个人审合并到 main。**这是医疗产品 —— 真正的瓶颈是 review gate，不是吞吐；这是特性不是缺陷。**

---

## 3. 三个 Feature 的范围 / 文件 / 宪法约束

### F3 — 医学安全硬化（最高敏感度，**必须人审，禁止无人值守合并**）
合并条目 **B1 + B2 + B3**：
- **B1 verify_quotes 代码硬校验**：把"医学数字必须有权威来源"从 SKILL 软约束升级为**代码硬门**。
  主改 `services/rag/`（引用校验）+ `services/tools/handlers/rag.py`（`verify_quotes` handler）。
- **B2 KB 临床签核流程 + `kb.approve`**：578 卡当前全 `verified=false`；建签核流程，核心 ~100 卡先
  `verified=true`。**含外部依赖（临床审核人）** —— 若本轮无法闭环，按宪法把"未签核"做成 KNOWN GAP
  并标风险，不得自行把卡标 `verified=true`（只有记录了 `reviewer`/`reviewed_at` 外部审核 provenance 才能）。
- **B3 红区恢复二次确认 / 三区规则补全**：router 三区已有；核实并补 PRD §2.3 细则
  （如"红区后 2h 恢复需二次确认"）。主改 `services/safety/`。
- **宪法**：原则 **I 医学零容错 & 权威只读**、原则 **III 硬编码安全路由（不可协商）**。
  Damocles 必须出 SEC-### 并对 LLM 攻击面（OWASP LLM Top 10，尤其 prompt injection / 越权）做审计。

### F4 — 陪伴者叙事 + 协商交互
合并条目 **C1 + C2 + C3**：
- **C1 报告中文叙事层**：builder 已有 `audience` + `_daily_card_section` 骨架；补 TIR→生活语言、
  周报/医生版/家属版叙事差异。主改 `services/reports/builder.py`（+ 可能 `handlers/reports.py`）。
- **C2 协商式假设验证话术**：四状态机（candidate/observing/stable/invalid）已有；接入话术 +
  邀请验证流程（PRD §2.4）。
- **C3 连续异常渐进关心 + 脆弱人群更早干预**：SOUL 定义了第 1/3/5 天升级策略；核实是否在调度/报告里实现。
- **宪法**：原则 **IV 知情陪伴者人设契约**。Luna 必须出 DSG-###（语气、共情、不制造焦虑、文化敏感）。

### F5 — 主动推送 + 投递闭环（**blast radius 最大，见 §5**）
合并条目 **D1 + D2**：
- **D1 push-tick 工具化 + cron**：调度策略/静默即认可核心已完成（`scheduling/scheduler.py`），但
  `push-tick` 仅 CLI、未在 registry/plugin.yaml。包成 Hermes tool 并接 Hermes cron。
  → **新增工具** = 改 `services/tools/registry.py` + `handlers/`（新建 handler + `handlers/__init__.py`
  + `executor.py` 的 `_DISPATCH`）+ `integrations/hermes/cgm/plugin.yaml`。
- **D2 delivery webhook/email 实现**：当前仅 `local_file` 完整，email/webhook 记为 `queued`；
  先做 webhook HTTP POST。主改 `services/tools/handlers/delivery.py`。
- **宪法**：原则 **VII Hermes 边界 & 数据隐私**（timing/投递通道由 Hermes/cron 外部驱动，
  本层只拥有策略/内容/状态；webhook 出网注意 PHI 不外泄）。

---

## 4. 每个 Feature 的工作流（speckit 管线 × 人物分工）

在该 feature 的隔离 worktree 里，**串行**执行（具备 speckit skills 的编码代理 = Claude Code 来跑 `/speckit-*`）：

| 步骤 | speckit 命令 | 主责人物 | 产出 / 门禁 |
|---|---|---|---|
| 1 需求+规格 | `/speckit-specify "<seed>"` | Caesar | `specs/NNN-…/spec.md` + 质量 checklist |
| 2 澄清 | `/speckit-clarify` | Caesar↔用户 | 消歧后回写 spec（≤5 问） |
| 3 计划 | `/speckit-plan` | Caesar | research/data-model/contracts/quickstart + **Constitution Check 必须 7/7 过** |
| 4 安全审计 | （审 plan） | **Damocles** | SEC-### 写回 spec/plan；HIGH/CRITICAL 必须先解决 |
| 5 设计/伦理 | （审 plan） | **Luna** | DSG-### 写回（F4 必走；F3/F5 视情） |
| 6 评审+PRD | `/speckit-analyze` 后 | **Apollo** | analyze **0 CRITICAL**；Apollo 出 PRD（SEC/DSG 为不可协商项） |
| 7 任务 | `/speckit-tasks` | Apollo/Caesar | `tasks.md`（test-first） |
| 8 实现 | `/speckit-implement` | **Ark**（直接 write_file） | 全程测试绿，每阶段 commit |
| 9 验收 | — | **QA** | 独立跑测试 + 需求可追溯矩阵；出 PASS/FAIL |

- **test-first 不可协商**（宪法原则 V）：先写失败测试再实现。
- **可追溯、无幻影文档**（宪法原则 VI）：决策入 `docs/DECISION_LOG.md`（有引用守卫测试 `test_decision_log_citations.py`，
  代码里引用 D0xx 前必须先在 DECISION_LOG 建条目）；FIX-PLAN 类临时文档不要新建。

---

## 5. 并行与合并策略（关键）

**分支**：每个 feature 从"已合并 G1 的 main"切独立分支 + 独立 worktree：
- F3 → `002-medical-safety-hardening`
- F4 → `003-companion-narrative`
- F5 → `004-push-delivery-loop`

> speckit 目录自动按序编号（001 已用）。**并行跑 `/speckit-specify` 可能撞号** ——
> 建议**先串行跑完三个 `specify`**（拿到 002/003/004 目录），**再并行**推进各自的 plan→implement。

**文件争用图**（决定冲突风险）：
- ✅ **隔离良好**：F3(`rag/`+`safety/`+`handlers/rag.py`) · F4(`reports/builder.py`+`handlers/reports.py`)
  —— 目录基本不相交，可放心并行。
- ⚠️ **F5 blast radius 最大**：新增工具会动**共享文件**：
  `services/tools/registry.py`、`integrations/hermes/cgm/plugin.yaml`、`handlers/__init__.py`、
  `executor.py` 的 `_DISPATCH`，以及守卫测试
  `tests/test_tool_registry.py`（`ExecutorDispatchCoverageTests`）、
  `tests/test_hermes_plugin_integration.py`（工具集断言 + plugin.yaml 漂移守卫）。
  → F5 的 Ark 必须**同步**更新这些（守卫测试会强制你不漏接 handler / 不让 plugin.yaml 漂移 —— 这是帮你）。
- ⚠️ **append-only 轻冲突**：`docs/BACKLOG.md`、`docs/DECISION_LOG.md` 三个 feature 都会追加；
  合并时按行追加即可解决。

**合并顺序（推荐）**：**F4 → F3 → F5**（先合最隔离的 F4；F3 需最久人审；F5 动共享文件放最后）。
每合并一个，**其余分支 rebase 到新 main 后重跑全套测试到绿**再继续。也可按"谁先 ready 谁先合"，
但 F5 之后若有其它分支，必处理 registry/plugin/guard-test 冲突。

**每个 feature 的 PR**：推分支 → 开 PR → **人审 gate** → 合并。F3 **禁止**无人值守自动合并。

---

## 6. 红线 / 必须人审（医疗项目克制）

- ❌ **禁止无人值守并行/自动合并**：双轨记忆隔离、安全闸、医学数值（F3 全部、F4 的医学陈述）。
- ✅ **可较放手**：测试编写、F4 文案打磨、F5 webhook、纯重构、VERIFY 类排查。
- 宪法 **不可协商** 原则：III（安全路由）、V（test-first + 绿 CI）。违反即 BLOCK。
- Damocles 对 F3/F5 必须覆盖 **OWASP LLM Top 10**（尤其 LLM01 prompt injection、LLM07/08 工具越权/过度代理、
  LLM06 敏感信息泄露）。webhook（F5-D2）出网严禁带 PHI 明文。

---

## 7. 完成定义（DoD，每个 feature）

- [ ] speckit 全链产物齐全（spec/plan/research/data-model/contracts/quickstart/tasks），Constitution Check 7/7。
- [ ] `analyze` 0 CRITICAL；SEC-###（Damocles）、DSG-###（Luna）已并入 PRD 并实现。
- [ ] 全套测试绿且 **≥ 基线**（合并前在最新 main 上复核）。
- [ ] QA 出具 PASS（含需求可追溯矩阵）。
- [ ] 决策入 DECISION_LOG；BACKLOG 对应条目状态更新。
- [ ] PR 经人审合并。

---

## 8. 起手命令（每个 feature 的 specify 种子）

```
# 前置：先合并 PR #3（G1）到 main；三个分支从新 main 切出。

# F3（→ specs/002-…）
/speckit-specify F3 医学安全硬化：verify_quotes 升级为代码硬校验（医学数字必须有权威 KB 来源，
  否则拦截）；KB 临床签核流程 + kb.approve 工具（核心~100卡先 verified=true，需记录 reviewer/reviewed_at
  外部审核 provenance）；红区恢复二次确认与三区规则补全（PRD §2.3）。约束宪法原则 I/III。

# F4（→ specs/003-…）
/speckit-specify F4 陪伴者叙事 + 协商交互：报告中文生活化叙事层（TIR→生活语言，日/周/医生/家属版差异）；
  协商式假设验证话术（candidate/observing/stable/invalid 四态 + 邀请验证）；连续异常渐进关心与脆弱人群更早干预。
  约束宪法原则 IV，走 Luna 设计/伦理评审出 DSG-###。

# F5（→ specs/004-…）
/speckit-specify F5 主动推送 + 投递闭环：把 push-tick 包成 Hermes tool 并接 cron（新增工具需同步
  registry + handlers + _DISPATCH + plugin.yaml + 守卫测试）；delivery 实现 webhook HTTP POST（先于 email），
  PHI 不出网。约束宪法原则 VII。
```

---

## 9. 不在本轮范围

- **F2 数据来源方向**（Libre/Nightscout vs 仅 CSV）：暂忽略。CSV 导入已够喂数据跑通 Stage 2；
  Dexcom 已冻结。它只是战略 ADR，不阻塞 F3/F4/F5。
- **G1 剩余**（`cli.py` 1252 / `builder.py` 980 拆分）：归 F6 持续技术债，不在 Stage 2 并行争用点上。
  （注：F4 会大改 `builder.py`，若顺手可在 F4 内做小幅内聚拆分，但非必须。）
- **F7 分析深度**（MAGE/MODD/AGP）：DEFERRED。
