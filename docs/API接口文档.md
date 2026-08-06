# QuillForge — API 接口文档

> 版本：6.0.0 | 最后更新：2026-08-06 | 基础地址：`http://localhost:8050` | Swagger 文档：`http://localhost:8050/docs`

---

## 一、架构概览

```
┌─────────────┐   本地路径 / 上传    ┌──────────────────┐
│  剧本文件夹  │ ──────────────────► │ adapter/parser   │
│ (YAML 目录)  │                     │ (解析 + 归一化)   │
└─────────────┘                     └───────┬──────────┘
                                            │ 结构化数据
                                            ▼
┌─────────┐  Session API / 轻量 API  ┌──────────────────┐  prompt   ┌───────────┐
│ Web 前端 │ ◄────────────────────► │ quillforge/core   │ ────────► │ DeepSeek  │
│/ /game /play│                      │ 5 阶段 Pipeline   │ ◄──────── │ API       │
└─────────┘                        └──────────────────┘  JSON      └───────────┘
                                            │
                                            ▼
                                   ┌──────────────────┐
                                   │  validator.py     │
                                   │  8 项自动校验      │
                                   └──────────────────┘
```

接口分为 **四套**：

| 类型 | 特点 | 适用场景 |
|------|------|---------|
| **Session API** | 有状态会话，`/api/start` → `/generate` → `/choose` | 完整交互游戏流程 |
| **轻量 API** | 无状态单次调用，`/api/story` + `/api/scene` | 快速集成、脚本调用 |
| **便捷 API** | 一键操作，`/api/load-folder` + `/api/auto-run` + `/api/generate-script` | 批量生成、快速测试 |
| **实时 API** | 无剧本，一句话描述实时生成可玩故事（SSE） | AI 实时生成模式 |

### 选择驱动叙事

系统支持**选择驱动叙事**机制，玩家选择通过以下方式影响后续剧情：

| 机制 | 说明 |
|------|------|
| **完整选择历史注入** | 玩家在每个场景的选择文本 + 后果（consequences）通过 `{{choiceHistory}}` 注入到旁白和对话的 prompt 中，LLM 据此体现选择路径的累积影响 |
| **选项 ID 保存** | `choose()` 保存 `option_id` 到历史记录，用于 `unlock_conditions` 精确匹配 |
| **unlock_conditions 过滤** | 隐藏选项仅在满足前置条件时展示（如“在 scene_01 选择 investigate 且在 scene_02 选择 continue”才解锁 hidden 选项） |
| **结局匹配** | 最终结局根据完整选择历史通过关键词同义词匹配 |

---

## 二、数据模型

### 2.1 Character（角色卡）

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `name` | string | 是 | 角色姓名 |
| `role` | string | 是 | 角色职业/身份 |
| `personality` | string | 是 | 角色性格特征 |
| `speakingStyle` | string | 否 | 说话风格（如"轻浮、戏谑"） |
| `background` | string | 否 | 角色背景故事 |
| `motivation` | string | 否 | 角色动机（从 backstory 推断或剧本定义） |
| `secrets` | string | 否 | 角色秘密（从 backstory 推断或剧本定义） |
| `relationships` | string | 否 | 角色关系（从 backstory 推断或角色关系文件） |
| `appearance` | string | 否 | 外貌描述（从 personality 推断或剧本定义） |
| `arc` | string | 否 | 成长弧线（从 backstory 推断或剧本定义） |

> **降级策略**：对于结构简单的剧本（无独立角色关系/世界线等文件），适配器会自动从 `personality`/`background` 文本中推断缺失字段，确保全部 10 个字段有值。

```json
{
  "name": "江林",
  "role": "灵异博主，自媒体恐怖内容创作者",
  "personality": "胆子极大，为了流量可以不顾一切，表面上玩世不恭...",
  "speakingStyle": "轻浮、戏谑，恐惧时反而变得沉默寡言",
  "background": "江林为了拍摄'全国十大凶宅'系列视频，特意来到幸福里公寓...",
  "motivation": "为了拍摄'全国十大凶宅'系列视频",
  "secrets": "他的父亲江德明三十年前曾是这栋楼的物业管理员",
  "relationships": "父亲江德明（已故）：三十年前灭门惨案后突然辞职搬走",
  "appearance": "随身携带运动相机",
  "arc": "从灵异博主身份出发，经历内心冲突后做出关键抉择"
}
```

### 2.2 Dialogue（单轮对话）

| 字段 | 类型 | 说明 |
|------|------|------|
| `speaker` | string | 说话者姓名（必须在角色卡中） |
| `text` | string | 对话内容 |
| `emotion` | string | 情绪标签（中文：平静/急切/分析/果断/担忧/愤怒/希望/好奇/沉着/讽刺/恐惧/坚定） |

### 2.3 Choice（玩家选项）

| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | string | 选项文本 |
| `effect` | string | 该选项的简要效果描述 |
| `worldlineImpact` | string | 世界线影响类型：`advance` / `advance_risky` / `stay_current` / `side_branch` |

### 2.4 Pipeline 输出（完整响应）

| 字段 | 类型 | 说明 |
|------|------|------|
| `narration` | string | 场景旁白（150-300 字，第二人称） |
| `dialogues` | Dialogue[] | 3-15 轮角色对话 |
| `nextChoices` | Choice[] | 2-4 个玩家选项 |
| `worldlineState` | object | 世界线状态（currentNode / progress / nodeIndex / totalNodes） |
| `validation` | object | 8 项验证结果汇总 |

---

## 三、Session API（有状态会话）

> 适用于完整交互游戏流程，服务端维护会话状态。

### 3.1 POST `/api/upload` — 上传剧本文件

上传单个 YAML 剧本文件。

**请求**：`multipart/form-data`，字段名 `file`

**响应**：

```json
{
  "script_id": "a1b2c3d4",
  "title": "回魂公寓",
  "characters": 5,
  "scenes": 7
}
```

---

### 3.2 POST `/api/upload-directory` — 上传文件夹

上传多个剧本文件，保留子目录结构。

**请求**：`multipart/form-data`，字段名 `files`（多文件）

**响应**：同 `/api/upload`

---

### 3.3 POST `/api/start` — 开始游戏会话

创建新游戏会话。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `script_id` | string | 否 | 已上传剧本的 ID（不传则使用默认） |

**响应**：

```json
{
  "session_id": "abc12345",
  "script": {
    "title": "回魂公寓",
    "characters": [...],
    "scenes": [...]
  },
  "state": {
    "state": "idle",
    "current_scene": 0,
    "total_scenes": 7,
    "progress": 0.0
  }
}
```

---

### 3.4 POST `/api/session/{session_id}/generate` — 生成当前场景

调用 5 阶段 Pipeline 生成当前场景的旁白、对话和选项。

**路径参数**：`session_id` — 会话 ID

**响应**：

```json
{
  "success": true,
  "scene_name": "迷雾降临",
  "data": {
    "narration": "幸福里公寓的一楼大厅...",
    "dialogues": [
      {"speaker": "江林", "text": "...", "emotion": "分析"},
      {"speaker": "苏雯", "text": "...", "emotion": "担忧"}
    ],
    "nextChoices": [
      {"text": "询问陈阿婆墙里的秘密", "effect": "...", "worldlineImpact": "advance"}
    ],
    "worldlineState": {
      "currentNode": "困兽之楼",
      "progress": 0.0,
      "nodeIndex": 0,
      "totalNodes": 5
    }
  },
  "validation": {
    "passed": true,
    "checks": [...],
    "retry_count": 0
  },
  "state": {
    "state": "generated",
    "current_scene": 0
  }
}
```

---

### 3.5 POST `/api/session/{session_id}/generate-stream` — 流式生成场景（SSE）

流式版本的场景生成。Pipeline 各阶段产出时立即推送给前端，无需等待全部完成。旁白逐字推送、对话逐条推送，第一条对话在生成完成时即出现。

**路径参数**：`session_id` — 会话 ID

**响应**：`text/event-stream`，事件类型：

| 事件 | 时机 | data 内容 |
|------|------|----------|
| `meta` | 流开头 + 结尾各一次 | `{"scene_id", "scene_name", "script_choices", "session_state"}` — 前端据此应用背景图/场景名 |
| `stage` | 每个阶段开始 | `{"stage": 1-5, "message": "..."}` |
| `narration_start` | 旁白开始生成 | `{}` |
| `narration_delta` | 旁白逐字推送 | `{"delta": "增量文本"}` |
| `narration` | 旁白完成 | `{"text": "完整旁白"}` |
| `dialogue` | 每条对话解析完成 | `{"speaker", "text", "emotion"}` |
| `action` | 动作/心理旁白 | `{"subject": "角色名", "text": "描写"}` |
| `choices` | 选项生成完成 | `{"choices": [{text, effect, worldlineImpact}]}` |
| `retry` | 验证失败重试 | `{"attempt": N}` |
| `error` | 异常 | `{"message": "..."}` |

> `meta` 在流开头先下发一次（背景/场景名即时应用），结尾再下发一次（携带 session_state=generated 供前端显示选项）。

---

### 3.6 GET `/api/session/{session_id}/assets-stream` — 预生成美术素材（SSE）

游戏开始时预生成角色立绘 + 场景背景，线程池并行执行。**按需生图**：仅为至少出现在一个场景 `characters_present` 中的角色生成立绘，未引用角色跳过。已缓存的图片直接复用，未配置且无缓存时回退占位图。分两阶段推送：Phase 1（前 N 场景背景 + 出场角色）完成后前端即可开始游戏，Phase 2 后台补充剩余素材。

**响应**：`text/event-stream`，事件类型：

| 事件 | 时机 | data 内容 |
|------|------|----------|
| `assets_meta` | 开始 | `{"enabled": bool, "total": N, "message": "..."}` |
| `asset_progress` | 每张完成 | `{"kind": "character"|"scene", "name", "done", "total", "status": "done"|"fallback", "url"}` |
| `assets_done` | 全部完成 | `{"characters": {角色名: url}, "scenes": {scene_id: url}}` |

---

### 3.7 GET `/generated_images/{path}` — 读取生成的图片

返回 `generated_images/` 缓存目录下的图片文件（PNG）。响应头 `Cache-Control: no-cache`，确保手动替换图片后浏览器即时更新。

**路径示例**：`/generated_images/回响/char_su_ran_f529e7fb.png`

---

### 3.8 GET `/generated_audio/{path}` — 读取生成的音频

返回 `generated_audio/` 缓存目录下的 TTS 语音文件（WAV）。同一 (speaker + text) 仅合成一次，磁盘缓存复用。

---

### 3.9 POST `/api/session/{session_id}/choose` — 提交玩家选择

玩家选择后自动续写下一场景。

**请求体**：

```json
{
  "choice_index": 0,
  "choice_text": "询问陈阿婆墙里的秘密"
}
```

**处理逻辑**：
1. 根据当前场景的选项列表查找选中项，提取 `option_id` 和 `consequences`
2. 将选择信息（`scene_id`、`scene_name`、`option_id`、`player_choice`、`consequences`）保存到会话历史
3. 自动推进到下一场景，调用 5 阶段 Pipeline 生成（注入 `choice_history` 到 prompt + 用于 `unlock_conditions` 过滤）

**响应**：返回下一场景的 Pipeline 输出（同 `/generate`）

> 选择历史中每条记录包含：`scene_id`、`scene_name`、`player_choice`、`option_id`、`consequences`，后续场景生成时注入到旁白/对话/选择 prompt 中影响 LLM 生成。

---

### 3.10 GET `/api/session/{session_id}/state` — 查询会话状态

**响应**：

```json
{
  "state": "generated",
  "current_scene": 0,
  "total_scenes": 7,
  "progress": 0.14
}
```

**状态机**：`idle` → `generated` → `chosen` → `generated` → ... → `finished`

---

### 3.11 GET `/api/session/{session_id}/history` — 查看对话历史

历史含每个场景的旁白、完整对话和玩家选择（含 `option_id` 和 `consequences`）。

**响应**：

```json
{
  "history": [
    {
      "scene_index": 0,
      "scene_name": "迷雾降临",
      "scene_id": "scene_01",
      "narration": "...",
      "dialogues": [...],
      "nextChoices": [...],
      "player_choice": "询问陈阿婆墙里的秘密",
      "option_id": "investigate",
      "consequences": "你发现了墙中隐藏的物件"
    }
  ]
}
```

---

## 四、轻量 API（无状态单次调用）

> 无需维护会话，每次请求独立，适合脚本调用和快速集成。

### 4.1 POST `/api/story` — 加载剧本数据

**请求体**：

```json
{
  "path": "C:/Users/tongj/Desktop/测试剧本/回魂公寓"
}
```

**响应**：

```json
{
  "title": "回魂公寓",
  "genre": "悬疑恐怖",
  "narrativeStyle": "第二人称叙事",
  "characters": [
    {"id": "jiang_lin", "name": "江林", "role": "灵异博主", "personality": "...", "speakingStyle": "...", "background": "...", "motivation": "...", "secrets": "...", "relationships": "...", "appearance": "...", "arc": "..."}
  ],
  "totalScenes": 7,
  "scenes": [
    {"name": "迷雾降临", "location": "幸福里公寓，一楼大厅", "time": "深夜 23:00"}
  ],
  "worldLines": [...],
  "characterRelationships": [...],
  "events": [...],
  "endings": [...],
  "hasWorldbook": false,
  "plotSummary": "..."
}
```

---

### 4.2 POST `/api/scene` — 生成/续写当前场景

**请求体**（`PlayRequest`）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `storyPath` | string | 是 | 剧本目录的绝对路径 |
| `sceneIndex` | int | 是 | 场景索引（从 0 开始） |
| `playerChoice` | string | 否 | 玩家上一步的选择 |
| `previousDialogues` | Dialogue[] | 否 | 历史对话记录 |

> **选择驱动**：Session API 的 `choose()` 会自动将完整选择历史（含 `option_id` + `consequences`）注入到后续场景的生成 prompt 中。轻量 API 需调用方自行传入 `playerChoice`。

```json
{
  "storyPath": "C:/Users/tongj/Desktop/测试剧本/回魂公寓",
  "sceneIndex": 0
}
```

**响应**：Pipeline 完整输出（旁白 + 对话 + 选项 + 世界线状态）

---

### 4.3 GET `/play` — 互动剧情 Web 界面

返回浏览器可交互的 HTML 页面。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `path` | string | 否 | 预填剧本目录路径 |

**访问示例**：
```
http://localhost:8050/play?path=C:/Users/tongj/Desktop/测试剧本/回魂公寓
```

---

## 五、便捷 API

> 简化操作流程，适合批量生成和快速测试。

### 5.1 POST `/api/load-folder` — 从本地路径加载剧本

直接指定本地文件夹路径加载剧本，同时注册到 Session API 和轻量 API。

**请求体**：

```json
{
  "path": "C:/Users/tongj/Desktop/测试剧本/回魂公寓"
}
```

**响应**：

```json
{
  "script_id": "a1b2c3d4",
  "path": "C:/Users/tongj/Desktop/测试剧本/回魂公寓",
  "title": "回魂公寓",
  "story_title": "回魂公寓",
  "story_scenes": 7,
  "story_characters": 5
}
```

---

### 5.2 POST `/api/auto-run` — 一键运行全部场景

给定文件夹路径，自动加载剧本并逐场景生成对话，返回完整结果。

**请求体**（`AutoRunRequest`）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `path` | string | 是 | 剧本文件夹路径 |
| `max_scenes` | int | 否 | 最多生成几个场景（默认全部） |
| `start_scene` | int | 否 | 从第几个场景开始（0-based，默认 0） |

```json
{
  "path": "C:/Users/tongj/Desktop/测试剧本/回魂公寓",
  "max_scenes": 3,
  "start_scene": 0
}
```

**响应**：

```json
{
  "title": "回魂公寓",
  "total_scenes": 7,
  "generated_scenes": 3,
  "scene_range": [0, 2],
  "results": [
    {
      "scene_index": 0,
      "scene_name": "迷雾降临",
      "location": "幸福里公寓，一楼大厅",
      "success": true,
      "narration": "...",
      "dialogues": [...],
      "nextChoices": [...],
      "validation": {...}
    }
  ],
  "ending": {
    "narration": "...",
    "dialogues": [...],
    "all_endings": [
      {"title": "真相大白", "type": "完美结局", "narrative": "...", "epilogue": "...", "replay_value": "..."}
    ]
  }
}
```

> `ending` 仅在最后一个场景成功生成时有值，包含最后场景的旁白/对话和全部结局定义。

---

### 5.3 POST `/api/auto-run/stream` — 流式一键运行（SSE）

SSE（Server-Sent Events）流式版本的 auto-run。每个场景生成完毕后立刻推送给前端，无需等待全部场景完成。**首场景可见时间约为总时间的 1/3**。

**请求体**：同 `/api/auto-run`

**响应**：`text/event-stream`，事件类型：

| 事件 | 时机 | data 内容 |
|------|------|----------|
| `start` | 开始生成 | `{"title": "...", "total_scenes": N, "scene_range": [start, end]}` |
| `scene` | 每个场景完成 | 单个场景的完整结果（含 `scene_index`, `narration`, `dialogues`, `nextChoices`, `validation`, `elapsed_ms`） |
| `ending` | 最后场景成功后 | `{"narration": "...", "dialogues": [...], "all_endings": [...]}` |
| `done` | 全部完成 | `{"generated_scenes": M, "total_time_ms": T}` |
| `error` | 生成异常 | `{"scene_index": N, "scene_name": "...", "message": "..."}` |

**前端接入示例**：

```javascript
const response = await fetch('/api/auto-run/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ path: 'C:/测试剧本/回魂公寓' })
});
const reader = response.body.getReader();
const decoder = new TextDecoder();
// 解析 SSE 事件流
```

---

### 5.4 GET `/api/samples` — 获取内置示例列表

**响应**：

```json
{
  "samples": [
    {"id": "回响-DEMO", "filename": "回响-DEMO", "path": "...", "title": "回响", "total_scenes": 4, "characters": ["苏燃", "林晓", "杜未明"]}
  ]
}
```

> 内置示例存放在 `samples/` 目录，当前包含：回响-DEMO、索尔维0801。

---

### 5.5 POST `/api/upload-sample-path` — 从路径加载内置示例

从文件路径加载 `samples/` 目录下的内置示例剧本。

**查询参数**：`path` — 示例文件路径

**响应**：同 `/api/upload`

> 安全限制：仅允许加载 `samples/` 目录下的文件。

---

### 5.6 POST `/api/generate-script` — AI 生成剧本

输入一句话描述，SSE 流式返回生成进度，最终产出完整剧本包（元数据/角色/场景/剧情/世界书）。

---

## 六、AI 实时生成 API

> 无需上传剧本，输入一句话描述即可实时生成可玩故事。所有接口基于 SSE 流式推送，TTS 语音与场景事件实时交错下发。未配置 `TTS_API_KEY` 时自动降级为纯文字模式。

### 6.1 POST `/api/realtime/start` — 开始实时生成（SSE）

输入一句话描述，流式返回故事种子 + 第一个场景，并后台预生成角色立绘。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `prompt` | string | 是 | 剧本描述（≤ 150 字） |

**限流**：10 次/分钟（按客户端 IP）。

---

### 6.2 POST `/api/realtime/{session_id}/choose` — 选择分支（SSE）

玩家做出选择，流式返回下一场景（含 TTS 实时推送）。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `choice_index` | int | 是 | 选中选项索引 |

---

### 6.3 GET `/api/realtime/{session_id}/state` — 状态查询

返回实时生成会话的当前状态（场景进度、历史等）。

---

### 6.4 GET `/api/realtime/{session_id}/assets-stream` — 预生成美术素材（SSE）

为实时会话预生成角色立绘 + 已生成场景的背景图，Phase 1 完成后下发 `assets_ready` 事件，前端即可开始游戏。

---

## 七、典型调用流程

### 7.1 一键运行（最简方式）

```
1. POST /api/auto-run         →  传入路径，自动跑完所有场景（等全部完成）
2. POST /api/auto-run/stream  →  或使用 SSE 流式版本（逐场景推送，首场景更快可见）
3. 渲染返回的 results 数组
```

### 7.2 Session API（交互式游戏）

```
1. POST /api/load-folder          →  加载剧本
2. POST /api/start?script_id=xxx  →  创建会话
3. POST /api/session/{id}/generate →  生成第一幕
4. 展示 dialogues，渲染 nextChoices
5. POST /api/session/{id}/choose   →  提交选择
6. 重复 4~5 直到 state=finished
```

### 7.3 轻量 API（脚本集成）

```
1. POST /api/story  →  加载剧本，获取 totalScenes
2. POST /api/scene  →  sceneIndex=0，生成第一幕
3. POST /api/scene  →  sceneIndex=1，传入 playerChoice + previousDialogues
4. 重复 2~3 直到 sceneIndex >= totalScenes
```

### 7.4 AI 实时生成（无剧本）

```
1. POST /api/realtime/start                  →  输入一句话描述，流式获得故事种子 + 第一场景
2. GET  /api/realtime/{id}/assets-stream     →  预生成立绘/背景（可选）
3. POST /api/realtime/{id}/choose            →  提交选择，流式获得下一场景
4. 重复 3 直到故事结束
```

---

## 八、5 阶段 Pipeline

每次生成请求经过以下阶段：

| 阶段 | 名称 | 说明 |
|------|------|------|
| Stage 1 | Context Assembly | 解析世界线节点、标注当前位置（annotatedWorldline）、推断紧张度 |
| Stage 2 | Narrative Anchoring | LLM 生成 150-300 字场景旁白，含完整角色卡和世界线进度 |
| Stage 3 | Dialogue Generation | LLM 生成 3-15 轮角色对话，遵循性格标签 + 说话风格 + 背景故事。内置三层保底机制确保对话 ≥ 3 轮 |
| Stage 4 | Choice Branching | LLM 生成 2-4 个玩家选项，标注世界线影响类型。**若剧本已定义选项，跳过 LLM 直接使用剧本选项。** 选项提取优先级：`critical_choice→key_choices` > `branching_point.choice_id` > 顶层 `choices` > `interactions[].options[]`。随后对选项执行 `unlock_conditions` 过滤：隐藏选项仅在满足前置条件时展示（如“在 scene_01 选择 investigate”）。**选择历史通过 `{{choiceHistory}}` 注入 prompt** |
| — | 选择驱动 | 玩家选择保存 `option_id` + `consequences` 到会话历史，后续场景生成时注入到 Stage 2/3/4 的 prompt 中，LLM 据此体现选择路径的累积影响 |
| Stage 5 | Validation Gate | 8 项自动校验，失败自动重试（最多 3 次） |

### 验证器检查项

| # | 检查项 | 级别 | 说明 |
|---|--------|------|------|
| 1 | `schema_compliance` | Critical | 旁白/对话/选择结构完整 |
| 2 | `dialogue_count` | High | 对话轮数 [3, 15] |
| 3 | `character_consistency` | Critical | speaker 全部在角色卡中 |
| 4 | `consecutive_speaker` | Medium | 无连续 3 轮同一角色发言 |
| 5 | `personality_match` | Medium | 对话风格符合角色性格 |
| 6 | `worldline_adherence` | High | 内容不偏离主线大纲 |
| 7 | `choice_diversity` | Medium | 选项效果多样性 |
| 8 | `choice_validity` | High | 选项内容有效性 |

---

## 九、剧本目录结构规范

支持两种格式（按文件名关键词自动匹配）：

### 扁平结构

```
回魂公寓/
├── 元数据.yaml            ← 关键词「元数据」
├── 主要角色.yaml          ← 关键词「主要角色」或「角色」
├── 场景清单.yaml          ← 关键词「场景清单」或「场景」
└── 主线剧情.yaml          ← 关键词「主线剧情」或「主线」
```

### 嵌套结构

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

### 角色卡字段

| 字段 | 必填 | 说明 |
|------|:----:|------|
| `id` | 是 | 角色唯一标识（用于 `characters_present` 匹配） |
| `name` | 是 | 角色姓名 |
| `role` | 是 | 角色身份 |
| `occupation` | 否 | 职业（追加到 role 后） |
| `personality` | 是 | 性格描述（多行文本） |
| `voice_tone` | 否 | 说话风格 → 映射到 `speakingStyle` |
| `backstory` / `background` | 否 | 背景故事 → 映射到 `background` |
| `motivation` | 否 | 角色动机（缺失时从 backstory 自动推断） |
| `secrets` | 否 | 角色秘密（缺失时从 personality/backstory 自动推断） |
| `relationships` | 否 | 角色关系（缺失时从 backstory 自动推断，或从角色关系文件提取） |
| `appearance` | 否 | 外貌描述（缺失时从 personality 自动推断） |
| `arc` | 否 | 成长弧线（缺失时从 backstory 自动推断） |

---

## 十、端点总览

| Method | Path | 类型 | 用途 |
|--------|------|------|------|
| GET | `/` | 前端 | Galgame VN 主界面（流式演出/立绘/自动播放/历史） |
| GET | `/game` | 前端 | 互动剧场页面（VN Galgame 引擎） |
| GET | `/play` | 前端 | 轻量版互动剧情 Web 界面 |
| POST | `/api/upload` | Session | 上传单个剧本文件 |
| POST | `/api/upload-directory` | Session | 上传文件夹 |
| POST | `/api/load-folder` | 便捷 | 从本地路径加载剧本 |
| POST | `/api/auto-run` | 便捷 | 一键运行全部场景 |
| POST | `/api/auto-run/stream` | 便捷 | SSE 流式一键运行（逐场景推送） |
| POST | `/api/start` | Session | 创建游戏会话 |
| POST | `/api/session/{id}/generate` | Session | 生成当前场景（同步） |
| POST | `/api/session/{id}/generate-stream` | Session | 流式生成场景（SSE 逐条推送） |
| GET | `/api/session/{id}/assets-stream` | Session | 预生成美术素材（SSE 按需生图） |
| POST | `/api/generate-script` | 便捷 | AI 生成剧本（SSE 流式进度） |
| POST | `/api/realtime/start` | 实时 | AI 实时生成-开始（SSE，一句话 → 故事） |
| POST | `/api/realtime/{id}/choose` | 实时 | AI 实时生成-选择分支（SSE） |
| GET | `/api/realtime/{id}/state` | 实时 | AI 实时生成-状态查询 |
| GET | `/api/realtime/{id}/assets-stream` | 实时 | AI 实时生成-预生成立绘/背景（SSE） |
| GET | `/generated_images/{path}` | 资源 | 读取生成的背景/立绘图片 |
| GET | `/generated_audio/{path}` | 资源 | 读取生成的 TTS 语音音频 |
| POST | `/api/session/{id}/choose` | Session | 提交玩家选择 |
| GET | `/api/session/{id}/state` | Session | 查询会话状态 |
| GET | `/api/session/{id}/history` | Session | 查看对话历史 |
| POST | `/api/story` | 轻量 | 加载剧本数据 |
| POST | `/api/scene` | 轻量 | 生成/续写场景 |
| GET | `/api/samples` | 便捷 | 内置示例列表 |
| POST | `/api/upload-sample-path` | 便捷 | 从路径加载内置示例 |
| GET | `/docs` | 文档 | Swagger UI |
| GET | `/redoc` | 文档 | ReDoc 文档 |
| GET | `/openapi.json` | 文档 | OpenAPI Schema |

---

## 十一、错误处理

所有接口统一返回：

```json
{
  "detail": "错误描述信息"
}
```

| 状态码 | 场景 | 处理建议 |
|:------:|------|----------|
| 400 | 路径不存在 / 参数缺失 / 场景索引越界 | 检查请求参数 |
| 404 | 会话不存在 / 路径不存在 | 确认 session_id 或 path 正确 |
| 422 | 数据校验失败（Pydantic 模型校验） | 检查请求体格式 |
| 500 | 内部错误（LLM JSON 解析失败等） | 查看服务端日志 |
