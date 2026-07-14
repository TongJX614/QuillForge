# Narrative Harness — API 接口文档

> 版本：3.0.0 | 基础地址：`http://localhost:8766` | Swagger 文档：`http://localhost:8766/docs`

---

## 一、架构概览

```
┌─────────────┐   本地路径 / 上传    ┌──────────────────┐
│  剧本文件夹  │ ──────────────────► │ generic_adapter  │
│ (YAML 目录)  │                     │ (解析 + 角色提取) │
└─────────────┘                     └───────┬──────────┘
                                            │ 结构化数据
                                            ▼
┌─────────┐  Session API / 轻量 API  ┌──────────────────┐  prompt   ┌───────────┐
│ Web 前端 │ ◄────────────────────► │  harness.py       │ ────────► │ DeepSeek  │
│ /  /play │                        │  5 阶段 Pipeline   │ ◄──────── │ API       │
└─────────┘                        └──────────────────┘  JSON      └───────────┘
                                            │
                                            ▼
                                   ┌──────────────────┐
                                   │  validator.py     │
                                   │  8 项自动校验      │
                                   └──────────────────┘
```

接口分为 **三套**：

| 类型 | 特点 | 适用场景 |
|------|------|---------|
| **Session API** | 有状态会话，`/api/start` → `/generate` → `/choose` | 完整交互游戏流程 |
| **轻量 API** | 无状态单次调用，`/api/story` + `/api/scene` | 快速集成、脚本调用 |
| **便捷 API** | 一键操作，`/api/load-folder` + `/api/auto-run` | 批量生成、快速测试 |

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

```json
{
  "name": "江林",
  "role": "灵异博主，自媒体恐怖内容创作者",
  "personality": "胆子极大，为了流量可以不顾一切，表面上玩世不恭...",
  "speakingStyle": "轻浮、戏谑，恐惧时反而变得沉默寡言",
  "background": "江林为了拍摄'全国十大凶宅'系列视频，特意来到幸福里公寓..."
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
| `dialogues` | Dialogue[] | 3-5 轮角色对话 |
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

### 3.5 POST `/api/session/{session_id}/choose` — 提交玩家选择

玩家选择后自动续写下一场景。

**请求体**：

```json
{
  "choice_index": 0,
  "choice_text": "询问陈阿婆墙里的秘密"
}
```

**响应**：返回下一场景的 Pipeline 输出（同 `/generate`）

---

### 3.6 GET `/api/session/{session_id}/state` — 查询会话状态

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

### 3.7 GET `/api/session/{session_id}/history` — 查看对话历史

**响应**：

```json
{
  "history": [
    {
      "scene_index": 0,
      "scene_name": "迷雾降临",
      "narration": "...",
      "dialogues": [...],
      "nextChoices": [...],
      "player_choice": "询问陈阿婆墙里的秘密"
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
  "characters": [
    {"id": "jiang_lin", "name": "江林", "role": "灵异博主", "personality": "...", "speakingStyle": "...", "background": "..."}
  ],
  "totalScenes": 7,
  "scenes": [
    {"name": "迷雾降临", "location": "幸福里公寓，一楼大厅", "time": "深夜 23:00"}
  ]
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
http://localhost:8766/play?path=C:/Users/tongj/Desktop/测试剧本/回魂公寓
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
  ]
}
```

---

### 5.3 GET `/api/samples` — 获取内置示例列表

**响应**：

```json
{
  "samples": []
}
```

> 内置示例存放在 `samples/` 目录，当前已清空。

---

## 六、典型调用流程

### 6.1 一键运行（最简方式）

```
1. POST /api/auto-run  →  传入路径，自动跑完所有场景
2. 渲染返回的 results 数组
```

### 6.2 Session API（交互式游戏）

```
1. POST /api/load-folder          →  加载剧本
2. POST /api/start?script_id=xxx  →  创建会话
3. POST /api/session/{id}/generate →  生成第一幕
4. 展示 dialogues，渲染 nextChoices
5. POST /api/session/{id}/choose   →  提交选择
6. 重复 4~5 直到 state=finished
```

### 6.3 轻量 API（脚本集成）

```
1. POST /api/story  →  加载剧本，获取 totalScenes
2. POST /api/scene  →  sceneIndex=0，生成第一幕
3. POST /api/scene  →  sceneIndex=1，传入 playerChoice + previousDialogues
4. 重复 2~3 直到 sceneIndex >= totalScenes
```

---

## 七、5 阶段 Pipeline

每次生成请求经过以下阶段：

| 阶段 | 名称 | 说明 |
|------|------|------|
| Stage 1 | Context Assembly | 解析世界线节点、标注当前位置（annotatedWorldline）、推断紧张度 |
| Stage 2 | Narrative Anchoring | LLM 生成 150-300 字场景旁白，含完整角色卡和世界线进度 |
| Stage 3 | Dialogue Generation | LLM 生成 3-5 轮角色对话，遵循性格标签 + 说话风格 + 背景故事 |
| Stage 4 | Choice Branching | LLM 生成 2-4 个玩家选项，标注世界线影响类型 |
| Stage 5 | Validation Gate | 8 项自动校验，失败自动重试（最多 3 次） |

### 验证器检查项

| # | 检查项 | 级别 | 说明 |
|---|--------|------|------|
| 1 | `schema_compliance` | Critical | 旁白/对话/选择结构完整 |
| 2 | `dialogue_count` | High | 对话轮数 [3, 5] |
| 3 | `character_consistency` | Critical | speaker 全部在角色卡中 |
| 4 | `consecutive_speaker` | Medium | 无连续 3 轮同一角色发言 |
| 5 | `personality_match` | Medium | 对话风格符合角色性格 |
| 6 | `worldline_adherence` | High | 内容不偏离主线大纲 |
| 7 | `choice_diversity` | Medium | 选项效果多样性 |
| 8 | `choice_validity` | High | 选项内容有效性 |

---

## 八、剧本目录结构规范

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

---

## 九、端点总览

| Method | Path | 类型 | 用途 |
|--------|------|------|------|
| GET | `/` | 前端 | 首页（加载/上传/内置示例） |
| GET | `/play` | 前端 | 互动剧情 Web 界面 |
| POST | `/api/upload` | Session | 上传单个剧本文件 |
| POST | `/api/upload-directory` | Session | 上传文件夹 |
| POST | `/api/load-folder` | 便捷 | 从本地路径加载剧本 |
| POST | `/api/auto-run` | 便捷 | 一键运行全部场景 |
| POST | `/api/start` | Session | 创建游戏会话 |
| POST | `/api/session/{id}/generate` | Session | 生成当前场景 |
| POST | `/api/session/{id}/choose` | Session | 提交玩家选择 |
| GET | `/api/session/{id}/state` | Session | 查询会话状态 |
| GET | `/api/session/{id}/history` | Session | 查看对话历史 |
| POST | `/api/story` | 轻量 | 加载剧本数据 |
| POST | `/api/scene` | 轻量 | 生成/续写场景 |
| GET | `/api/samples` | 便捷 | 内置示例列表 |
| GET | `/docs` | 文档 | Swagger UI |
| GET | `/redoc` | 文档 | ReDoc 文档 |
| GET | `/openapi.json` | 文档 | OpenAPI Schema |

---

## 十、错误处理

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
