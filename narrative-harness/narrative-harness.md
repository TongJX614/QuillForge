# Narrative Generation Harness — 技术规格文档

## 概述

Narrative Generation Harness 是一个结构化的 AI 文本生成 Pipeline，用于根据世界线大纲和角色卡自动生成互动叙事内容。该系统借鉴 Harness.io 的 Pipeline 设计理念，将叙事生成拆解为多个阶段（Stage），每个阶段有明确的输入、输出和验证门控（Validation Gate）。

---

## Pipeline 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Narrative Generation Pipeline                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│  │ Stage 1  │──▶│ Stage 2  │──▶│ Stage 3  │──▶│ Stage 4  │   │
│  │ Context  │   │Narrative │   │Dialogue  │   │ Choice   │   │
│  │ Assembly │   │Anchoring │   │Generation│   │Branching │   │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘   │
│                                                                 │
│  ═══════════════════════════════════════════════════════════     │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Stage 5: Validation Gate                     │  │
│  │  ☐ 对话轮数 3-5   ☐ 角色一致性   ☐ 主线符合度            │  │
│  │  ☐ 选择合理性     ☐ Schema 合规                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ★ 验证失败 → 自动回退到 Stage 2/3 重新生成（最多 2 次）       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 阶段定义

### Stage 1: Context Assembly（上下文组装）

**目的：** 将离散的输入数据合并为结构化的场景上下文，确定当前世界线位置。

**输入：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `currentScene` | string | 是 | 当前场景描述 |
| `characters` | Character[] | 是 | 参与角色列表（至少 1 个） |
| `worldline` | string | 是 | 世界线大纲，节点用 `→` 分隔 |
| `playerChoice` | string | 否 | 玩家在本场景的选择（首次调用为空） |
| `previousContext` | object | 否 | 上一轮生成的完整输出（用于续写） |

**Character 对象结构：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 角色姓名 |
| `role` | string | 是 | 角色身份/职业 |
| `personality` | string | 是 | 性格特征关键词 |
| `speakingStyle` | string | 否 | 说话风格描述（如"简短有力"、"学术腔"） |
| `background` | string | 否 | 背景故事摘要 |

**处理逻辑：**

1. 解析 `worldline`，识别所有节点，判断 `currentScene` 对应的当前节点
2. 标记前一节点（用于上下文衔接）和后续节点（用于引导叙事方向）
3. 若有 `playerChoice`，将其注入场景上下文，标记为"已生效决策"
4. 若有 `previousContext`，提取末尾状态作为本场景的起始状态

**输出：** `AssembledContext` 对象

```json
{
  "currentNode": "危机",
  "nodeIndex": 2,
  "totalNodes": 4,
  "previousNode": "旅行",
  "nextNode": "到达",
  "worldlineProgress": 0.5,
  "sceneDescription": "太空旅行中，飞船遭遇陨石流",
  "activeCharacters": [
    {
      "name": "林晗",
      "role": "物理学家",
      "personality": "冷静理性",
      "speakingStyle": "学术腔，喜欢用数据说话",
      "mood": "警觉"
    }
  ],
  "playerDecision": "选择左转躲避（非正面硬扛）",
  "tension": "high",
  "narrativeDirection": "危机应对 → 转折"
}
```

---

### Stage 2: Narrative Anchoring（叙事锚定）

**目的：** 基于组装好的上下文，生成场景描写/旁白，为后续对话奠定基调。

**输入：** `AssembledContext`

**Prompt 策略：**

- 系统提示词注入叙事风格规范（见 `prompts/system_prompt.md`）
- 用户提示词注入完整 `AssembledContext`
- 要求生成 100-300 字的场景描写
- 必须体现世界线当前位置的叙事张力
- 若存在 `playerDecision`，描写需反映该选择的即时影响

**输出：** `narration` 字符串

**验证：**
- 字数在 50-500 字之间
- 不包含角色对话（对话由 Stage 3 生成）
- 包含场景环境描写

---

### Stage 3: Dialogue Generation（对话生成）

**目的：** 生成 3-5 轮符合角色性格的对话。

**输入：** `AssembledContext` + `narration`

**Prompt 策略：**

- 注入每个角色的性格标签和说话风格
- 要求对话推动情节向世界线下一节点发展
- 对话需体现角色间的性格冲突或协作
- 若存在 `playerDecision`，对话中需体现角色对该决策的反应

**对话生成规则：**

1. 每轮对话必须包含 `speaker` 和 `text`
2. 同一角色不连续发言超过 2 轮（除非有叙事需要）
3. 对话长度：短句 5-15 字（紧张场景），长句 15-40 字（分析/讨论场景）
4. 必须体现性格标签：冷静理性的角色不会大喊大叫，果断务实的角色不会犹豫不决
5. 最后一轮对话应自然过渡到"决策点"

**输出：** `dialogues` 数组

```json
[
  { "speaker": "赵远山", "text": "左转！现在！", "emotion": "urgent" },
  { "speaker": "林晗", "text": "左转的话我们会偏离航线至少 3 天。燃料够吗？", "emotion": "analytical" },
  { "speaker": "赵远山", "text": "先活下来再算账。", "emotion": "decisive" },
  { "speaker": "林晗", "text": "……明白。我已经计算好偏转角度了。", "emotion": "composed" }
]
```

**验证门控：**
- ☐ 对话轮数 3-5 轮
- ☐ 每个 speaker 都存在于 `characters` 列表中
- ☐ 无同一角色连续 3 轮以上发言
- ☐ 对话中体现性格标签（通过关键词匹配 + LLM 自评）

---

### Stage 4: Choice Branching（选择分支）

**目的：** 生成 2-4 个有意义的后续选择，每个选择有明确的叙事效果。

**输入：** `AssembledContext` + `narration` + `dialogues`

**选择生成规则：**

1. 至少 2 个选择，最多 4 个
2. 每个选择必须有 `text`（选项描述）和 `effect`（预期效果）
3. 至少一个选择推进主线（向世界线下一节点发展）
4. 至少一个选择是"有风险但有回报"的路线
5. 选择之间应有实质差异，避免"换皮"选项
6. 选择文本 5-20 字，效果描述 5-15 字

**输出：** `nextChoices` 数组

```json
[
  { "text": "执行左转规避", "effect": "安全但延误 3 天", "worldlineImpact": "stay_current" },
  { "text": "正面穿越陨石流", "effect": "高风险高回报", "worldlineImpact": "advance_risky" },
  { "text": "释放诱饵无人机", "effect": "消耗资源但获取情报", "worldlineImpact": "side_branch" }
]
```

---

### Stage 5: Validation Gate（验证门控）

**目的：** 对完整输出进行质量校验，不通过则触发回退重新生成。

**验证清单：**

| 检查项 | 条件 | 严重度 |
|--------|------|--------|
| Schema 合规 | 输出符合 Output Schema | Critical |
| 对话轮数 | 3 ≤ dialogues.length ≤ 5 | High |
| 角色一致性 | 所有 speaker ∈ characters | Critical |
| 性格匹配度 | LLM 自评 ≥ 0.7 | Medium |
| 主线符合度 | 叙事方向与世界线一致 | High |
| 选择多样性 | 选项间效果描述不重复 | Medium |
| 续写连贯性 | 有 previousContext 时，内容衔接自然 | High |

**重试策略：**

- Critical 失败：立即回退到 Stage 2 重新生成
- High 失败：回退到失败阶段重新生成
- Medium 失败：记录警告，继续输出
- 最多重试 2 次，超过则输出当前最佳结果 + 警告标记

---

## 输入接口规范

### 完整请求体

```json
{
  "currentScene": "string (必填) — 当前场景描述，50-500字",
  "characters": [
    {
      "name": "string (必填) — 角色姓名",
      "role": "string (必填) — 角色身份/职业",
      "personality": "string (必填) — 性格关键词，如'冷静理性'",
      "speakingStyle": "string (可选) — 说话风格",
      "background": "string (可选) — 背景故事"
    }
  ],
  "worldline": "string (必填) — 世界线大纲，用 → 连接节点",
  "playerChoice": "string (可选) — 玩家选择",
  "previousContext": {
    "narration": "string",
    "dialogues": [],
    "nextChoices": [],
    "worldlineState": {}
  },
  "options": {
    "dialogueCount": "number (可选, 默认 3-5)",
    "narrationLength": "string (可选, 'short'|'medium'|'long', 默认 'medium')",
    "tone": "string (可选, 'serious'|'humorous'|'dark'|'hopeful', 默认自动推断)",
    "language": "string (可选, 默认 'zh-CN')"
  }
}
```

### 最小请求示例

```json
{
  "currentScene": "太空旅行中，飞船遭遇陨石流",
  "characters": [
    { "name": "林晗", "role": "物理学家", "personality": "冷静理性" },
    { "name": "赵远山", "role": "指挥官", "personality": "果断务实" }
  ],
  "worldline": "出发→旅行→危机→到达"
}
```

---

## 输出接口规范

### 完整响应体

```json
{
  "success": true,
  "data": {
    "narration": "string — 场景描写/旁白，50-500字",
    "dialogues": [
      {
        "speaker": "string — 角色姓名",
        "text": "string — 对话内容",
        "emotion": "string — 情绪标签 (可选)"
      }
    ],
    "nextChoices": [
      {
        "text": "string — 选项描述",
        "effect": "string — 预期效果",
        "worldlineImpact": "string — 对主线的影响 (stay_current|advance|advance_risky|side_branch)"
      }
    ],
    "worldlineState": {
      "currentNode": "string — 当前世界线节点",
      "nodeIndex": "number — 当前节点索引",
      "totalNodes": "number — 总节点数",
      "progress": "number — 进度百分比 (0-1)"
    }
  },
  "validation": {
    "passed": true,
    "checks": [
      { "name": "schema_compliance", "passed": true },
      { "name": "dialogue_count", "passed": true, "detail": "4 轮对话" },
      { "name": "character_consistency", "passed": true },
      { "name": "personality_match", "passed": true, "score": 0.85 },
      { "name": "worldline_adherence", "passed": true }
    ],
    "retryCount": 0
  },
  "metadata": {
    "generationTime": "number — 生成耗时 (ms)",
    "model": "string — 使用的模型",
    "tokenUsage": { "prompt": "number", "completion": "number" }
  }
}
```

---

## 续写机制

当玩家做出选择后，将完整输出作为 `previousContext` 传入下一轮调用：

```
第 1 轮: currentScene + characters + worldline → 生成内容 A
第 2 轮: nextScene + characters + worldline + playerChoice + previousContext(A) → 生成内容 B
第 3 轮: ...
```

**续写约束：**
- 新叙事必须衔接上一轮的末尾状态
- 角色情绪状态需延续或合理转变
- 世界线进度必须递增（除非选择 stay_current）
- 不得出现与上一轮矛盾的情节

---

## 配置管理

所有配置文件存放在 `config/` 目录：

| 文件 | 用途 |
|------|------|
| `worldline_template.yaml` | 世界线模板（可复用大纲结构） |
| `character_card_template.yaml` | 角色卡模板（标准化角色定义） |
| `example_scene.yaml` | 参考示例场景 |
| `harness_config.yaml` | Harness 运行参数配置 |

---

## 版本管理

当前版本: `1.0.0`

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-07-12 | 初始版本：5 阶段 Pipeline + 验证门控 + 续写机制 |
