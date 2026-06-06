# CGM-Agent 下一阶段状态报告

- **日期**：2026-06-06
- **阶段**：G0-G8 能力层完成后，进入记忆/RAG 产品闭环建设
- **关联**：[ADR-0001](adr/ADR-0001-memory-and-knowledge-architecture.md)、[DECISION_LOG](DECISION_LOG.md)、[MEM-ARCH](MEM-ARCH.md)、[REFACTOR-PLAN-2026-06-06](REFACTOR-PLAN-2026-06-06.md)

## 一句话摘要

当前代码已经具备 CGM 数据、分析、报告、安全、记忆和双轨 RAG 的主体能力；下一阶段重点不是继续横向加功能，而是闭合「报告候选入队→用户确认→L1→L2/L3→Hermes USER.md」记忆链路，并把权威 KB 从 6 张草稿卡扩展为可核验、可评测的 claim card 库。

## 当前事实

- Hermes venv 下全量单元测试通过：222 tests OK。
- 权威 KB 当前为 `kb-2026-06-draft`，仅 6 张 card，`verified=true` 为 0。
- `L0Context` 已有确定性 Builder，并通过 `context.get_l0` 工具和 `context-build` CLI 暴露。
- 报告能产出 `g8_memory_candidates`，工具执行路径已可自动入队，并保留用户确认闸门。
- L2 profile 已可从 SQLite 单向同步到 Hermes `USER.md` 受管 CGM 段。

## 本阶段工程决断

- 权威医学轨保持小库策略：BM25 + tags/synonyms/population，默认不加载 embedding。
- 个人记忆轨采用分层策略：L2/L3 Hot SQL 全量注入；L1 随规模增长启用 hybrid/dense。
- 报告候选自动进入 memory candidate queue，但仍由确认闸门保护长期记忆。
- L0 工作记忆必须由确定性 Builder 生成，LLM 不直接读取原始无界时序。
- L2 以 SQLite 为 source of truth 单向导出到 Hermes `USER.md` 受管 CGM 段。
- PDF ingest 只生成候选卡，生产 KB 仍需人工/临床签核。

## 优先级

1. 文档治理与决策日志对齐（D036-D040）。
2. 报告候选自动入队，闭合 G7→G8。
3. 分轨 Retriever 工厂，确保权威轨与个人轨不共享 dense 策略。
4. L0 Builder 和 `context.get_l0` 工具。
5. PDF→候选 claim card 半自动管线与 RAG eval。
6. L2→USER.md 单向同步。
7. Hermes 安装、插件清单、README 与 smoke test 固化。

## 风险

- 权威 KB 扩容依赖人工/临床核验，不能仅靠代码完成可信度。
- 个人 L1 hybrid 检索引入 optional ML 依赖，需要保持默认路径离线可跑。
- USER.md 同步必须只改受管 CGM 段，避免覆盖用户手写记忆。
