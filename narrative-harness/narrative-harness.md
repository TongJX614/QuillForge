# Narrative Generation Harness — 技术规格文档

> **版本**: 3.2.0 | **最后更新**: 2026-07-12

## 概述

Narrative Generation Harness 是一个结构化的 AI 文本生成 Pipeline，用于根据剧本数据（世界线大纲、角色卡、事件/结局等）自动生成互动叙事内容。该系统借鉴 Harness.io 的 Pipeline 设计理念，将叙事生成拆解为 5 个阶段（Stage），每个阶段有明确的输入、输出和验证门控（Validation Gate）。

系统支持两种剧本结构：
- **扁平结构**（4 文件）：元数据 + 角色 + 主线 + 场景
- **嵌套结构**（11 文件）：额外含世界线、事件、结局、角色关系、世界书等

通过 `generic_adapter.py` 自动识别并按关键词匹配文件，`harness.py` 执行 5 阶段 Pipeline，`server.py` 提供 19 个 API 端点（Session API + 轻量 API + 便捷 API）。

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
│  │  ☐ 对话轮数 3-15  ☐ 角色一致性   ☐ 主线符合度            │  │
│  │  ☐ 选择合理性     ☐ Schema 合规  ☐ 性格匹配度            │  │
│  │  ☐ 连续发言检查   ☐ 选择多样性   ☐ 选择有效性            │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ★ 验证失败 → 自动回退重新生成（最多 2 次）                     │
│  ★ 对话不足 → 三层保底（简化 prompt → 硬编码保底）              │
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
| `_worldbook` | string | 否 | 世界书约束规则 |
| `_narrativeStyle` | string | 否 | 叙事风格（如"第二人称叙事"） |
| `_plotSummary` | string | 否 | 故事概要 |
| `_coreConflict` | string | 否 | 核心冲突 |
| `_themes` | string | 否 | 故事主题 |
| `_relationshipNetwork` | string | 否 | 角色关系网络 |
| `_stagesOverview` | string | 否 | 剧情阶段总览 |
| `_events` | string | 否 | 关键事件清单 |
| `_keyChoices` | string | 否 | 关键选择及后果 |
| `_endings` | string | 否 | 结局系统 |
| `_sceneDetails` | string | 否 | 当前场景详情（氛围/互动/叙事笔记） |
| `_sceneBeats` | string | 否 | 当前场景剧情节拍 |
| `_sceneHooks` | string | 否 | 当前场景剧情钩子 |

**Character 对象结构（10 字段）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 角色姓名 |
| `role` | string | 是 | 角色身份/职业 |
| `personality` | string | 是 | 性格特征描述 |
| `speakingStyle` | string | 否 | 说话风格（如"轻浮、戏谑"） |
| `background` | string | 否 | 背景故事 |
| `motivation` | string | 否 | 角色动机 |
| `secrets` | string | 否 | 角色秘密 |
| `relationships` | string | 否 | 角色关系 |
| `appearance` | string | 否 | 外貌描述 |
| `arc` | string | 否 | 成长弧线 |

> **降级策略**：`generic_adapter.py` 对简单结构剧本自动从 personality/background 推断缺失字段（motivation/secrets/relationships/appearance/arc），确保全部 10 个字段有值。

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
  "annotatedWorldline": "● 出发 → ● 旅行 → 【危机】 → ○ 到达",
  "sceneDescription": "太空旅行中，飞船遭遇陨石流",
  "activeCharacters": [...],
  "playerDecision": "选择左转躲避（非正面硬扛）"
}
```

---

### Stage 2: Narrative Anchoring（叙事锚定）

**目的：** 基于组装好的上下文，生成场景描写/旁白，为后续对话奠定基调。

**输入：** `AssembledContext` + 全局 `_` 前缀变量

**Prompt 模板：** `prompts/narration_v2.md`

**系统提示词：** `prompts/system_prompt_v2.md` + `prompts/system_prompt_dynamic.md`

**Prompt 策略：**

- 系统提示词注入叙事风格、世界书约束、故事概要、核心冲突、角色关系
- 用户提示词注入完整 AssembledContext + 剧情阶段总览 + 关键事件
- 要求生成 150-300 字的场景描写（第二人称视角）
- 必须体现世界线当前位置的叙事张力
- 若存在 `playerDecision`，描写需反映该选择的即时影响

**输出：** `narration` 字符串

**验证：**
- 字数在 10+ 字（过短则 schema_compliance 失败）
- 不包含角色对话（对话由 Stage 3 生成）

---

### Stage 3: Dialogue Generation（对话生成）

**目的：** 生成 **3-15 轮**符合角色性格的对话。

**输入：** `AssembledContext` + `narration`

**Prompt 模板：** `prompts/dialogue_v2.md`

**对话生成规则：**

1. 每轮对话必须包含 `speaker` 和 `text`
2. 同一角色不连续发言超过 2 轮
3. 对话长度：短句 5-15 字（紧张场景），长句 15-40 字（分析/讨论场景）
4. 必须体现性格标签：冷静理性的角色不会大喊大叫，果断务实的角色不会犹豫不决
5. 最后一轮对话应自然过渡到"决策点"
6. 情绪标签使用中文：平静/急切/分析/果断/担忧/愤怒/希望/好奇/沉着/讽刺/恐惧/坚定/紧张/释然/中性

**输出：** `dialogues` 数组

```json
[
  { "speaker": "赵远山", "text": "左转！现在！", "emotion": "急切" },
  { "speaker": "林晗", "text": "左转的话我们会偏离航线至少 3 天。燃料够吗？", "emotion": "分析" },
  { "speaker": "赵远山", "text": "先活下来再算账。", "emotion": "果断" },
  { "speaker": "林晗", "text": "……明白。我已经计算好偏转角度了。", "emotion": "沉着" }
]
```

**三层保底机制：**

```
Pipeline 重试（max_retries=2 次）
    │
    ▼ 对话不足 min 轮
简化 prompt 重试（_retries=2 次，极简指令强制生成）
    │
    ▼ LLM 仍无法生成
硬编码保底（不依赖 LLM，角色轮流发言，确保 ≥ min 轮）
```

**验证门控：**
- ☐ 对话轮数 3-15 轮
- ☐ 每个 speaker 都存在于 `characters` 列表中
- ☐ 无同一角色连续 3 轮以上发言
- ☐ 对话中体现性格标签（启发式关键词匹配）

---

### Stage 4: Choice Branching（选择分支）

**目的：** 生成 2-4 个有意义的后续选择，每个选择有明确的叙事效果。

**输入：** `AssembledContext` + `narration` + `dialogues`

**Prompt 模板：** `prompts/choices_v2.md`

**选择生成规则：**

1. 至少 2 个选择，最多 4 个
2. 每个选择必须有 `text`（选项描述）和 `effect`（预期效果）
3. 至少一个选择推进主线（向世界线下一节点发展）
4. 选择之间应有实质差异，避免"换皮"选项
5. 选择文本 5-20 字，效果描述 5-15 字

**剧本选项优先：** 若剧本已定义选项（`interactions[].options[]` 或 `critical_choice` 匹配 `key_choices`），跳过 LLM 直接使用剧本选项。

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

**目的：** 对完整输出进行 8 项质量校验，不通过则触发回退重新生成。

**验证清单：**

| # | 检查项 | 严重度 | 说明 |
|---|--------|--------|------|
| 1 | `schema_compliance` | Critical | 旁白/对话/选择结构完整性 |
| 2 | `dialogue_count` | High | 对话轮数在 [3, 15] 范围内 |
| 3 | `character_consistency` | Critical | 所有 speaker 在角色卡中 |
| 4 | `consecutive_speaker` | Medium | 无连续 3 轮以上同一角色发言 |
| 5 | `personality_match` | Medium | 对话风格符合角色性格（启发式） |
| 6 | `worldline_adherence` | High | 内容不偏离主线大纲 |
| 7 | `choice_diversity` | Medium | 选项效果多样性 |
| 8 | `choice_validity` | High | 选项内容有效性 |

**重试策略：**

- Critical 或 High 级别失败 = 总体不通过，触发回退重试
- 最多重试 **2 次**（配置项 `maxRetries`）
- 超过重试次数则输出当前结果 + 验证状态
- 对话不足时额外触发三层保底机制

---

## 输入接口规范

### 完整请求体

```json
{
  "currentScene": "string (必填) — 当前场景描述",
  "characters": [
    {
      "name": "string (必填) — 角色姓名",
      "role": "string (必填) — 角色身份/职业",
      "personality": "string (必填) — 性格描述",
      "speakingStyle": "string (可选) — 说话风格",
      "background": "string (可选) — 背景故事",
      "motivation": "string (可选) — 角色动机",
      "secrets": "string (可选) — 角色秘密",
      "relationships": "string (可选) — 角色关系",
      "appearance": "string (可选) — 外貌描述",
      "arc": "string (可选) — 成长弧线"
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
    "dialogueCount": "number (可选, 默认 4, 范围 3-15)",
    "narrationLength": "string (可选, 'short'|'medium'|'long', 默认 'medium')",
    "tone": "string (可选, 默认自动推断)",
    "language": "string (可选, 默认 'zh-CN')"
  },
  "_worldbook": "string (可选) — 世界书约束",
  "_narrativeStyle": "string (可选) — 叙事风格",
  "_plotSummary": "string (可选) — 故事概要",
  "_coreConflict": "string (可选) — 核心冲突",
  "_events": "string (可选) — 关键事件",
  "_keyChoices": "string (可选) — 关键选择",
  "_endings": "string (可选) — 结局系统",
  "_sceneDetails": "string (可选) — 场景详情",
  "_sceneBeats": "string (可选) — 剧情节拍",
  "_sceneHooks": "string (可选) — 剧情钩子"
}
```

---

## 输出接口规范

### 完整响应体

```json
{
  "success": true,
  "data": {
    "narration": "string — 场景描写/旁白，150-300字",
    "dialogues": [
      {
        "speaker": "string — 角色姓名",
        "text": "string — 对话内容",
        "emotion": "string — 中文情绪标签（平静/急切/分析/果断/担忧/愤怒/希望/好奇/沉着/讽刺/恐惧/坚定/紧张/释然/中性）"
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
      { "name": "schema_compliance", "passed": true, "severity": "critical" },
      { "name": "dialogue_count", "passed": true, "severity": "high", "detail": "4 轮对话（要求 3-15）" },
      { "name": "character_consistency", "passed": true, "severity": "critical" },
      { "name": "consecutive_speaker", "passed": true, "severity": "medium" },
      { "name": "personality_match", "passed": true, "severity": "medium", "score": 0.85 },
      { "name": "worldline_adherence", "passed": true, "severity": "high", "score": 0.52 },
      { "name": "choice_diversity", "passed": true, "severity": "medium" },
      { "name": "choice_validity", "passed": true, "severity": "high" }
    ],
    "retryCount": 0
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

## API 端点总览

共 **19 个端点**（16 路由 + 3 文档端点）：

| Method | Path | 类型 | 用途 |
|--------|------|------|------|
| GET | `/` | 前端 | 首页 |
| GET | `/play` | 前端 | 互动剧情 Web 界面 |
| POST | `/api/upload` | Session | 上传单个剧本文件 |
| POST | `/api/upload-directory` | Session | 上传文件夹 |
| POST | `/api/load-folder` | 便捷 | 从本地路径加载剧本 |
| POST | `/api/auto-run` | 便捷 | 一键运行全部场景 |
| POST | `/api/auto-run/stream` | 便捷 | SSE 流式一键运行 |
| POST | `/api/start` | Session | 创建游戏会话 |
| POST | `/api/session/{id}/generate` | Session | 生成当前场景 |
| POST | `/api/session/{id}/choose` | Session | 提交玩家选择 |
| GET | `/api/session/{id}/state` | Session | 查询会话状态 |
| GET | `/api/session/{id}/history` | Session | 查看对话历史 |
| POST | `/api/story` | 轻量 | 加载剧本数据 |
| POST | `/api/scene` | 轻量 | 生成/续写场景 |
| GET | `/api/samples` | 便捷 | 内置示例列表 |
| POST | `/api/upload-sample-path` | 便捷 | 从路径加载示例 |
| GET | `/docs` | 文档 | Swagger UI |
| GET | `/redoc` | 文档 | ReDoc 文档 |
| GET | `/openapi.json` | 文档 | OpenAPI Schema |

---

## 剧本目录结构

支持两种格式（`generic_adapter.py` 按文件名关键词自动匹配）：

### 扁平结构（4 文件）

```
回魂公寓/
├── 元数据.yaml            ← 关键词「元数据」
├── 主要角色.yaml          ← 关键词「主要角色」或「角色」
├── 场景清单.yaml          ← 关键词「场景清单」或「场景」
└── 主线剧情.yaml          ← 关键词「主线剧情」或「主线」
```

### 嵌套结构（11 文件）

```
回响-DEMO/
├── 剧本元数据.yaml
├── 角色/
│   ├── 主要角色.yaml
│   └── 角色关系.yaml
├── 场景/
│   └── 场景清单.yaml
├── 剧情/
│   ├── 主线剧情.yaml
│   ├── 世界线.yaml
│   └── 事件/
│       ├── 事件清单.yaml
│       └── 关键选择.yaml
└── 世界书.md
```

---

## 配置管理

配置文件存放在 `config/harness_config.yaml`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `llm.maxTokens` | 2000 | LLM 最大输出 token |
| `llm.model` | deepseek-chat | 使用的模型 |
| `dialogueGeneration.dialogueCount.min` | 3 | 最少对话轮数 |
| `dialogueGeneration.dialogueCount.max` | 15 | 最多对话轮数 |
| `dialogueGeneration.dialogueCount.default` | 4 | 默认对话轮数 |
| `stages.validationGate.maxRetries` | 2 | 验证失败最大重试次数 |

---

## 源码文件说明

| 文件 | 用途 |
|------|------|
| `src/server.py` | FastAPI 后端，19 个 API 端点 |
| `src/harness.py` | 5 阶段 Pipeline 核心引擎 |
| `src/schemas.py` | 数据模型定义（Character/HarnessInput/Dialogue/Choice 等） |
| `src/validator.py` | 8 项验证器 |
| `src/generic_adapter.py` | 通用剧本适配器（扁平/嵌套结构自动识别） |
| `src/echo_adapter.py` | 回响-DEMO 专用适配器 |
| `prompts/system_prompt_v2.md` | 系统提示词模板 |
| `prompts/system_prompt_dynamic.md` | 动态系统提示词（叙事风格/世界书/概要/冲突/关系） |
| `prompts/narration_v2.md` | Stage 2 旁白生成模板 |
| `prompts/dialogue_v2.md` | Stage 3 对话生成模板 |
| `prompts/choices_v2.md` | Stage 4 选择生成模板 |

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 3.2.0 | 2026-07-12 | 对话轮数 3-15，三层保底机制，6 剧本支持，项目清理 |
| 3.1.0 | 2026-07-12 | API 文档完善，SSE 流式端点，剧本选项优先机制 |
| 3.0.0 | 2026-07-12 | 角色卡 10 字段，全数据注入，5 剧本验收通过 |
| 2.0.0 | 2026-07-12 | generic_adapter，嵌套结构支持，prompt v2 模板 |
| 1.0.0 | 2026-07-12 | 初始版本：5 阶段 Pipeline + 验证门控 + 续写机制 |
