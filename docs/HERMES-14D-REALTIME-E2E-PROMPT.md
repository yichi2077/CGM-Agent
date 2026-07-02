# Hermes Prompt - 14-Day Real-Time CGM Virtual E2E Simulation

Use the prompt below in Hermes with the CGM toolset enabled.

Recommended launch:

```powershell
hermes --provider deepseek --model deepseek-v4-flash -t cgm
```

Prompt:

```text
你现在是 CGM Agent 项目的端到端实机模拟测试执行者。请完整理解并执行本次 14 天真实时间虚拟 CGM 仿真测试。

项目位置：
E:\字幕组测试\CGM-Agent\hermes-cgm-agent-latest

本次测试的目标：
使用项目内的虚拟 CGM 数据源，在真实 14 天时间窗口内持续模拟设备上传、轮询入库、数据分析、记忆/情景构建、日报/周报/医生报告、推送调度和交付路径，验证 CGM Agent 在 Hermes 中是否能作为完整产品链路运行。

固定测试配置：
1. Hermes provider/model：provider=deepseek，model=deepseek-v4-flash。
2. 数据集：examples/cgm_test_dataset/cgm_14d_1min.csv。
3. 数据集 SHA256：7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc。
4. 数据集来源：修正后的 cgm_14d_1min_v2.csv，已替换为默认虚拟源。
5. Hermes 可见数据库：C:\Users\postgres\AppData\Local\hermes\cgm-agent\app.db。
6. 本次 v2 测试必须使用新的隔离身份：
   user_id=demo-prediabetes-14d-v2
   source=virtual:aidex-v2
7. Email 不是本轮测试必需项。交付闭环使用 local_file 或 webhook；SMTP email 只作为可选通道记录。
8. KB 临床核验暂不作为阻塞项。允许使用未验证 KB 卡作为测试材料，但所有涉及 KB 的结论必须标记“未临床核验，仅用于测试”。

开始前必须完成的核实：
1. 确认当前工作区和文档：
   - docs/PRETEST-FREEZE-2026-07-02.md
   - examples/cgm_test_dataset/manifest.json
   - examples/cgm_test_dataset/README.md
2. 确认 Hermes 已加载 18 个 cgm_* tools。
3. 查询当前 DB baseline：
   - glucose_points 总数
   - demo-prediabetes-14d-v2 / virtual:aidex-v2 是否已有旧数据
   - reports、memory_candidates、push_events、audit_logs 的数量
4. 如果 demo-prediabetes-14d-v2 已有旧数据，不要静默覆盖；先记录冲突，并改用新的 run-specific user_id/source 或请求人工确认。

仿真执行要求：
1. 启动虚拟 CGM feed，读取 examples/cgm_test_dataset/cgm_14d_1min.csv。
2. 以真实 5 分钟节奏进行 14 天轮询：
   - 每次只推进 1 个 5 分钟点。
   - 不要用 count=12 或 interval-sec=0 代替真实 14 天测试；这些只允许作为 smoke test。
3. 建议命令形态：
   - virtual_cgm_feed.py 使用 --emit-interval-min 5。
   - auto_poll.py 使用 --count 1 --interval-min 5 --duration-hours 336。
4. 轮询必须写入 Hermes 可见 DB。
5. 测试过程中定期调用并记录：
   - cgm_timeseries_get_realtime_snapshot
   - cgm_timeseries_get_aggregate
   - cgm_context_get_l0
   - cgm_reports_generate
   - cgm_scheduling_push_tick
   - cgm_delivery_send（local_file 或 webhook）
6. 每日至少生成一次 daily report。
7. 每周至少生成一次 weekly report。
8. 到达足够数据后生成 doctor report。
9. 推送调度必须检查 silent-consent、unread badge、push_events 幂等性。

全程记录要求：
你必须维护一份测试日志，专门分成两个重点部分：

A. 发生的问题
记录每一个实际发生的问题，包括：
- 时间点
- 触发动作
- 使用的工具或命令
- 错误原文
- 影响范围
- 临时绕过方式
- 是否需要代码修复
- 是否已经复现

B. 还没有研究清晰或可能存在问题的地方
记录所有不确定项，包括：
- 数据是否足够支撑结论
- 指标是否和预期一致
- 报告叙事是否有风格漂移
- 未验证 KB 卡是否影响医学表述
- 记忆/假设是否出现错误归因
- push/delivery 是否存在幂等性或重复投递风险
- DB 中是否有旧数据污染
- 任何工具返回 ok 但语义上可疑的情况

报告原则：
1. 不要只报“测试通过/失败”。必须解释证据。
2. 每个阶段都要给出 DB 计数、最新时间戳、关键工具返回状态。
3. 对所有未验证 KB 内容保持明确标注。
4. 对所有医学相关输出使用 CGM Agent 的“知情陪伴者”风格：不诊断、不替代医生、基于用户自身数据、明确不确定性。
5. 如果测试中断，必须记录中断前最后一个成功点、最后一个 DB 最新 timestamp、下一步恢复命令。

最终交付物：
1. 14 天运行日志。
2. 每日 checkpoint 摘要。
3. 问题清单。
4. 未研究清楚/潜在风险清单。
5. 工具调用证据摘要。
6. DB 最终状态摘要。
7. 报告样例路径或 report_id。
8. 是否可以进入下一轮真实设备/真实用户数据测试的判断。
```
