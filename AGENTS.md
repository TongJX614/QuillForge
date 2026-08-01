# AGENTS.md — QuillForge 项目架构指南

## 项目概述

QuillForge 是一个 AI 驱动的互动叙事生成引擎，基于 FastAPI 构建。支持两种模式：
- **上传剧本模式**（Harness 管线）：解析 YAML 剧本文件，通过五阶段流水线生成交互式叙事内容
- **AI 实时生成模式**：通过 `realtime_engine.py` 实时生成剧本

## 架构概览：Harness 五阶段流水线

核心架构为 **Harness 编排器 + SUT 隔离 + 可插拔 Stage**，执行流程：

```
Fixture Setup → Stage 1 → [重试循环: Stage 2 → 3 → 4 → 5] → Fallback → Output
```

| 阶段 | 名称 | 职责 | LLM 调用 |
|------|------|------|----------|
| Stage 1 | Context Assembly | 组装上下文（角色、世界线、场景） | 无 |
| Stage 2 | Narrative Anchoring | 生成旁白/叙事文本（≤250字） | 是 |
| Stage 3 | Dialogue Generation | 生成角色对话（3-15轮） | 是 |
| Stage 4 | Choice Branching | 生成分支选项（2-4个） | 是 |
| Stage 5 | Validation Gate | 验证质量（性格匹配、主线符合度） | 无 |

关键设计原则：
- **Harness 是薄编排器**：不含业务逻辑，只负责组装组件和驱动流程
- **SUT 隔离**：LLM 调用和 Prompt 渲染封装在 `NarrativeSUT` 中，可替换
- **Observer 模式**：日志、SSE 推送、指标收集通过观察者解耦
- **三层 Fallback**：对话不足时触发保底生成（LLM 重试 → 模板生成 → 硬编码兜底）
- **Fixture 系统**：`PreparedContext` 封装执行前的上下文准备

## 目录职责

```
quillforge/
├── src/
│   ├── quillforge/          # 核心引擎（Harness 架构）
│   │   ├── core.py          # Harness 编排器（薄编排，不含业务逻辑）
│   │   ├── sut.py           # System Under Test（LLM + Prompt 渲染隔离层）
│   │   ├── observer.py      # Observer 模式（Logging/SSE/Metrics 观察者）
│   │   ├── fallback.py      # 三层 Fallback 保底策略
│   │   ├── fixtures.py      # Fixture 系统（PreparedContext 准备）
│   │   └── stages/          # 五阶段 Pipeline 实现
│   │       ├── base.py      # StageBase 抽象基类
│   │       ├── context_assembly.py  # Stage 1
│   │       ├── narrative.py         # Stage 2
│   │       ├── dialogue.py          # Stage 3
│   │       ├── choices.py           # Stage 4
│   │       └── validation.py        # Stage 5
│   │
│   ├── routes/              # API 层（FastAPI 路由，按功能域拆分）
│   │   ├── pages.py         # 前端页面服务
│   │   ├── story.py         # 剧本交互（生成/选择/状态机）
│   │   ├── upload.py        # 剧本文件上传
│   │   ├── auto_run.py      # 自动运行
│   │   ├── session.py       # 会话管理
│   │   ├── realtime.py      # 实时生成模式
│   │   ├── script_gen.py    # 剧本生成
│   │   └── assets.py        # 静态资源（图片/音频）
│   │
│   ├── adapter/             # 适配器层（剧本解析与数据归一化）
│   │   ├── parser.py        # GenericScriptAdapter（YAML 剧本解析核心）
│   │   ├── normalizer.py    # 角色字段归一化（fill_character_gaps）
│   │   └── loader.py        # 剧本加载（load_story/build_worldline/build_scene_text）
│   │
│   ├── server.py            # FastAPI 应用入口（中间件、路由注册）
│   ├── schemas.py           # 数据模型（dataclass 定义所有 I/O 结构）
│   ├── llm_client.py        # 统一 LLM 客户端（OpenAI 兼容格式）
│   ├── config_manager.py    # 配置管理（环境变量 + YAML）
│   ├── validator.py         # 叙事验证器（NarrativeValidator）
│   ├── extra_context.py     # 额外上下文（ExtraContext 类型安全封装）
│   ├── image_gen.py         # 图片生成（立绘/背景）
│   ├── tts_gen.py           # TTS 语音合成
│   ├── realtime_engine.py   # AI 实时生成引擎
│   ├── session_manager.py   # 会话状态管理
│   ├── formatters.py        # 输出格式化
│   └── logger.py            # 日志工具
│
├── config/                  # 配置文件
│   ├── quillforge_config.yaml   # 运行参数（LLM/阶段/验证阈值）
│   ├── character_card_template.yaml
│   └── worldline_template.yaml
│
├── prompts/                 # Prompt 模板（Mustache 风格）
│   ├── system_prompt_v2.md
│   ├── narration_v2.md
│   ├── dialogue_v2.md
│   └── choices_v2.md
│
└── tests/                   # 测试
```

## 技术栈

- **Web 框架**: FastAPI + uvicorn
- **LLM 客户端**: openai SDK（兼容 DeepSeek/Azure/Ollama/vLLM 等 OpenAI API 格式）
- **数据模型**: dataclasses + Pydantic
- **配置**: PyYAML + python-dotenv
- **限流**: slowapi
- **图片**: rembg（背景移除）+ OpenAI Images API

## 编码约定

1. **文件头**：`# -*- coding: utf-8 -*-` + 模块级 docstring 说明职责
2. **导入**：`from __future__ import annotations` 置于顶部
3. **日志**：通过 `from logger import get_logger` 获取，名称格式 `"module.submodule"`
4. **类型标注**：所有公共方法必须有完整类型标注
5. **数据模型**：使用 `@dataclass`，定义在 `schemas.py`
6. **接口抽象**：使用 `ABC` + `@abstractmethod`（如 `StageBase`、`HarnessObserver`）
7. **分节注释**：使用 `═══` 分隔符划分代码区块
8. **Prompt 模板**：Mustache 风格 `{{variable}}`、`{{#if}}`、`{{#each}}`
9. **配置访问**：通过 `config_manager.get_settings()` 或 `load_config()` 获取
10. **路径处理**：使用 `pathlib.Path`，不用字符串拼接

## 变更约束

- **不得在 Harness 编排器（core.py）中添加业务逻辑**，业务逻辑属于 Stage 或 SUT
- **不得绕过 SUT 直接调用 LLM**，所有 LLM 调用必须经过 `NarrativeSUT` 或 `LLMClient`
- **新增 Stage 必须继承 `StageBase`**，实现 `name`、`stage_number`、`execute()` 三个抽象成员
- **路由层（routes/）不得包含生成逻辑**，只做请求解析、调用引擎、格式化响应
- **适配器层（adapter/）不得调用 LLM**，只做数据解析和归一化
- **schemas.py 中的 dataclass 变更需保持向后兼容**（新增字段必须有默认值）
- **配置文件变更需同步更新 `config/quillforge_config.yaml`**
- **Prompt 模板变更需验证渲染结果**，模板变量名与 `ExtraContext` / `_common_variables()` 对齐

## 运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务（默认端口 8766）
python server_start.py
# 或
python src/server.py --host 127.0.0.1 --port 8766
```

必需环境变量（`.env` 文件）：
- `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY` + `DEEPSEEK_BASE_URL` + `DEEPSEEK_MODEL`
- `IMAGE_API_KEY` / `IMAGE_BASE_URL` / `IMAGE_MODEL`（生图功能）

## 故障排除

### 实时引擎并发竞态

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| 后台预生成场景与主线程数据不一致 | `_start_pre_generation` 中 `self.scenes`/`self.history` 被主线程并发修改 | 确保使用 `copy.deepcopy` 快照（已实现），检查是否有新增字段未纳入快照 |
| TTS/图片 SSE 事件乱序或丢失 | `queue.Queue` 生产-消费模型中，后台线程异常未捕获 | 检查 `_tts_task`/`_bg_task` 的 try-except 是否覆盖所有异常路径 |
| 会话过期后线程池未释放 | `TTLCache` 淘汰时 `__delitem__` 未正确关闭 executor | 确认 `_RealtimeSessionCache.__delitem__` 调用了 `session.close()` |
| 多个 SSE 客户端同时写入同一 session | 实时路由未对 session 加互斥锁 | 避免同一 session_id 并发请求；必要时在路由层加 `threading.Lock` |

### LLM 调用超时与 Fallback 触发

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| `ReadTimeout` / `ConnectTimeout` 异常 | 网络波动或 LLM 服务过载；默认超时 90s（Harness）/ 180s（实时） | 检查 `.env` 中 `DEEPSEEK_BASE_URL` 可达性；适当调大 `config/quillforge_config.yaml` 中 `llm.timeout` |
| JSON 解析失败触发重试 | LLM 输出含 markdown 代码块或截断（max_tokens 不足） | 确认 `max_tokens` 配置充足（当前 Harness 3000/实时 6000）；检查是否禁用了 thinking（`extra_body={"thinking": {"type": "disabled"}}`） |
| 三层 Fallback 全部触发（日志出现 `[FALLBACK] 硬编码保底`） | Stage 3 对话生成连续失败或返回数量不足 `dialogue_count_min` | 检查 LLM API Key 余额；查看 `fallback.py` 日志确认是 LLM 异常还是解析失败 |
| 重试循环耗尽仍未通过验证 | Prompt 模板变量缺失导致 LLM 输出偏离 | 核对 `ExtraContext` 字段是否完整传入；检查 `prompts/` 模板中 `{{variable}}` 与 `_common_variables()` 对齐 |

### .env 缺失或配置错误

| 症状 | 原因 | 解决方案 |
|------|------|----------|
| 启动报 `sk-placeholder` 相关 401 错误 | 未配置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` | 在项目根目录 `.env` 中填写有效 API Key |
| 图片生成功能静默跳过（无报错但无图片） | `IMAGE_API_KEY` / `IMAGE_BASE_URL` / `IMAGE_MODEL` 缺失 | 补全 `.env` 中图片生成三件套；`ImageGenerator.enabled` 依赖三者同时存在 |
| TTS 不生效 | `TTS_API_KEY` / `TTS_BASE_URL` 未配置 | 补全 TTS 相关环境变量；`TTSGenerator.enabled` 为 False 时所有 TTS 调用返回空 |
| 配置优先级混乱（环境变量 vs YAML） | `config_manager.py` 优先级为：环境变量 > `.env` > 默认值 | 检查是否有系统环境变量覆盖了 `.env` 中的值 |

### Stage 5 验证拒绝常见原因

Stage 5 (`ValidationStage`) 通过 `NarrativeValidator.validate_all()` 执行 8 项检查，`critical` 或 `high` 级别失败即判定不通过：

| 检查项 | 级别 | 常见拒绝原因 |
|--------|------|-------------|
| `schema_compliance` | critical | 旁白 < 10 字；对话缺少 speaker/text；选项缺少 text/effect |
| `dialogue_count` | high | 对话轮数不在 `[min, max]` 范围内（默认 3-15） |
| `character_consistency` | critical | 对话 speaker 不在角色卡名称集合中（LLM 幻觉出不存在的角色） |
| `consecutive_speaker` | medium | 同一角色连续发言 ≥ 3 轮（不阻塞，但影响质量评分） |
| `personality_match` | medium | 对话关键词与角色性格描述偏离（启发式评分 < 0.5） |
| `worldline_adherence` | high | 生成内容未命中当前/下一世界线节点关键词（偏离主线） |
| `choice_diversity` | medium | 选项文本高度雷同或 impact 类型单一 |
| `choice_validity` | high | 选项 impact 不在合法集合 `{advance, advance_risky, stay_current, side_branch}` 中 |

**排查步骤**：查看日志中 `[FAIL] 验证未通过: [...]` 列出的失败检查名，对照上表定位原因。若频繁触发 `worldline_adherence`，检查 `世界书.md` 和 `主线剧情.yaml` 中节点名称是否过于抽象。

---

## 维护元数据

```
@maintainer: QuillForge Team
@last_reviewed: 2026-08-01
@version: 1.1.0
```
