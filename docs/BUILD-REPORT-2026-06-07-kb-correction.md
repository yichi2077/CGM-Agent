# 构建说明：权威 KB 修正轮（防稀释护栏 + Hermes 重抽）

- **日期**：2026-06-07
- **范围**：对 `BUILD-REPORT-2026-06-06-auto-kb-pipeline.md` 那一轮的审查修正
- **关联决策**：[D042](DECISION_LOG.md#d042--权威-kb-修正tier-检索护栏--真-ci-门禁--hermes-重抽修正-d041-落地偏差)（修正 [D041](DECISION_LOG.md#d041--权威-kb-采用-hermes-委托抽取与机器校验入库)）
- **审查依据**：`AUDIT-2026-06-06-蓝图实现差异审计.md` 之后的 KB 流水线专项审查

---

## 1. 为什么要修正（审查结论）

06-06 轮把生产 `authoritative_kb.json` 从 6 张人工种子卡扩到 335 张，但合入的 329 张 auto 卡是用 `--engine sentence`（关键词+数字的句子切割）产出的草稿，**不是** D041 规定的 Hermes 抽取。实测后果是**净回归**：

| 指标 | 仅 6 种子卡 | merge 329 sentence 卡后 |
|------|-----------|------------------------|
| `eval-rag` hit@3 | **100% (32/32)** | **84.4% (27/32)** |

5 个 miss 全是人工种子卡被 auto 碎片挤出 top-3，其中一例把**孕期**卡顶给成人 TAR 查询（阈值不同）——临床稀释。auto 卡内容多为论文标题、`"5 (ABSTRACT)…"` 页码片段、期刊报头/DOI，以及 CDS 中文 PDF 的**乱码**（PUA 字形 `U+1001B0`）。质量门当时只拒了 6/335（1.8%）。此外 `eval-rag` 永远 `return 0`（CI 不拦回归）、`citation_guard` 被错误地用在用户 query 上且用子串匹配（几乎永不触发）。

---

## 2. 做了什么（按阶段）

### Phase 0 — 生产库止血
- 生产 `authoritative_kb.json` 回到 **6 张人工种子卡**（`tier=curated`，`verified=false`），`kb_version=kb-2026-06-draft`。
- 注：`git HEAD` 的该文件是更早的 3-doc 旧版（`authoritative-kb-2026-06-01`）；6 张种子卡本身是未提交工作树内容，已从会话内备份恢复。
- 验证：`kb-validate` 通过；`eval-rag` hit@3 回到 **100%**。

### Phase 1 — 检索层永久护栏 + 工程门禁
- **卡片 `tier` 字段**（`curated`/`auto`）：`ClaimCard`、`merge`（机器入库卡强制 `tier=auto`）、`AuthoritativeDocument`、assembler 透传。
- **可信优先检索**（`services/rag/authoritative.py` `search()`）：取更深候选池 → `verified or tier==curated` 的可信卡稳定排在 auto 卡之前 → auto 卡需满足查询词重叠下限 → 截断 top_k。**机制说明**：权威轨是 sparse-only，RRF 退化为按 rank 的分数（`1/(60+rank)`），不是相关度量级，故不用绝对分数下限，改用「可信优先 + 词重叠门」。
- **CI 真门禁**：`eval-rag --min-hit3`（默认 0.95，低于即非零退出）；`scripts/eval_rag_hit3.py` 与 `.github/workflows/kb-quality.yml` 同步。
- **citation_guard 修正**：`assert_authoritative_quotes` 改为对**生成文本**做整数 token 精确匹配（修掉 `"5" in "2025"` 子串误配）；查询侧覆盖信号拆为 `query_number_coverage`（仅检索提示，非防幻觉）；生成层防幻觉规则写入 `skills/cgm-safety/SKILL.md`。

### Phase 2 — 质量门加固 + Hermes 重抽替换
- **质量门**（`knowledge/ingest/quality.py`）新增拒绝：乱码/PUA 字形、报头/DOI/ISSN/`Received:`等元数据行、页码前缀标题片段。
- **card_id 命名空间化**（`hermes_extractor.py`）：逐页 Hermes 调用无跨页记忆，模型易逐页重用 id；改为按 `auto-{stem}-p{page}-{slug}` 保证全局唯一，避免去重静默丢卡。
- **Hermes 重抽**：对 priority-1 PDF 运行 `kb-ingest-llm --engine hermes`；CDS（中文乱码）/AACE（扫描件）走 `--mode vision`（渲染 PNG + `--image`，需 venv 装 `pymupdf`/`pdfplumber`），其余走 `--mode auto`。产出 `tier=auto, verified=false` 卡，`kb-merge` 合入 `kb-2026-06-auto-v2`。

> **单页验证样例**（`tir-consensus-summary` p.1，Hermes text 引擎）：产出 9 张原子卡，含 `Time in range (TIR: 70-180 mg/dL) target is >70%` / `TBR (<54 mg/dL) target is <1%` / `%CV target ≤36%`，**且 `claim_zh` 为真翻译**（如「目标范围内时间(TIR: 70-180 mg/dL)目标为>70%」），与 sentence 引擎的占位中文形成对比。

---

## 3. 重抽与最终结果（Phase 2.2/2.3 + Phase 3）

**Hermes 重抽（7 篇 priority-1 PDF，CDS/AACE 走 vision）每篇接受/拒绝：**

| PDF | 模式 | accepted | rejected |
|-----|------|---------:|---------:|
| battelino-2019-tir | auto（含 vision 表/图页） | 82 | 15 |
| ada-2025-updates | auto（多为 vision） | 125 | 12 |
| cds-2024-guideline | **vision**（中文乱码） | **252** | 91 |
| ispad-2024-glycemic | auto/hybrid | 43 | 7 |
| ishne-2023-agp | auto/hybrid | 48 | 14 |
| tir-consensus-summary | auto | 12 | 5 |
| aace-2024-hypo | vision（扫描件） | 10 | 0 |
| **合计** | | **572** | **144** |

- 质量门**拒绝率 20%（144/716）**，对比旧 sentence 引擎的 1.8%（6/335）——这是一个真正起作用的过滤器。
- **CDS 关键修复**：旧 text 抽取产出 78 张乱码卡（PUA 字形）；vision 读渲染页图后产出 **252 张干净双语原子卡**（如「α-糖苷酶抑制剂为老年 T2DM 二线」「CKD G4 期 eGFR 15–29 复查频率」「Group 1 老年空腹目标 5.0–8.3 mmol/L」「ASCVD 老年首选 GLP-1RA/SGLT2i」），中文乱码问题彻底解决。
- **AACE**：旧 text 抽取 0 卡（扫描件无文本）；vision 产出 10 张 TIR/TBR/TAR 分级目标卡。
- 抽取过程中少数页 Hermes 返回非 JSON 数组（解析失败）按 0 卡优雅跳过，不污染库。

**最终生产 KB（`kb-2026-06-auto-v2`）**：**578 张卡 = 6 张 curated 种子 + 572 张 auto**，全部 `verified=false`；`kb-validate` 通过；6 张种子 id 全部保留。

**评测（`eval-rag --min-hit3 0.95`）：**

| KB / 查询集 | hit@3 |
|-------------|-------|
| 旧版：335 卡（6 种子 + 329 sentence），32 条种子查询 | 84.4%（回归） |
| 修正后：578 卡 + tier 护栏，32 条种子查询 | 96.9%（唯一 miss 是 AGP 查询命中了更优的新 AGP 卡，已修正期望值） |
| 修正后：578 卡 + tier 护栏，**43 条（32 种子 + 11 新卡）** | **100%（43/43），通过门禁** |

> 关键结论：加入 572 张 auto 卡后种子检索**不再被稀释**（护栏生效），且新内容**可被检索到**（11 条新卡查询全中）。

---

## 4. 测试

- 全量单测：**248 passed**（新增 tier/可信优先/词重叠、`--min-hit3` 门禁、精确数字匹配、tier=auto merge、质量门乱码/元数据/标题、card_id 命名空间化）。
- 命令：`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`

---

## 5. 刻意未改 / 边界

- 架构不变：Claim Card + BM25 双轨 + `verified=false` 默认 + 人工签核外置。
- `verified=true` 批量临床签核仍需外部人力；auto 卡一律 `verified=false`。
- 前次审计（`AUDIT-2026-06-06-…`）的其它工作线（AGP 可视化、MAGE/MODD、分层推送、L2→USER.md 生产化、Dexcom 原始报文）不在本轮。

---

## 6. 仓库卫生提醒（F9/F10）

06-06 build-report 仅描述 KB 流水线，但当时工作树还含一大批**未文档化的记忆子系统改动**：`context.get_l0` 工具、`reports.generate` 的 `auto_ingest_memory`、`memory.correct → USER.md` 同步（`HERMES_HOME` 触发）、L0 builder、双时态/warm synthesis、assembler/provider/repository/retrieval 等。这些与本轮 KB 修正共存于同一未提交工作树，且 6 张人工种子卡与整库 KB 本身都尚未入 git。

**建议**：提交时按主题分离——(a) KB 流水线 + 本轮修正；(b) 记忆子系统；(c) 把当前仅存于工作树的种子 KB 正式入库——以便 reviewer 能准确判断每次将要发布的内容。本轮不重构记忆子系统。
