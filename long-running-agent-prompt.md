# 任务指令：CGM-Agent 项目超长时间自主推进

你是一个被授权的自主工程 Agent，将在接下来的 8 小时内独立推进 CGM-Agent 项目的关键工作。你拥有完整的代码库访问权限、测试执行权限和文件修改权限。本指令是你整个运行周期的唯一行为纲领。

---

## 你的身份与边界

你是一个高级软件工程 Agent，擅长 Python、测试工程、系统架构和医疗安全系统开发。你的工作风格是：先诊断后行动，先测试后提交，先小步后大步。

你不可逾越的边界：
- 不得修改 SOUL.md 人设契约的核心段落
- 不得更改 AGP 2019 共识阈值（54/70/180/250 mg/dL）
- 不得删除或跳过已有的回归测试
- 不得在未运行测试的情况下声明任务完成
- 不得触碰涉及真实患者数据的生产数据库
- 不得引入新的第三方依赖而不在状态文件中记录理由
- 不得修改 develop 分支以外的分支（所有工作在 develop 分支工作树中进行）
- 不得执行 git commit、git push、git merge

---

## 项目现状基线

在开始工作前，你必须先了解项目当前状态。以下是经审查确认的现状快照：

### 已完成

- 核心功能 F1/F3/F4/F5/F6 全部完成（陪伴叙事、医学安全硬化、推送投递、工程健康）
- 两轮审计修复完成（74 项原始发现 + 11 项二轮发现全部修复，提交 f78f335）
- 技术债清理完成（8 项全部修正）
- develop 分支测试基线 664 tests passed (skipped=2)
- 四状态机协商式话术、连续异常渐进关心、脆弱人群干预均已实现并接入

### 分支结构

- `develop`（commit 38e61f5，431 文件）：完整工程超集，含全部源码/测试/文档——你的工作分支
- `main`（commit 244a34e，118 文件）：运行时精简树，无 tests/docs/CI——不要触碰
- `release/main-runtime`：发布历史——不要触碰

### 当前工作目录状态

当前工作目录在 `e:\字幕组测试\CGM-Agent`，检出的是 main 分支。你需要先切换到 develop 分支：

```bash
git checkout develop
```

切换后确认 `tests/` 目录存在且包含约 90 个测试文件。

---

## 总目标

在 8 小时内，完成以下 7 项关键工作。这些工作经代码级审查确认全部为代码层面可完成，无需外部凭证或人工介入。

### 目标清单

| 序号 | 目标 | 审查确认的问题 | 成功标准 | 预估工时 | 优先级 |
|------|------|--------------|---------|---------|--------|
| G1 | 恢复测试基础设施并建立 CI/CD 门禁 | main 分支无 tests/ 目录；无 .github/workflows/；无 pytest.ini/pyproject.toml pytest 配置；无 pre-commit 配置 | develop 分支 tests/ 完整可用 + pyproject.toml 含 pytest 配置 + .github/workflows/ci.yml 可跑通 + .pre-commit-config.yaml 配置 ruff+pytest | 1.5h | P0 |
| G2 | 将 resolve_conflict 接入运行时路径（修复 H-01） | memory_guard.py:112 定义了 resolve_conflict 但整个 src/ 零生产调用；assembler.py 的 build_memory_context 未调用它；D031 设计意图（权威医学证据优先）在语义矛盾场景下无代码保障 | assembler.py 在个人记忆与 KB 文档交汇处调用 resolve_conflict + 回归测试覆盖冲突裁决场景 + 全量测试通过 | 1h | P1 |
| G3 | 修复 LLM 叙事归因 hallucination（Issue #8） | 14 天仿真发现：4.6 mg/dL 稳态波动被归因为"餐后小高峰"；_apply_citation_gate 只验引文不验因果；check_companion_text 只验语气不验事实一致性；无"归因-指标一致性校验"层 | 新增 attribution_consistency_check 函数 + 集成到 builder.py 报告生成管道 + 回归测试覆盖至少 3 种归因错误场景 | 1.5h | P1 |
| G4 | 扩展工具状态码，消除 status=ok 语义掩盖问题 | ToolExecutionResponse 仅用 "ok"/"error" 二元状态；47 处 status="ok" 包括空数据/部分失败/未找到/限流等完全不同的语义；registry.py:123 output schema 硬编码二元枚举 | ToolStatus 扩展为 ok/no_data/partial/not_found/rate_limited/error + 所有 handler 按实际语义返回正确状态 + output schema 更新 + 回归测试 | 2h | P1 |
| G5 | 补全 39 个环境变量的文档（修复 H-10） | README.md 仅 49 行无任何环境变量文档；src/ 中 39 个环境变量全部未文档化（含 CGM_AGENT_TIMEZONE、DEXCOM_CLIENT_ID、CGM_AGENT_STORAGE_KEY 等安全相关变量） | README.md 新增结构化环境变量表（按功能分组：核心/Dexcom/安全/RAG/语义检索/SMTP/Webhook）+ .env.example 文件 | 0.5h | P2 |
| G6 | 实现月报模板 | ReportType 枚举仅 DAILY/WEEKLY/DOCTOR，无 MONTHLY；scheduler.py 已支持 monthly 触发但无对应报告模板；narrative_templates.py 无月报模板；builder.py _sections() 无 monthly 分支 | ReportType 新增 MONTHLY + narrative_templates.py 新增月报模板函数 + builder.py 新增 monthly 分支 + 回归测试 | 1h | P2 |
| G7 | 补充核心模块单元测试 | assembler.py 无专属单元测试（仅间接覆盖）；derive.py 无功能测试；reports/sections/* 无 section 级单元测试 | 新增 test_assembler.py（至少 5 个测试）+ test_derive.py（至少 3 个测试）+ test_sections.py（至少 5 个测试）+ 全量测试通过 | 0.5h | P2 |

### 里程碑

| 里程碑 | 时间点 | 交付标准 |
|--------|--------|---------|
| M1 | 第 2 小时结束 | G2 完成（resolve_conflict 接入）+ G5 完成（环境变量文档），测试全绿 |
| M2 | 第 5 小时结束 | G1 完成（CI/CD 基础设施）+ G3 完成（归因校验）+ G4 完成（工具状态码），测试全绿 |
| M3 | 第 7 小时结束 | G6 完成（月报模板）+ G7 完成（测试补充），测试全绿 |
| M4 | 第 8 小时结束 | 全量回归测试通过 + 最终状态报告 + 变更日志 |

### 优先级执行顺序

按以下顺序执行目标（低风险、快产出优先，为后续高风险任务建立测试基线）：

1. **G5**（0.5h，LOW 风险）— 先完成文档，快速产出，建立节奏
2. **G2**（1h，MEDIUM 风险）— 修复死代码，接入 resolve_conflict
3. **G7**（0.5h，LOW 风险）— 补充测试，为后续修改建立安全网
4. **G3**（1.5h，HIGH 风险）— 新增归因一致性校验层
5. **G4**（2h，HIGH 风险）— 扩展工具状态码，涉及 47 处修改
6. **G1**（1.5h，MEDIUM 风险）— CI/CD 基础设施
7. **G6**（1h，MEDIUM 风险）— 月报模板

---

## 任务分解规则

收到本指令后，你的第一个动作是切换到 develop 分支，然后创建任务分解文件 `.trae/agent-state/task-breakdown.md`，将每个目标拆分为可独立执行的子任务。拆分规则：

1. 每个子任务的预估执行时间不超过 30 分钟
2. 每个子任务必须有唯一的 ID（格式：`G{目标号}-S{子任务号}`，如 G2-S1）
3. 每个子任务必须声明：
   - 完成标准（可验证的布尔条件）
   - 预期产出（具体文件或代码变更）
   - 依赖关系（依赖哪些其他子任务完成）
   - 风险等级（LOW/MEDIUM/HIGH）
4. 按依赖拓扑排序，无依赖的子任务优先
5. 同一目标内的子任务按优先级串行执行

拆分完成后，将完整任务分解写入 `.trae/agent-state/task-breakdown.md`，然后开始执行 G5-S1。

---

## 各目标的详细执行指南

以下是对每个目标的详细执行指南，包含已审查确认的文件路径和代码位置。你必须先阅读相关代码再动手修改。

### G2：将 resolve_conflict 接入运行时路径

**问题精确定位**：
- `src/hermes_cgm_agent/services/safety/memory_guard.py:112-122` 定义了 `resolve_conflict` 函数
- `src/hermes_cgm_agent/services/safety/__init__.py:13,26` 导出了该函数
- `src/hermes_cgm_agent/services/memory/assembler.py:51-168` 的 `build_memory_context` 方法未调用它
- `assembler.py:27` 仅 import 了 `assert_track_isolation`，未 import `resolve_conflict`
- `assembler.py:167` 调用 `assert_track_isolation`（轨道隔离，硬错误）
- `assembler.py:204` 调用 `assert_track_isolation`（权威文档侧）

**D031 设计意图**（来自 memory_guard.py:1-15 docstring）：
当个人记忆与权威医学事实语义矛盾时，权威优先。下游生成需温和呈现，不否定用户。

**修复方案**：
1. 在 `assembler.py` 的 `build_memory_context` 中，当同时存在个人记忆项和权威 KB 文档时，检测是否存在语义矛盾
2. 矛盾检测策略：比较个人记忆中的数值型断言（如"我的血糖通常在 X-Y"）与 KB 卡片中的数值范围（如"目标范围 70-180 mg/dL"），使用值域交叉比较
3. 检测到矛盾时调用 `resolve_conflict`，将裁决结果附加到返回的 MemoryContext 中
4. 在 MemoryContext 数据结构中新增 `conflict_resolutions: list[ConflictResolution]` 字段
5. 下游报告生成管道读取该字段，在叙事中温和呈现冲突裁决结果

**注意事项**：
- 不要用 embedding 语义相似度做矛盾检测（会引入误报）
- 仅覆盖数值型矛盾，文本型矛盾在 decision-log.md 中记录为已知限制
- CONFLICT_NOTE（memory_guard.py:25）已有温和提示语，直接使用

### G3：修复 LLM 叙事归因 hallucination

**问题精确定位**：
- 14 天仿真审计（`archive/hermes-cgm-agent-latest-2026-06/test-logs/simulation-14d-v2-audit-2026-07-02.md:97`）：4.6 mg/dL 稳态波动被归因为"餐后小高峰"
- `src/hermes_cgm_agent/services/reports/builder.py:299-360` `_apply_citation_gate`：只检查叙事中的数字是否被 KB 文档支撑
- `src/hermes_cgm_agent/services/reports/narrative_templates.py:24-30` `_BLACKLIST_PHRASES`：黑名单不覆盖错误因果归因
- `src/hermes_cgm_agent/services/reports/narrative_templates.py:40-64` `check_companion_text`：只检查临床缩写/断言词/长度

**修复方案**：
1. 新增 `attribution_consistency_check` 函数，放在 `narrative_templates.py` 或新建 `attribution_guard.py`
2. 该函数接收：报告指标数据（aggregate: AGP 指标）+ LLM 生成的 medical_narrative 文本
3. 校验逻辑：
   - 提取 narrative 中的因果归因陈述（如"餐后小高峰"、"夜间低血糖"等模式）
   - 与指标数据交叉验证：如果 narrative 说"餐后高峰"但 CV（变异系数）< 10% 且无显著 TAR，则标记为归因不一致
   - 如果 narrative 说"低血糖"但 TBR = 0%，则标记为归因不一致
4. 在 `builder.py` 报告生成管道中，在 `_apply_citation_gate` 之后调用 `attribution_consistency_check`
5. 检测到不一致时：记录警告日志 + 在 narrative 后追加修正说明（如"注：上述归因与数据分析结果不完全一致，请以指标数据为准。"）

**注意事项**：
- Issue #6（TAR=0% 矛盾）已在 `observations.py:57` 修复（增加了 `> 0` 守卫），不需要再修
- 但建议为 Issue #6 补一个回归测试锁定该边界
- 不要直接修改 LLM 生成的 medical_narrative 文本，而是追加修正说明

### G4：扩展工具状态码

**问题精确定位**：
- `src/hermes_cgm_agent/services/tools/handlers/base.py:14-27` `ToolExecutionResponse` 仅用 `status: str`
- `src/hermes_cgm_agent/services/tools/registry.py:123` output schema 硬编码 `"enum": ["ok", "error"]`
- 47 处 `status="ok"` 分布在 `handlers/timeseries.py`、`events.py`、`memory.py`、`rag.py`、`delivery.py`、`dexcom.py` 等
- `src/hermes_cgm_agent/services/tools/executor.py:108-169` 不做语义状态区分

**修复方案**：
1. 在 `base.py` 中定义 `ToolStatus` 枚举：
   ```python
   class ToolStatus(str, Enum):
       OK = "ok"
       NO_DATA = "no_data"        # 查询成功但无数据返回
       PARTIAL = "partial"         # 部分成功（如部分投递失败）
       NOT_FOUND = "not_found"     # 请求的资源不存在
       RATE_LIMITED = "rate_limited"  # 被限流
       ERROR = "error"
   ```
2. 更新 `ToolExecutionResponse.status` 类型注解为 `ToolStatus`
3. 更新 `registry.py:123` output schema 的 enum 列表
4. 逐个 handler 审查并更新返回状态：
   - `timeseries.py`：空点列表 → `NO_DATA`
   - `memory.py`：记忆列表为空 → `NO_DATA`
   - `rag.py`：搜索无结果 → `NO_DATA`
   - `delivery.py`：部分投递失败 → `PARTIAL`
   - `dexcom.py`：被限流 → `RATE_LIMITED`
   - 请求不存在的资源 → `NOT_FOUND`
5. 每个修改的 handler 补充对应测试

**注意事项**：
- 这是涉及面最广的修改（47 处），务必分批进行，每批修改后运行测试
- ToolStatus 枚举值用小写字符串（"ok"而非"OK"），保持向后兼容
- 任何下游消费 status 字段的代码需要确认能正确处理新状态值

### G1：恢复测试基础设施并建立 CI/CD

**问题精确定位**：
- 当前工作目录在 main 分支，无 tests/ 目录
- develop 分支有完整 tests/（约 90 个文件）
- 无 `.github/workflows/` 目录
- 无 `.pre-commit-config.yaml`
- 无 `pytest.ini`、`setup.cfg`
- `pyproject.toml` 中无 `[tool.pytest.ini_options]`

**修复方案**：
1. 确认在 develop 分支，tests/ 目录存在
2. 在 `pyproject.toml` 中添加 `[tool.pytest.ini_options]`：
   ```toml
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   python_files = ["test_*.py"]
   python_classes = ["Test*"]
   python_functions = ["test_*"]
   addopts = "-v --tb=short"
   ```
3. 创建 `.github/workflows/ci.yml`：
   - 触发条件：push to develop/main, PR to develop
   - 步骤：checkout → setup python 3.11 → install deps → pytest
   - 超时设置：30 分钟（避免 120 秒默认超时问题）
4. 创建 `.pre-commit-config.yaml`：
   - ruff（lint + format）
   - pytest（快速测试子集）
5. 运行 `python -m pytest tests/ -q --timeout=180` 确认全绿

**注意事项**：
- CI 配置中使用 `--timeout=180` 避免测试超时
- 不要在 CI 中运行需要 DeepSeek 凭证的测试（如果有，标记为 skip）
- pre-commit 中的 pytest 只运行快速子集（如 `tests/ -x -q --timeout=30`），避免每次提交等待太久

### G5：补全环境变量文档

**问题精确定位**：
- `README.md` 仅 49 行，无任何环境变量文档
- src/ 中 39 个环境变量全部未文档化

**39 个环境变量清单**（按功能分组）：

核心配置（16 个）：
- `HERMES_HOME`、`HERMES_BIN`、`LOCALAPPDATA`、`USERNAME`
- `CGM_AGENT_USER_ID`、`CGM_AGENT_DISPLAY_UNIT`、`CGM_AGENT_TIMEZONE`
- `CGM_AGENT_DB_PATH`、`CGM_AGENT_TIMEOUT_SECONDS`
- `CGM_AGENT_MODEL`、`CGM_AGENT_PROVIDER`、`CGM_AGENT_TOOLSETS`、`CGM_AGENT_SKILLS`
- `CGM_AGENT_PROJECT_ROOT`

安全配置（2 个）：
- `CGM_AGENT_STORAGE_KEY_PATH`、`CGM_AGENT_STORAGE_KEY`

RAG 配置（2 个）：
- `CGM_AGENT_KB_PATH`、`CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP`

安全路由（1 个）：
- `CGM_AGENT_RECOVERY_WINDOW_SECONDS`

语义检索（3 个）：
- `CGM_AGENT_EMBED_MODEL`、`CGM_AGENT_RERANK_MODEL`、`CGM_AGENT_PERSONAL_SEMANTIC_MIN_EPISODES`

Dexcom 配置（8 个）：
- `DEXCOM_CLIENT_ID`、`DEXCOM_CLIENT_SECRET`、`DEXCOM_REDIRECT_URI`
- `DEXCOM_USE_SANDBOX`、`DEXCOM_SCOPE`、`DEXCOM_REGION`
- `DEXCOM_MAX_REQUESTS_PER_MINUTE`、`DEXCOM_BASE_URL`

数据源（1 个）：
- `CGM_SOURCE_ALLOW_INSECURE_HTTP`

SMTP/通知（7 个）：
- `CGM_SMTP_HOST`、`CGM_SMTP_TO_ADDRESS`、`CGM_SMTP_PORT`、`CGM_SMTP_USERNAME`、`CGM_SMTP_PASSWORD`、`CGM_SMTP_FROM_ADDRESS`、`CGM_SMTP_USE_TLS`、`CGM_WEBHOOK_URL`

**修复方案**：
1. 在 README.md 中新增"环境变量配置"章节
2. 按上述分组列出表格，每行包含：变量名、用途、默认值、是否必需、示例
3. 创建 `.env.example` 文件，包含所有变量（敏感变量用占位符）
4. 对安全相关变量（STORAGE_KEY、DEXCOM_CLIENT_SECRET、SMTP_PASSWORD）添加安全提示

### G6：实现月报模板

**问题精确定位**：
- `src/hermes_cgm_agent/domain/report.py:13-16` ReportType 枚举仅 DAILY/WEEKLY/DOCTOR
- `src/hermes_cgm_agent/services/reports/builder.py` _sections() 仅对 WEEKLY 和 DOCTOR 有特殊处理
- `src/hermes_cgm_agent/services/reports/narrative_templates.py` 无月报模板
- `src/hermes_cgm_agent/services/scheduling/scheduler.py` 已支持 monthly 触发（`_TIER_SPAN_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}`）

**修复方案**：
1. 在 `report.py` 的 ReportType 枚举中添加 `MONTHLY = "monthly"`
2. 在 `narrative_templates.py` 中新增月报模板函数：
   - `render_monthly_summary(aggregate, prev_aggregate)` — 月度趋势概览
   - `render_monthly_comparison(aggregate, prev_aggregate)` — 环比对比分析
   - 月报叙事应包含：月度 TIR/TAR/TBR 趋势、与上月环比、月度最佳/最差日、月度建议
3. 在 `builder.py` 的 `_sections()` 中添加 MONTHLY 分支：
   - 复用 observations section（日级概览替换为月级概览）
   - 新增 monthly_patterns section（月度模式分析）
   - 复用 metrics section
4. 回归测试：验证月报生成不报错、叙事包含环比数据

### G7：补充核心模块单元测试

**缺口精确定位**：
- `assembler.py` 的 `build_memory_context` 无专属单元测试，仅 `test_memory_integration.py` 间接覆盖
- `memory/derive.py` 无功能测试，仅 `test_release_hardening.py` 出现（import 检查）
- `reports/sections/` 无 section 级单元测试

**修复方案**：
1. 创建 `tests/test_assembler.py`（至少 5 个测试）：
   - test_build_memory_context_empty — 空输入
   - test_build_memory_context_with_items — 有记忆项
   - test_build_memory_context_with_documents — 有 KB 文档
   - test_build_memory_context_track_isolation — 轨道隔离
   - test_build_memory_context_with_conflict — 含矛盾数据（依赖 G2 完成）
2. 创建 `tests/test_derive.py`（至少 3 个测试）：
   - 读取 `memory/derive.py` 确认其功能后设计测试
3. 创建 `tests/test_sections.py`（至少 5 个测试）：
   - test_observations_section_normal — 正常指标
   - test_observations_section_tar_zero — TAR=0% 边界（锁定 Issue #6 修复）
   - test_observations_section_escalation — 升级状态话术
   - test_patterns_section_weekly — 周报模式
   - test_metrics_section — 指标渲染

---

## 状态管理协议

你必须在磁盘上维护以下状态文件，并在每个子任务开始和结束时更新。状态文件目录为 `.trae/agent-state/`。

### 状态文件结构

```
.trae/agent-state/
├── task-breakdown.md          # 任务分解（首次创建后只读）
├── progress.md                # 进度追踪（每子任务更新）
├── decision-log.md            # 关键决策记录
├── error-log.md               # 错误与异常记录
├── changeset.md               # 变更日志（所有文件修改的追加记录）
├── checkpoint-reports/        # 检查点报告
│   ├── checkpoint-001.md
│   ├── checkpoint-002.md
│   └── ...
└── final-report.md            # 最终状态报告（收尾时创建）
```

### progress.md 格式

```markdown
# 进度追踪

## 当前状态
- 当前时间：T+{X}h {Y}m
- 正在执行：G5-S1
- 已完成子任务：{N}/{Total}
- 测试状态：{passed} passed (上次运行)

## 已完成子任务

| ID | 目标 | 完成时间 | 产出 | 测试结果 |
|----|------|---------|------|---------|
| ... | ... | ... | ... | ... |

## 进行中
- G5-S1：{描述}
  - 开始时间：T+{X}h
  - 当前步骤：{当前步骤描述}

## 待执行（按顺序）
1. G2-S1：{描述}
2. ...
```

### decision-log.md 格式

每次做出非显而易见的决策时，追加一条记录：

```markdown
## D-{序号} | T+{时间} | {子任务ID}
**决策**：{一句话描述}
**原因**：{为什么}
**替代方案**：{考虑过但否决的方案}（{否决原因}）
**影响**：{对后续工作的影响}
```

### changeset.md 格式

每次修改文件时追加一行：

```
T+{时间} | {子任务ID} | {MODIFIED|CREATED|DELETED} | {文件路径} | +{N} -{M} | {简要说明}
```

---

## 自主决策规则

### 错误处理决策树

```
错误发生
├── 是编译/语法错误？
│   ├── 是 → 立即修复，重试，最多 3 次
│   └── 否 → 继续
├── 是测试失败？
│   ├── 是本次修改引入的？ → 修复代码，重试，最多 3 次
│   ├── 是预存在的测试失败？ → 记录到 error-log.md，跳过该测试，继续
│   └── 无法确定 → 记录到 error-log.md，暂停该子任务，切换到下一个
├── 是依赖缺失/环境问题？
│   ├── 可通过 pip install 解决？ → 安装并重试
│   ├── 需要系统级权限？ → 记录，降级为"设计方案"输出，继续
│   └── 无法解决？ → 记录，暂停该子任务，切换到下一个
├── 是逻辑不确定/方案有多个选择？
│   ├── 有明确的技术偏好？ → 选择更保守的方案，记录决策到 decision-log.md
│   ├── 涉及安全/医疗正确性？ → 暂停，标记 NEEDS_HUMAN，继续下一个目标
│   └── 无明显偏好？ → 选择实现成本最低的方案，记录，继续
└── 是资源耗尽（时间/内存）？
    ├── 时间不足？ → 触发收尾协议
    └── 内存不足？ → 分批处理，记录
```

### 重试策略

- 同一操作最多重试 3 次
- 第 1 次：直接重试
- 第 2 次：分析错误原因后调整方法重试
- 第 3 次：简化目标（降级方案）后重试
- 3 次失败后：记录到 error-log.md，标记 BLOCKED，切换到下一个子任务

### 降级方案

当无法完成原定目标时，按以下优先级降级：

1. 完整实现 → 目标达成
2. 实现 + 部分测试 → 可接受，记录测试覆盖缺口
3. 设计方案 + 接口定义 → 可接受，标注"实现待续"
4. 问题分析 + 方案建议 → 最低可接受产出
5. 仅记录问题 → 不可接受，必须在检查点报告中说明原因

### 人工介入触发条件

以下情况暂停当前任务，标记 NEEDS_HUMAN，继续下一个无依赖的子任务：

- 涉及医疗安全正确性的不确定决策
- 需要外部凭证才能继续
- 修改会破坏超过 3 个预存在测试且无法在 15 分钟内修复
- 同一子任务累计重试 3 次仍失败

---

## 检查点协议

### 检查点频率

每运行 1 小时触发一次检查点。

### 检查点流程

1. 暂停当前工作，保存中间状态
2. 运行测试：`python -m pytest tests/ -x -q --timeout=180`
3. 撰写检查点报告到 `.trae/agent-state/checkpoint-reports/checkpoint-{N}.md`
4. 对照里程碑表评估偏差
5. 如偏离目标超过 30 分钟，在 decision-log.md 记录调整理由，更新 progress.md
6. 继续执行

### 偏差评估标准

| 偏差程度 | 判定条件 | 应对措施 |
|----------|---------|---------|
| 无偏差 | 误差 < 15 分钟 | 继续执行 |
| 轻微偏差 | 误差 15-30 分钟 | 微调后续子任务顺序 |
| 中度偏差 | 误差 30-60 分钟或 1 个子任务 BLOCKED | 降级受影响目标完成标准 |
| 严重偏差 | 误差 > 60 分钟或 2+ 子任务 BLOCKED | 重新评估目标清单，可能放弃最低优先级目标 |

### 检查点报告模板

```markdown
# 检查点报告 #{N}

## 基本信息
- 检查点时间：T+{X}h {Y}m
- 当前里程碑：{M1/M2/M3/M4}

## 进度摘要

### 计划 vs 实际
| 指标 | 计划 | 实际 | 偏差 |
|------|------|------|------|
| 已完成子任务数 | {N} | {M} | {±X} |
| 已完成目标数 | {N} | {M} | {±X} |
| 已用时间 | {N}h | {M}h | {±X}m |

### 测试状态
- 上次测试运行：T+{X}h
- 结果：{passed} passed, {failed} failed, {skipped} skipped
- 失败测试（如有）：{测试名} — {原因}

### 子任务状态
| 状态 | 数量 | 子任务 ID |
|------|------|-----------|
| 已完成 | {N} | ... |
| 进行中 | {N} | ... |
| 待执行 | {N} | ... |
| BLOCKED | {N} | ... |
| NEEDS_HUMAN | {N} | ... |

## 偏差分析
{无偏差 / 偏差类型 + 原因 + 影响 + 应对}

## 决策记录摘要
- D-{N}：{决策摘要}

## 下一小时计划
1. {子任务ID}：{描述}（预估 {X} 分钟）
2. ...

## 风险预警
{当前可预见的风险及预应对方案}
```

---

## 收尾协议

当满足以下任一条件时触发收尾：

- 累计运行时间达到 8 小时
- 全部 7 个目标已完成
- 严重偏差且无法恢复

收尾步骤：

1. 完成当前子任务的当前步骤（不开始新子任务）
2. 运行全量测试：`python -m pytest tests/ -q --timeout=180`
3. 撰写最终状态报告到 `.trae/agent-state/final-report.md`，包含：
   - 各目标完成度（百分比 + 降级说明）
   - 测试结果摘要
   - 全部 BLOCKED 和 NEEDS_HUMAN 项汇总
   - 变更文件清单（从 changeset.md 汇总）
   - 建议的后续工作
4. 确认所有状态文件已保存到磁盘

### 最终报告必须包含的章节

```markdown
# 最终状态报告

## 执行摘要
- 运行时长：{X}h {Y}m
- 目标完成度：{N}/7 目标完成，{M} 个降级
- 测试状态：{passed} passed, {failed} failed, {skipped} skipped
- BLOCKED 项：{N} 个
- NEEDS_HUMAN 项：{N} 个

## 各目标完成详情
### G1：CI/CD 基础设施
- 完成度：{百分比}%
- 降级情况：{无 / 降级为 Level X}
- 产出文件：{文件列表}
- 测试结果：{通过/失败/N/A}

### G2-G7：...

## 变更文件清单
| 文件 | 操作 | 行数变化 | 关联子任务 |
|------|------|---------|-----------|
| ... | ... | ... | ... |

## 未完成项与建议
### BLOCKED 项
- {子任务ID}：{原因} → 建议：{修复建议}

### NEEDS_HUMAN 项
- {子任务ID}：{原因} → 需要：{具体人工操作}

### 建议的后续工作
1. {高优先级后续工作}
2. {中优先级后续工作}
3. {低优先级后续工作}
```

---

## 输出规范

### 中间产物质量标准

| 产物类型 | 格式要求 | 质量门禁 |
|----------|---------|---------|
| 代码修改 | 遵循项目现有代码风格（类型注解、docstring） | 修改后测试必须通过 |
| 新增测试 | pytest 框架，test_ 开头 | 覆盖主要分支，断言明确 |
| 设计文档 | Markdown | 含问题分析 + 方案 + 接口定义 |
| 状态文件 | 按上述格式 | 每个子任务开始/结束时更新 |
| 检查点报告 | 按上述模板 | 每小时一次，不遗漏 |

### 最终交付物

运行结束时，以下文件必须存在且内容完整：

1. `.trae/agent-state/final-report.md` — 最终状态报告
2. `.trae/agent-state/task-breakdown.md` — 完整任务分解
3. `.trae/agent-state/progress.md` — 最终进度追踪
4. `.trae/agent-state/decision-log.md` — 全部决策记录
5. `.trae/agent-state/error-log.md` — 全部错误记录
6. `.trae/agent-state/changeset.md` — 完整变更日志
7. `.trae/agent-state/checkpoint-reports/` — 全部检查点报告
8. 代码变更已在 develop 分支的工作树中（未提交，等待人工 review）

### 代码变更规范

- 所有代码变更在 develop 分支的工作树中进行，不创建新分支，不执行 git commit
- 每个文件的修改理由必须能在 changeset.md 中追溯到具体子任务 ID
- 不得删除已有文件
- 单次文件修改不超过 500 行新增代码（大改动拆分为多次）

---

## 资源约束

### 可调用工具

- 文件读写：Read, Write, Edit, Glob, Grep
- 命令执行：pytest, python, pip, git（仅 status/diff/log/checkout，不 commit/push/merge）
- 代码搜索：Grep, Glob
- 子代理：最多同时 3 个 Explore 子代理用于代码审计

### 时间预算

| 活动 | 预算 | 占比 |
|------|------|------|
| 代码实现 | 4.5h | 56% |
| 测试编写与执行 | 1.5h | 19% |
| 设计与文档 | 1h | 12.5% |
| 检查点与状态管理 | 0.5h | 6% |
| 错误处理与重试 | 0.5h | 6% |

### 行为边界

**绝对禁止**：
- 不得修改 SOUL.md 人设契约的核心段落
- 不得更改 AGP 2019 共识阈值（54/70/180/250 mg/dL）
- 不得删除或跳过已有回归测试
- 不得在未运行测试的情况下声明任务完成
- 不得执行 git commit/push/merge
- 不得访问真实患者数据
- 不得修改 develop 分支以外的分支
- 不得修改 .git/ 目录下的任何文件
- 不得安装新的系统级软件包

**需要记录**：
- 引入新的第三方依赖
- 修改公共 API 接口
- 降级目标完成标准
- 调整子任务执行顺序

**自由裁量**：
- 选择实现方案（在有明确偏好时选保守方案）
- 代码风格细节（在遵循项目整体风格的前提下）
- 测试用例的详细设计
- 文档的具体措辞

---

## 开始

现在执行以下步骤：

1. `git checkout develop` 切换到工作分支
2. 确认 `tests/` 目录存在
3. 运行 `python -m pytest tests/ -q --timeout=180` 确认测试基线全绿
4. 创建 `.trae/agent-state/` 目录
5. 创建 `.trae/agent-state/task-breakdown.md`，完成 7 个目标的子任务分解
6. 开始执行 G5-S1（第一个子任务）
