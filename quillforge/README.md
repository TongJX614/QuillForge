# QuillForge

基于 **Harness 架构**的 AI 互动叙事生成引擎，根据剧本数据（世界线大纲、角色卡、事件/结局等）自动生成符合主线的场景旁白、角色对话和玩家选择。支持**选择驱动叙事**：玩家选择影响后续剧情走向、解锁隐藏选项、触发不同结局。

> **版本**: 5.0.0 | **最后更新**: 2026-07-24 | **对话轮数**: 3-15 | **API 端点**: 22 个 | **前端**: Galgame 视觉小说界面（流式演出）

## 快速开始

```powershell
# 安装依赖
pip install -r requirements.txt

# 启动服务器（端口 8766）
python server_start.py

# 或直接启动（跳过浏览器自动打开）
python src/server.py --host 127.0.0.1 --port 8766

# API 文档
# Swagger UI: http://127.0.0.1:8766/docs
# ReDoc:      http://127.0.0.1:8766/redoc
```

### 一键运行剧本

```powershell
# 上传剧本文件夹并自动生成全部场景
curl -X POST http://127.0.0.1:8766/api/load-folder \
  -H "Content-Type: application/json" \
  -d '{"path": "C:/path/to/剧本目录"}'
```

## 项目结构

```
quillforge/
├── quillforge.md                   # 完整技术规格文档
├── README.md                         # 本文件
├── requirements.txt                  # Python 依赖
├── server_start.py                   # 服务器启动器（含 API Key 输入）
├── run.py                            # 回响-DEMO 快速测试启动器
├── prompts/                          # Prompt 模板（v2）
│   ├── system_prompt_v2.md           # 系统提示词（剧本对齐 + 角色一致性）
│   ├── system_prompt_dynamic.md      # 动态系统提示词（叙事风格/世界书/概要等）
│   ├── narration_v2.md               # Stage 2: 旁白生成模板
│   ├── dialogue_v2.md                # Stage 3: 对话生成模板
│   └── choices_v2.md                 # Stage 4: 选择分支模板
├── config/                           # 配置文件
│   ├── quillforge_config.yaml         # Harness 运行参数
│   ├── character_card_template.yaml  # 角色卡模板（10 字段）
│   ├── worldline_template.yaml       # 世界线模板
│   └── example_scene.yaml            # 示例场景（含预期输出）
├── src/                              # 源代码
│   ├── server.py                     # FastAPI 后端（22 个 API 端点）
│   ├── quillforge/                   # Harness 架构核心包
│   │   ├── __init__.py
│   │   ├── core.py                   # 薄编排器（组件组装 + 流程驱动 + 重试循环）
│   │   ├── sut.py                    # SUT：LLM 客户端 + Prompt 模板加载/渲染
│   │   ├── observer.py               # Observer 模式（Logging/SSE/Metrics）
│   │   ├── fallback.py               # 三层保底策略
│   │   ├── fixtures.py               # Fixture 系统（PreparedContext）
│   │   └── stages/                   # 5 个可插拔 Stage
│   │       ├── base.py               # StageBase 接口
│   │       ├── context_assembly.py   # Stage 1: 上下文组装
│   │       ├── narrative.py          # Stage 2: 叙事锚定（旁白流式生成）
│   │       ├── dialogue.py           # Stage 3: 对话生成（流式 JSON 解析）
│   │       ├── choices.py            # Stage 4: 选择分支（剧本选项优先 + unlock_conditions 过滤）
│   │       └── validation.py         # Stage 5: 验证门控
│   ├── schemas.py                    # 数据模型（Character 10 字段 / HarnessInput / Output）
│   ├── validator.py                  # 验证器（8 项检查）
│   ├── image_gen.py                  # 生图模块（OpenAI 兼容协议 + 磁盘缓存 + 按需生图）
│   ├── script_generator.py           # AI 剧本生成器（一句话 → 完整剧本包 + ID校验）
│   ├── generic_adapter.py            # 通用剧本适配器（模糊匹配 + 多格式 + unlock_conditions 过滤 + 选择历史注入）
│   ├── realtime_engine.py           # AI 实时生成管线
│   ├── tts_gen.py                    # TTS 语音合成
│   └── static/                       # 前端页面
│       ├── index.html                # Galgame VN 主界面（流式演出/立绘/自动播放/历史）
│       └── game.html                 # 互动剧场页面
├── test_samples.py                   # 内置剧本一致性测试
└── tests/                            # 测试套件
    ├── test_acceptance.py            # 验收测试
    ├── test_echo_e2e.py              # 回响-DEMO 专项测试
    └── test_e2e_full.py              # 全链路三层验证
```

## Harness 架构

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

**unlock_conditions 示例**：

```yaml
options:
  - id: "hidden"
    text: "埋下伏笔"
    unlock_conditions:
      - "在scene_01选择investigate"   # 需 scene_01 选了 investigate
      - "在scene_02选择continue"     # 且 scene_02 选了 continue
```

**选择历史注入示例**：

```
- 信号：选择了「先调查发件人」— 后果：你追踪邮件来源，发现它经过多重加密跳转
- 碎片：选择了「继续深入」— 后果：你强行推进修复，看到了织女号灾难的完整画面
```

## API 端点总览

| 类型 | 端点 | 用途 |
|------|------|------|
| 便捷 | `POST /api/load-folder` | 从本地路径加载剧本 |
| 便捷 | `POST /api/auto-run` | 一键运行全部场景 |
| 便捷 | `POST /api/auto-run/stream` | SSE 流式一键运行 |
| Session | `POST /api/start` | 创建游戏会话 |
| Session | `POST /api/session/{id}/generate` | 生成当前场景（同步） |
| Session | `POST /api/session/{id}/generate-stream` | **流式生成场景（SSE 逐条推送）** |
| Session | `GET /api/session/{id}/assets-stream` | **预生成美术素材（SSE 并行生图）** |
| Session | `POST /api/session/{id}/choose` | 提交玩家选择 |
| 资源 | `GET /generated_images/{path}` | 读取生成的背景/立绘图片 |
| 轻量 | `POST /api/story` | 加载剧本数据 |
| 轻量 | `POST /api/scene` | 生成/续写场景 |
| 前端 | `GET /` | Galgame VN 主界面 |
| 前端 | `GET /play` | 轻量版互动剧情界面 |

> 完整 22 个端点详见 [API 接口文档](../docs/API接口文档.md)。

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
| `IMAGE_BASE_URL` | 生图 API 地址（默认火山方舟） |
| `IMAGE_MODEL` | 生图模型（默认 doubao-seedream-5-0-260128） |
| `IMAGE_SIZE_BG` | 背景尺寸（默认 2560x1440） |
| `IMAGE_SIZE_CHAR` | 立绘尺寸（默认 1440x2560） |
| `IMAGE_CONCURRENCY` | 生图并发数（默认 6，推荐 9） |
| `IMAGE_BG_PRELOAD_COUNT` | 开局预加载场景数（默认 2） |

## 内置示例剧本

回响-DEMO、守夜人、晚风知我意、暗香浮沉、霍格沃兹的阴影、一条狗的使命、铁衣照雪、除夕的警铃、鎏金囚笼、晚风信箱、狗生重启、蔷薇刺、将军的第二次性命

## Galgame 前端特性

- **流式演出**：旁白逐字打字机 + 对话逐条即时渲染（SSE generate-stream）
- **角色立绘**：动态双槽位（左/右），说话者高亮、新角色登场淡入替换
- **动作旁白**：对话间少量穿插角色动作/心理描写（斜体弱化 + 角色名前缀）
- **自动播放**：按文本长度估算阅读时间自动推进
- **历史记录**：含旁白 + 完整对话 + 玩家选择（含 option_id + consequences）
- **结局演出**：全屏顶端向下滚动打字机 + 结局卡片（根据选择历史匹配结局）
- **美术素材**：开局并行预生成背景/立绘（按需生图：仅为场景引用角色生成，磁盘缓存复用，无 Key 回退占位图）
- **角色匹配**：characters_present 支持精确/子串/token 三级模糊匹配，兼容多种剧本格式
- **AI 剧本生成**：一句话描述 → 自动创作完整剧本包（含 ID 一致性校验）
- **选择驱动叙事**：unlock_conditions 隐藏选项过滤 + 选择历史注入 LLM prompt + 结局关键词匹配
