# 更新日志

本项目的版本变更记录，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [6.0.0] - 2026-08-06

### 新增

- AI 实时生成模式：`/api/realtime/start`、`/choose`、`/state`、`/assets-stream`
- TTS 语音合成：音色克隆优先，失败回退预置音色，音频经 `/generated_audio/` 提供
- 互动剧场页面 `/game`（打字机、立绘、TTS、结局演出）
- AI 剧本生成：`POST /api/generate-script`，一句话生成完整剧本包
- 基础模块：`config_manager`、`llm_client`、`session_manager`、`dependencies`、`extra_context`
- pytest 测试套件（9 个模块）、`pyproject.toml`、`scripts/check_architecture.py`
- 示例剧本更新为 回响-DEMO 与 索尔维0801

### 变更

- 架构模块化重构：API 层按功能域拆分为 `routes/`（8 个模块，26 个端点）
- 新增 `adapter/` 解析层脚手架，`generic_adapter.py` 仍为解析核心
- 默认端口统一为 8050，文档同步至 6.0.0

## [5.0.0] - 2026-07-24

### 新增

- 项目更名为 QuillForge（原 narrative-harness）
- Harness 核心包：`core` / `sut` / `observer` / `fallback` / `fixtures` / `stages`
- `script_generator`、`realtime_engine`、`tts_gen` 模块
- `game.html` Galgame 前端
- 守夜人、薔薇刺、除夕的警铃示例剧本

### 变更

- 选择驱动叙事：`unlock_conditions` 隐藏选项过滤，完整选择历史经 `{{choiceHistory}}` 注入 Stage 2/3/4
- 选项提取优先级调整：关键选择优先于普通交互
- 世界书注入长度 400 → 800 字，新增设定类章节

## [4.0.0] - 2026-07-21

### 新增

- 流式生成 SSE：旁白逐字、对话逐条、动作旁白穿插
- `image_gen.py`：OpenAI 兼容生图、磁盘缓存、线程池并行、无 Key 占位回退
- 美术素材预生成 `assets-stream` 与图片服务 `/generated_images/`（no-cache）
- Galgame 前端增强：自动播放、历史含对话、结局全屏滚动、立绘双槽位

### 变更

- Prompt 调整：对话间克制穿插动作/心理旁白（最多 1–2 条）
- API 端点 19 → 22

## [3.2.0] - 2026-07-12

- 对话轮数调整为 3–15
- 三层保底机制
- 新增剧本支持，项目结构清理

## [3.1.0] - 2026-07-12

- API 文档完善
- 新增 SSE 流式端点
- 剧本选项优先机制

## [3.0.0] - 2026-07-12

- 角色卡扩展为 10 字段
- 全量剧本数据注入
- 多剧本验收通过

## [2.0.0] - 2026-07-12

- `generic_adapter` 通用剧本适配器
- 支持嵌套剧本结构
- Prompt v2 模板

## [1.0.0] - 2026-07-12

- 初始版本：五阶段 Pipeline + 验证门控 + 续写机制
