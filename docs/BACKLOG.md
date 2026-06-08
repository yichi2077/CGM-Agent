# CGM-Agent Feature Backlog（单一事实源）

- **日期**：2026-06-08
- **作用**：项目后续开发的唯一 backlog 事实源（宪法 §VI）。所有 ad-hoc 计划（`docs/FIX-PLAN-*`）以本文件为准归并，归并后退役。
- **权威约束**：[宪法](../.specify/memory/constitution.md)（7 原则）· [ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md) · [DECISION_LOG](DECISION_LOG.md)
- **状态图例**：`OPEN` 未动 · `PARTIAL` 有骨架待完善 · `CORE-DONE` 核心已完成 · `VERIFY` 需先排查再定 · `BLOCKED/FROZEN` 外部依赖或主动冻结 · `DEFERRED` 暂缓
- **来源**：FIX-PLAN-2026-06-07（Apollo）+ Caesar 审查 + Damocles 审计 + PRD-SUPPLEMENT + ADR-0001 backlog + AUDIT-2026-06-07 残留 + Dexcom 集成备忘。状态均已对当前 HEAD 代码核实。

---

## 1. Feature 合并视图（14+ 条目 → 7 个 feature）

| Feature | 合并条目 | 主要文件 | 走 speckit？ | 宪法 |
|---|---|---|---|---|
| **F1 Hermes 运行可用性** | A1 A2 A3 A5（+A4 A6 排查） | `config.py` `executor.py` `registry.py` `cgm/__init__.py` | ✅ 是 | V, VII |
| **F2 数据来源方向** | E2（+E1 冻结） | ADR + 决策 | 否（出 ADR） | — |
| **F3 医学安全硬化** | B1 B2 B3 | `rag/` `safety/` `executor` | ✅ 是 | I |
| **F4 陪伴者叙事 + 协商交互** | C1 C2 C3 | `reports/builder.py` | ✅ 是 | IV |
| **F5 主动推送 + 投递闭环** | D1 D2 | `scheduling/` `executor`(delivery) | ✅ 是 | VII |
| **F6 工程债** | G1 G2 G3 G4 | 各处 | 否（checklist） | V, VI |
| **F7 分析深度** | AN1 AN2 | `analytics/` | 暂缓 | I |

---

## 2. 条目清单（细粒度真相，状态已核实）

### A. 阻断级 —— 让产品在 Hermes 里真正可用（→ F1）
| # | 条目 | 状态 | 核实结论 / 说明 |
|---|---|---|---|
| A1 | DB 路径统一 + Fernet key 跟随 | `OPEN` | `config.py:92` 仍硬编码 `DEFAULT_DB_PATH`，绕过 `resolve_database_path()` → CLI 写 `.runtime/`、Hermes 读 `~/.hermes/cgm-agent/` → 对话看不到数据。含旧数据迁移脚本（DB+key 同迁，Damocles W1）+ CLI 迁移提示（W4） |
| A2 | 工具 schema 展平 + 技术字段强制覆盖 | `OPEN` | `events.create` 用悬空 `$ref:#/$defs/UserEvent`（无 `$defs`）；`timeseries`/`aggregate` 同样悬空。展平为内联 schema + executor 强制覆盖 `event_id/created_by/user_confirmed`（W2 防绕过） |
| A3 | memory.confirm/correct 工具可达性 | `OPEN` | `cgm/__init__.py:18-19` 仍 `continue` 排除。先诊断 Hermes 是否经 provider 暴露，再决定降级注册（防双注册 W3） |
| A4 | seed-demo 离线 / 首次不卡顿 | `VERIFY` | semantic 默认已关；需先定位 60s 卡点（可能不在 retrieval.py），再决定是否 `HF_HUB_OFFLINE=1` |
| A5 | 默认空库首次体验 | `OPEN` | `hermes-install --seed-demo` + 空库友好提示（prefetch/system_prompt_block 注入） |
| A6 | occurred_at 跨日不坍缩 | `VERIFY` | builder 已填 `ts_start`；补 builder→review→L1 全链路测试定位是否在 `_accept` 回退 now |

### B. 医学安全与可信（→ F3）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| B1 | verify_quotes 硬校验 | `OPEN` | 当前仅 SKILL 软约束 + 工具存在；把"医学数字必须有来源"做成代码硬门 |
| B2 | KB 临床签核流程 + `kb.approve` | `OPEN`（外部依赖） | 578 卡全 `verified=false`；建签核流程，核心 ~100 卡先 `verified=true` |
| B3 | 红区恢复二次确认 / 三区规则补全 | `VERIFY` | router 三区已有；核实"红区后 2h 恢复需二次确认"等 PRD §2.3 细则是否落地 |

### C. 产品叙事与交互（→ F4）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| C1 | 报告中文叙事层完善 | `PARTIAL` | builder 已有 `audience` + `_daily_card_section` 骨架；补 TIR→生活语言、周报/医生版/家属版叙事差异 |
| C2 | 协商式假设验证话术 | `OPEN` | 四状态机已有；接入 candidate/observing/stable/invalid 话术 + 邀请验证流程（PRD §2.4） |
| C3 | 连续异常渐进关心 + 脆弱人群更早干预 | `VERIFY` | SOUL 定义了第1/3/5天升级与特殊人群策略；核实是否在调度/报告里实现 |

### D. 推送与投递闭环（→ F5）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| D1 | push-tick 暴露为 Hermes tool + cron 注册 | `OPEN` | 调度策略/静默即认可核心已完成（`scheduling/scheduler.py`），但 `push-tick` 仅 CLI、未在 registry/plugin.yaml；包成工具并接 Hermes cron |
| D2 | delivery webhook/email 实现 | `PARTIAL` | 仅 `local_file` 完整，email/webhook 记为 `queued`；先做 webhook HTTP POST |

### E. 数据来源（→ F2，战略决策）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| E1 | Dexcom 集成解阻 | `FROZEN` | US-portal app vs AU/OUS data account，需 developer.dexcom.eu。**项目决定：Dexcom API 将弃用、非第一级交付 → 冻结，不投入** |
| E2 | 替代数据接入策略 | `需决策` | CSV 导入已有；是否接 Libre/Nightscout？定义 MVP 数据入口。**出 ADR** |

### F. 分析深度（→ F7，暂缓）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| AN1 | 高级变异性指标 MAGE/MODD/CONGA | `DEFERRED` | 代码中无；AUDIT 列为"横向铺功能，本阶段不做" |
| AN2 | AGP 百分位可视化 | `DEFERRED` | 同上，需授权 |

### G. 工程健康（→ F6，持续技术债）
| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| G1 | 大模块拆分 | `OPEN` | `cli.py`(1252)/`executor.py`(1010)/`builder.py`(980)；拆 executor 是后续并行的使能器 |
| G2 | 双插件路径一致性测试 + `_EXECUTOR_CACHE` 失效 | `OPEN` | Caesar 遗漏 B/C；加守卫测试 |
| G3 | 文档计数漂移收敛 | `OPEN` | 旧报告 183/196/222 等过期计数 |
| G4 | 未入库 FIX-PLAN → 收编后退役 | `OPEN` | 4 份竞争性计划文档，本 BACKLOG 落地后删除（宪法 §VI） |

---

## 3. 依赖与并行约束

**依赖脊柱（必须先串行）**
- **F1（尤其 A1 数据库路径）是几乎一切的前置** —— 路径不统一，别的 feature 改完在真实 Hermes 里无法验证。
- **F2（数据来源）决定有没有数据可喂** —— 不先定，F1 修完也无真实数据。

**文件争用热点（限制并行）**
- `executor.py`（1010 行）被 F1/F3/F5 同时碰；`registry.py` 被 F1/F5 碰。
- → 多代理并行改同一大文件 = merge 冲突。**G1 先拆 executor 是并行的使能器。**

**可安全并行（目录基本不相交）**
- F3（`rag/`+`safety/`） · F4（`reports/`） · F5（`scheduling/`+delivery）—— 可各开 worktree 子代理并行。

---

## 4. 执行计划（混合模式 · 已选定）

| 阶段 | 内容 | 方式 | 并行度 |
|---|---|---|---|
| **Stage 0** | F2 数据来源 ADR + G1 拆 executor | 直接做（非 speckit），人审 | 串行 |
| **Stage 1** | F1 Hermes 运行可用性（A1+A2+A3+A5） | 完整 speckit，人审 review gate | 串行（脊柱） |
| **Stage 2** | F3 / F4 / F5 | speckit + `isolation: worktree` 子代理 | **并行**，完成后依次合并 |
| **持续** | F6 其余（G2/G3/G4）· F7 暂缓 | checklist / 暂缓 | 机会并行 |

**子代理边界（医疗项目克制）**
- ✅ 可无人值守并行：测试、F4 文案、F5 webhook、F6 重构、VERIFY 排查。
- ⚠️ 必须人审、不盲跑：双轨隔离/安全闸/医学数值（A3、F3）—— 宪法要求每个 plan 过 Constitution Check + 守卫测试。
- ❌ 真正瓶颈是 review gate，不是吞吐；对医疗产品这是特性。

**预期墙钟**：纯串行 ~6 轮 → 混合 ~3 轮（2 串 + 1 波并行）。

---

## 5. 每个 feature 的 speckit 起手命令（备查）

```
F1: /speckit-specify 修复 CLI 与 Hermes 插件的 DB 路径分裂 + events.create schema 展平 + memory 工具可达性
F3: /speckit-specify verify_quotes 代码硬校验 + KB 临床签核流程
F4: /speckit-specify 报告中文叙事层 + 协商式假设验证话术
F5: /speckit-specify push-tick 工具化+cron + delivery webhook 闭环
```
F2 走 ADR（非 speckit）；F6/F7 走 checklist 或暂缓。
