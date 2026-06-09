# Narrative Contracts: Companion Narrative + Negotiated Interaction (F4)

**Date**: 2026-06-09
**Feature**: specs/003-companion-narrative/

## Audience Narrative Contracts

### SELF Audience Contract

**Language**: Conversational Chinese,口语化, like texting a friend.
**Length**: Daily card ≤50 chars, weekly pattern ≤100 chars, general ≤80 chars.
**Tone**: Warm, calm, non-judgmental, non-directive.
**Forbidden patterns**: "你应该", "你必须", "你需要", "建议你", "警告", "警报", "危险", "TIR 只有 X%", "经分析发现", "研究表明", "数据证明".

**Metric translation**:
| Clinical Term | Life-Language Replacement |
|--------------|--------------------------|
| TIR X% | "大部分时间都在范围里" / "在范围里的时间大概X成" |
| TAR X% | "偏高的时候多一些" / "偶尔会偏高" |
| TBR X% | "有几段偏低" / "偶尔会偏低" |
| MBG X mg/dL | "平均大约X" (only if user asks for numbers) |
| CV X% | "波动幅度" (avoid unless user asks) |
| GMI X | Omit in default narrative |

### CLINICIAN Audience Contract

**Language**: Clinical Chinese, structured.
**Length**: No soft limit; structured data preferred.
**Tone**: Professional, precise, data-first.
**Format**: Key=value pairs, percentages, timestamps.

### FAMILY Audience Contract

**Language**: Simplest possible Chinese.
**Length**: Daily card ≤1 sentence.
**Tone**: Reassuring, clear, no anxiety.
**Forbidden**: Any clinical terminology, any numbers, any technical terms.

**Daily card examples**:
| Scenario | FAMILY Output |
|----------|--------------|
| All normal | "今天整体平稳，没有什么需要特别担心的。" |
| Some highs | "今天有一点小波动，不过整体还好。" |
| Some lows | "今天有一小段偏低，不过已经记录下来了。" |
| No data | "今天记录不太完整，先别急着往异常上想。" |

## Hypothesis Narrative Contracts

### CANDIDATE State
- Must use hedged language: "看起来", "可能", "似乎"
- Must include invitation to verify: "要不要…多留意一下？"
- Must NOT imply certainty or conclusion
- Max length: 80 chars

### OBSERVING State
- Must reference evidence count naturally: "过去几次里有N次类似"
- Must suggest continued observation: "建议继续记录"
- Must NOT assert causation
- Max length: 80 chars

### STABLE State
- May reference confirmed pattern: "在你的记录中，这个模式比较常见"
- Must still use hedged framing (not "证明" or "确认")
- Max length: 80 chars

### ARCHIVED State
- Must frame as natural conclusion, not failure: "最近不明显"
- Must offer reactivation path: "先降级，之后如果又出现可以再看"
- Max length: 80 chars

## Escalation Concern Contracts

### NORMAL (day 0-2)
- Standard data attribution: "今天午餐后有个小高峰，可能跟外食有关。"
- No personal concern language

### CONCERN (day 3-4)
- Shift from data to person: "最近几天都有点波动，你还好吗？"
- Must feel like care, not monitoring
- No alarm words: "警告", "注意", "异常"

### EXTERNAL_SUPPORT (day 5+)
- Gentle suggestion: "要不要下次复诊时跟医生聊聊？"
- Offer to help: "我可以帮你整理一份报告。"
- Must NOT sound like medical advice or urgency

### Vulnerable Population Adjustment
- Same escalation levels but extra-gentle language
- Day 3 concern: "最近几天数据有点变化，想问问你感觉怎么样？"
- Day 5 support: "这段时间波动比较大，要是方便的话，可以跟医生说说。我帮你整理数据。"
