# QuillForge

基于 **Harness 架构**的 AI 互动叙事生成引擎，根据剧本数据（世界线大纲、角色卡、事件/结局等）自动生成符合主线的场景旁白、角色对话和玩家选择。支持**选择驱动叙事**：玩家选择影响后续剧情走向、解锁隐藏选项、触发不同结局。同时支持 **AI 实时生成模式**（一句话描述 → 实时生成可玩故事）、**AI 剧本生成**（一句话 → 完整剧本包）、**立绘/背景生图**与 **TTS 语音合成**。

> **版本**: 6.0.0 | **最后更新**: 2026-08-06 | **对话轮数**: 3-15 | **API 端点**: 26 个

## 快速开始

```powershell
# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制 .env.example 为 .env 并填入 API Key）
Copy-Item .env.example .env

# 启动服务器（默认端口 8050，自动打开浏览器）
python quillforge/server_start.py

# 或直接启动（跳过浏览器自动打开）
python quillforge/src/server.py --host 127.0.0.1 --port 8050

# API 文档
# Swagger UI: http://127.0.0.1:8050/docs
# ReDoc:      http://127.0.0.1:8050/redoc

# 运行全量测试
pytest
```

### 一键运行剧本

```powershell
# 上传剧本文件夹并自动生成全部场景
curl -X POST http://127.0.0.1:8050/api/load-folder \
  -H "Content-Type: application/json" \
  -d '{"path": "C:/path/to/剧本目录"}'
```

## 项目结构

```
quillforge/
├── quillforge.md                   # 完整技术规格文档
├── server_start.py                 # 服务器启动器（含 API Key 输入，端口 8050）
├── start_server.bat                # Windows 一键启动脚本
├── prompts/                        # Prompt 模板（Mustache 风格 v2）
│   ├── system_prompt_v2.md         # 系统提示词（剧本对齐 + 角色一致性）
│   ├── system_prompt_dynamic.md    # 动态系统提示词（叙事风格/世界书/概要等）
│   ├── narration_v2.md             # Stage 2: 旁白生成模板
│   ├── dialogue_v2.md              # Stage 3: 对话生成模板
│   └── choices_v2.md               # Stage 4: 选择分支模板
├── config/                         # 配置文件
│   ├── quillforge_config.yaml      # Harness 运行参数（LLM/阶段/验证阈值）
│   ├── character_card_template.yaml
│   ├── worldline_template.yaml
│   └── example_scene.yaml
├── samples/                        # 内置示例剧本（回响-DEMO、索尔维0801）
├── src/
│   ├── server.py                   # FastAPI 应用入口（中间件、路由注册）
│   ├── routes/                     # API 层（按功能域拆分，26 个端点）
│   │   ├── pages.py                # 前端页面（/ 与 /game）
│   │   ├── story.py                # 剧本交互（/api/story、/api/scene、/play）
│   │   ├── upload.py               # 剧本上传与加载
│   │   ├── auto_run.py             # 一键运行（同步 + SSE）
│   │   ├── session.py              # 会话管理（生成/选择/状态/历史/素材）
│   │   ├── realtime.py             # AI 实时生成模式
│   │   ├── script_gen.py           # AI 剧本生成
│   │   └── assets.py               # 静态资源（生成图片/音频服务）
│   ├── quillforge/                 # Harness 架构核心包
│   │   ├── core.py                 # 薄编排器（组件组装 + 流程驱动 + 重试循环）
│   │   ├── sut.py                  # SUT：LLM 客户端 + Prompt 模板加载/渲染
│   │   ├── observer.py             # Observer 模式（Logging/SSE/Metrics）
│   │   ├── fallback.py             # 三层保底策略
│   │   ├── fixtures.py             # Fixture 系统（PreparedContext）
│   │   └── stages/                 # 5 个可插拔 Stage
│   │       ├── base.py             # StageBase 接口
│   │       ├── context_assembly.py # Stage 1: 上下文组装
│   │       ├── narrative.py        # Stage 2: 叙事锚定（旁白流式生成）
│   │       ├── dialogue.py         # Stage 3: 对话生成（流式 JSON 解析）
│   │       ├── choices.py          # Stage 4: 选择分支（剧本选项优先）
│   │       └── validation.py       # Stage 5: 验证门控
│   ├── adapter/                    # 适配器层（剧本解析与数据归一化，不调用 LLM）
│   │   ├── parser.py               # GenericScriptAdapter（YAML 剧本解析核心）
│   │   ├── normalizer.py           # 角色字段归一化（fill_character_gaps）
│   │   └── loader.py               # 剧本加载（load_story/build_worldline）
│   ├── schemas.py                  # 数据模型（dataclass 定义所有 I/O 结构）
│   ├── llm_client.py               # 统一 LLM 客户端（OpenAI 兼容格式）
│   ├── config_manager.py           # 配置管理（环境变量 + YAML）
│   ├── validator.py                # 叙事验证器（8 项检查）
│   ├── extra_context.py            # 额外上下文（ExtraContext 类型安全封装）
│   ├── image_gen.py                # 生图模块（立绘/背景，磁盘缓存 + 按需生图）
│   ├── tts_gen.py                  # TTS 语音合成（音色克隆 + 预置音色回退）
│   ├── realtime_engine.py          # AI 实时生成引擎
│   ├── script_generator.py         # AI 剧本生成器（一句话 → 完整剧本包）
│   ├── session_manager.py          # 会话状态管理（TTLCache）
│   ├── dependencies.py             # FastAPI 依赖注入（单例管理）
│   ├── generic_adapter.py          # 兼容入口（委托 adapter/parser）
│   ├── formatters.py               # 输出格式化
│   ├── logger.py                   # 日志工具
│   └── static/                     # 前端页面
│       ├── index.html              # Galgame VN 主界面
│       └── game.html               # 互动剧场页面
└── tests/                          # pytest 测试套件（9 个模块）
```

仓库根目录还包含 `pyproject.toml`（pytest 配置）、`requirements.txt`、`.env.example`、`scripts/check_architecture.py`（架构约束检查）与 `AGENTS.md`（架构指南）。

# Harness 架构

QuillForge 采用真正的 Harness 架构，核心特征：

| 特征 | 说明 |
|------|------|
| **SUT 隔离** | LLM 客户端 + Prompt 渲染封装为 `NarrativeSUT`，可整体替换 |
| **可插拔 Stage** | 5 个阶段实现 `StageBase` 接口，可替换、重排、新增 |
| **Observer 模式** | 事件发射与执行逻辑解耦，支持 Logging/SSE/Metrics 观察者 |
| **Fixture 系统** | `PreparedContext` 管理前置准备与后置清理 |
| **Fallback 策略** | 三层保底抽象为 `DialogueFallback` 策略，可替换 |
| **薄编排器** | `Harness` 核心仅负责组装 + 驱动 + 重试 + 通知，不含业务逻辑 |

### 5 阶段流水线

```
Fixture → [Stage 1: 上下文组装] → [Stage 2: 叙事锚定] → [Stage 3: 对话生成]
        → [Stage 4: 选择分支] → [Stage 5: 验证门控] → Fixture teardown
                                      ↑                    │
                                      └── 验证失败重试 ────┘
```

**三层保底机制**（确保对话 ≥ 3 轮）：

```
Pipeline 重试（最多 2 次）→ 简化 prompt 重试 → 硬编码保底（不依赖 LLM）
```

### 选择驱动叙事机制

玩家选择通过以下机制影响后续剧情发展：

| 机制 | 说明 |
|------|------|
| **完整选择历史注入** | 玩家在每个场景的选择文本 + 后果（consequences）通过 `{{choiceHistory}}` 注入到旁白和对话的 prompt 中，LLM 据此体现选择路径的累积影响 |
| **选项 ID 保存** | `choose()` 保存 `option_id` 到历史记录，用于 `unlock_conditions` 精确匹配 |
| **unlock_conditions 过滤** | 隐藏选项仅在满足前置条件时展示（如"在 scene_01 选择 investigate 且在 scene_02 选择 continue"才解锁 hidden 选项） |
| **结局匹配** | 最终结局根据完整选择历史通过关键词同义词匹配 |

## API 端点总览

| 类型 | 端点 | 用途 |
|------|------|------|
| 便捷 | `POST /api/load-folder` | 从本地路径加载剧本 |
| 便捷 | `POST /api/auto-run` | 一键运行全部场景 |
| 便捷 | `POST /api/auto-run/stream` | SSE 流式一键运行 |
| 便捷 | `POST /api/generate-script` | AI 生成剧本（SSE 流式进度） |
| Session | `POST /api/start` | 创建游戏会话 |
| Session | `POST /api/session/{id}/generate` | 生成当前场景 |
| Session | `POST /api/session/{id}/generate-stream` | 流式生成场景（SSE） |
| Session | `GET /api/session/{id}/assets-stream` | 预生成美术素材（SSE） |
| Session | `POST /api/session/{id}/choose` | 提交玩家选择 |
| 实时 | `POST /api/realtime/start` | AI 实时生成（一句话 → 故事，SSE） |
| 实时 | `POST /api/realtime/{id}/choose` | 实时生成-选择分支（SSE） |
| 轻量 | `POST /api/story` | 加载剧本数据 |
| 轻量 | `POST /api/scene` | 生成/续写场景 |
| 前端 | `GET /` | Galgame VN 主界面 |
| 前端 | `GET /game` | 互动剧场页面 |
| 前端 | `GET /play` | 轻量互动剧情 Web 界面 |
| 资源 | `GET /generated_images/{path}` | 读取生成的背景/立绘图片 |
| 资源 | `GET /generated_audio/{path}` | 读取生成的 TTS 语音音频 |

> 完整端点详见 [API 接口文档](docs/API接口文档.md)。

## 输入/输出示例

**输入：**
```json
{
  "currentScene": "太空旅行中，飞船遭遇陨石流",
  "characters": [
    {
      "name": "林晗", "role": "物理学家",
      "personality": "冷静理性，喜欢用数据和概率分析问题",
      "speakingStyle": "学术腔，句式精准",
      "background": "天体物理学博士，飞船首席科学家",
      "motivation": "保护船员安全抵达目的地",
      "secrets": "知道飞船燃料不足但未告知其他人",
      "relationships": "与赵远山有深厚的工作信任",
      "appearance": "瘦高，戴眼镜，常穿实验服",
      "arc": "从纯粹理性到学会信任直觉"
    },
    {
      "name": "赵远山", "role": "指挥官",
      "personality": "果断务实，行动优先"
    }
  ],
  "worldline": "出发 → 旅行 → 危机 → 到达",
  "playerChoice": "选择左转躲避"
}
```

**输出：**
```json
{
  "success": true,
  "data": {
    "narration": "陨石流在雷达上密密麻麻，像一场无声的暴雨……",
    "dialogues": [
      { "speaker": "赵远山", "text": "左转！现在！", "emotion": "急切" },
      { "speaker": "林晗", "text": "左转的话我们会偏离航线至少 3 天。", "emotion": "分析" },
      { "speaker": "赵远山", "text": "先活下来再算账。", "emotion": "果断" },
      { "speaker": "林晗", "text": "……偏转角度 37 度，推进器全开。我已经算好了。", "emotion": "沉着" }
    ],
    "nextChoices": [
      { "text": "执行左转", "effect": "安全但延误", "worldlineImpact": "stay_current" },
      { "text": "正面穿越", "effect": "高风险高回报", "worldlineImpact": "advance_risky" }
    ],
    "worldlineState": { "currentNode": "危机", "nodeIndex": 2, "totalNodes": 4, "progress": 0.67 }
  },
  "validation": { "passed": true, "checks": [...], "retryCount": 0 }
}
```

## Character 角色卡（10 字段）

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 角色姓名 |
| `role` | 是 | 身份/职业 |
| `personality` | 是 | 性格描述 |
| `speakingStyle` | 否 | 说话风格 |
| `background` | 否 | 背景故事 |
| `motivation` | 否 | 角色动机 |
| `secrets` | 否 | 角色秘密 |
| `relationships` | 否 | 角色关系 |
| `appearance` | 否 | 外貌描述 |
| `arc` | 否 | 成长弧线 |

> 简单结构剧本（仅含 5 字段）自动降级推断缺失字段。

## 支持的剧本结构

**扁平结构（4 文件）**：元数据 + 主要角色 + 场景清单 + 主线剧情

**嵌套结构（11 文件）**：额外含世界线、事件清单、关键选择、结局系统、角色关系、世界书

## 续写机制

将上一轮输出作为 `previousContext` 传入即可实现连续叙事，同时传入 `choice_history` 以让 LLM 感知完整选择路径：

```python
from quillforge.core import create_default_harness

harness = create_default_harness()

# 第 1 轮
result_1 = harness.run({"currentScene": "...", "characters": [...], "worldline": "..."})

# 第 2 轮（玩家选择了选项 A）
result_2 = harness.run({
    "currentScene": "新场景描述",
    "characters": [...],
    "worldline": "...",
    "playerChoice": "选项A的描述",
    "previousContext": result_1["data"],
    "_choiceHistory": "场景1：选择了「选项A」— 后果：..."
})
```

## 支持的 LLM

兼容所有 OpenAI API 格式的服务：OpenAI、Azure OpenAI、Ollama、vLLM、DeepSeek、Moonshot 等。
在 `config/quillforge_config.yaml` 中配置 `apiBase` 和 `model` 即可。

## 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_MODEL` | 模型名称（默认 deepseek-chat） |
| `DEEPSEEK_BASE_URL` | API 地址（默认 https://api.deepseek.com） |
| `OPENAI_API_KEY` | 通用 OpenAI 兼容 API Key（优先级高于 DEEPSEEK） |
| `IMAGE_API_KEY` | 生图 API Key（不配置则禁用生图，走纯缓存/占位模式） |
| `IMAGE_BASE_URL` | 生图 API 地址 |
| `IMAGE_MODEL` | 生图模型（默认 seedream-5.0-lite） |
| `IMAGE_SIZE_BG` | 背景尺寸（默认 2560x1440） |
| `IMAGE_SIZE_CHAR` | 立绘尺寸（默认 1440x2560） |
| `IMAGE_CONCURRENCY` | 生图并发数（默认 6，推荐 9） |
| `IMAGE_BG_PRELOAD_COUNT` | 开局预加载场景数（默认 2） |
| `TTS_API_KEY` | TTS API Key（不配置则纯文字模式运行） |
| `TTS_BASE_URL` | TTS API 地址（默认 MiMo 网关） |
| `TTS_MODEL` | TTS 主模型（默认 mimo-v2.5-tts-voiceclone 音色克隆） |
| `TTS_FALLBACK_MODEL` | TTS 回退模型（默认 mimo-v2.5-tts 预置音色） |
| `TTS_VOICE_MAP` | 角色名→音色映射（如 `角色A:冰糖,角色B:voice_b.mp3`） |

完整配置参考 [.env.example](.env.example)。

## 内置示例剧本

回响-DEMO、索尔维0801（`quillforge/samples/` 目录）
