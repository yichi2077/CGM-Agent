# Memory Efficacy Report (D053)

- Queries: **20** · Seed corpus rows: **16**
- Mean recall **with** memory: **1.0**
- Mean recall **without** memory (empty store): **0.0**
- **Delta (evidence that memory helps): 1.0**

Method: deterministic context recall — fraction of each query's `expected_terms` present in `CGMMemoryProvider.prefetch(query)`, seeded store vs empty store. No LLM (v1); answer-quality grading is a known gap.

| Query | Layer | recall (with) | recall (without) |
|---|---|---|---|
| 昨晚睡觉时又低了 | L1 | 1.0 | 0.0 |
| 火锅后血糖飙升 | L1 | 1.0 | 0.0 |
| 最近午餐后运动有没有帮助 | L1 | 1.0 | 0.0 |
| 我是不是对面条比较敏感 | L2 | 1.0 | 0.0 |
| 散步对血糖有什么作用 | L2 | 1.0 | 0.0 |
| 早餐后血糖稳不稳 | L2 | 1.0 | 0.0 |
| 空腹喝咖啡会怎样 | L2 | 1.0 | 0.0 |
| 没睡好会不会低血糖 | L2 | 1.0 | 0.0 |
| 周末血糖是不是更乱 | L2 | 1.0 | 0.0 |
| 白米饭和糙米哪个升糖高 | L3 | 1.0 | 0.0 |
| 晚上散步对隔夜血糖好吗 | L3 | 1.0 | 0.0 |
| 高纤维早餐有用吗 | L3 | 1.0 | 0.0 |
| 熬夜影响第二天空腹血糖吗 | L3 | 1.0 | 0.0 |
| 这周整体怎么样 | warm | 1.0 | 0.0 |
| 晚餐后血糖高不高 | warm | 1.0 | 0.0 |
| 我最近有夜间低血糖吗 | L1 | 1.0 | 0.0 |
| 压力大的时候血糖如何 | L1 | 1.0 | 0.0 |
| 面条午餐后血糖怎样 | L1 | 1.0 | 0.0 |
| 运动后血糖会降吗 | L2 | 1.0 | 0.0 |
| 我的血糖有什么个人规律 | mixed | 1.0 | 0.0 |
