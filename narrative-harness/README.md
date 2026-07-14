# Narrative Generation Harness

AI 驱动的互动叙事生成 Pipeline，根据世界线大纲和角色卡自动生成符合主线的内容和对话。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# Mock 模式运行验收测试（不需要 API Key）
python tests/run_validation.py

# 使用真实 LLM 运行
export OPENAI_API_KEY="your-key"
python tests/run_validation.py --live

# 直接运行 Pipeline
python src/harness.py config/example_scene.yaml -o output.json
```

## 项目结构

```
narrative-harness/
├── narrative-harness.md          # 完整技术规格文档（含 I/O 接口定义）
├── README.md                     # 本文件
├── requirements.txt              # Python 依赖
├── prompts/                      # Prompt 模板
│   ├── system_prompt.md          # 系统提示词（角色演绎 + 叙事规范）
│   ├── scene_assembly.md         # Stage 2: 场景组装模板
│   ├── dialogue_generation.md    # Stage 3: 对话生成模板
│   └── choice_branching.md       # Stage 4: 选择分支模板
├── config/                       # 配置文件
│   ├── harness_config.yaml       # Pipeline 运行参数
│   ├── character_card_template.yaml  # 角色卡模板
│   ├── worldline_template.yaml   # 世界线模板
│   └── example_scene.yaml        # 示例场景（含预期输出）
├── src/                          # 源代码
│   ├── schemas.py                # 数据模型（I/O Schema）
│   ├── harness.py                # Pipeline 引擎（5 阶段）
│   └── validator.py              # 验证器（8 项检查）
└── tests/
    └── run_validation.py         # 验收测试（5 项标准）
```

## Pipeline 架构

```
输入 → [Stage 1: 上下文组装] → [Stage 2: 叙事锚定] → [Stage 3: 对话生成]
     → [Stage 4: 选择分支] → [Stage 5: 验证门控] → 输出
                                    ↑                    │
                                    └── 验证失败重试 ────┘
```

## 输入/输出示例

**输入：**
```json
{
  "currentScene": "太空旅行中，飞船遭遇陨石流",
  "characters": [
    { "name": "林晗", "role": "物理学家", "personality": "冷静理性" },
    { "name": "赵远山", "role": "指挥官", "personality": "果断务实" }
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
      { "speaker": "赵远山", "text": "左转！现在！", "emotion": "urgent" },
      { "speaker": "林晗", "text": "左转的话我们会偏离航线至少 3 天。", "emotion": "analytical" }
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

## 接口文档

完整的输入/输出接口规范见 [narrative-harness.md](narrative-harness.md)。

## 续写机制

将上一轮输出作为 `previousContext` 传入即可实现连续叙事：

```python
# 第 1 轮
result_1 = harness.run({"currentScene": "...", "characters": [...], "worldline": "..."})

# 第 2 轮（玩家选择了选项 A）
result_2 = harness.run({
    "currentScene": "新场景描述",
    "characters": [...],
    "worldline": "...",
    "playerChoice": "选项A的描述",
    "previousContext": result_1["data"]
})
```

## 支持的 LLM

兼容所有 OpenAI API 格式的服务：OpenAI、Azure OpenAI、Ollama、vLLM、DeepSeek、Moonshot 等。
在 `config/harness_config.yaml` 中配置 `apiBase` 和 `model` 即可。
