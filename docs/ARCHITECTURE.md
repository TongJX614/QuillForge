# QuillForge 架构设计

> 版本：6.0.0 | 最后更新：2026-11-08

本文档面向希望理解内部实现或参与二次开发的读者。使用方式与接口说明见 [README](../README.md) 与 [API 接口文档](API接口文档.md)。

---

## 一、设计目标

QuillForge 把「生成一段可玩的互动叙事」拆解为一组职责单一、可替换的组件：

| 目标 | 实现手段 |
|------|----------|
| 生成流程可编排 | `Harness` 薄编排器驱动固定阶段序列 |
| LLM 与 Prompt 可替换 | `NarrativeSUT` 隔离模型调用与模板渲染 |
| 阶段可插拔 | 所有阶段实现 `StageBase` 接口 |
| 过程可观测 | `HarnessObserver` 解耦日志、SSE、指标 |
| 失败可兜底 | `FallbackStrategy` 抽象三层保底 |
| 解析与生成解耦 | 剧本解析不调用 LLM，只产出结构化数据 |

---

## 二、分层架构

```
┌──────────────────────────────────────────────────────────────┐
│ 前端  static/index.html · game.html · web_play.html          │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / SSE
┌───────────────────────────▼──────────────────────────────────┐
│ API 层  routes/（pages · story · upload · auto_run ·          │
│         session · realtime · script_gen · assets）           │
│         只做请求解析、调用引擎、格式化响应                     │
└───────────┬───────────────────────────────┬──────────────────┘
            │                               │
┌───────────▼──────────────┐   ┌────────────▼─────────────────┐
│ 编排层 quillforge/core.py │   │ 扩展引擎                      │
│ Harness.run / run_stream  │   │ realtime_engine · script_gen │
└───────────┬──────────────┘   │ image_gen · tts_gen          │
            │                  └──────────────────────────────┘
┌───────────▼──────────────────────────────────────────────────┐
│ 阶段层 stages/（context_assembly · narrative · dialogue ·     │
│                choices · validation）                        │
└───────────┬──────────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────────┐
│ SUT  sut.py（Prompt 加载/渲染 + LLM 调用封装）                │
│      llm_client.py（OpenAI 兼容客户端）                       │
└───────────┬──────────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────────┐
│ 数据层 generic_adapter.py（剧本解析/归一化）· schemas.py      │
│        config_manager.py · validator.py                      │
└──────────────────────────────────────────────────────────────┘
```

| 层 | 目录 | 职责 | 禁止事项 |
|----|------|------|----------|
| 入口 | `quillforge/server_start.py`、`src/server.py` | 启动、中间件、路由注册 | — |
| API | `src/routes/` | 请求解析、调用引擎、SSE 推送 | 不得直接导入 `llm_client` |
| 编排 | `src/quillforge/core.py` | 组装组件、驱动阶段、重试、通知 | 不得写入业务逻辑 |
| 阶段 | `src/quillforge/stages/` | 单阶段生成与校验 | 不得绕过 SUT 调用 LLM |
| SUT | `src/quillforge/sut.py` | Prompt 渲染、LLM 调用、JSON 解析 | — |
| 解析 | `src/generic_adapter.py`、`src/adapter/` | YAML 解析、字段归一化 | 不得调用 LLM |
| 支撑 | `src/validator.py`、`config_manager.py` 等 | 校验、配置、日志、会话 | — |

> `adapter/` 是 `generic_adapter.py` 的模块化拆分脚手架，当前 `parser.py` 仍是重导出，两者公共接口一致；新代码可直接从 `generic_adapter` 导入。

---

## 三、Harness 五阶段流水线

### 执行流程

```
Fixture 准备（PreparedContext）
        │
        ▼
Stage 1 上下文组装（无 LLM）
        │
        ▼
┌──────► Stage 2 叙事锚定 ──► Stage 3 对话生成 ──► Stage 4 选择分支 ──► Stage 5 验证门控
│                                                                        │
│                              验证失败且未达最大重试次数 ◄───────────────┘
└──────────────────────────────────────────────────────────────────────┘
        │
        ▼
Fallback 检查（对话不足时兜底）──► 构建 HarnessOutput
```

最大重试次数由 `config/quillforge_config.yaml` 的 `stages.validationGate.maxRetries` 控制（默认 2）。

### 阶段契约

| Stage | 类 | `name` | 编号 | LLM | 产物 |
|-------|----|--------|:----:|:---:|------|
| 1 | `ContextAssemblyStage` | `context_assembly` | 1 | 否 | `AssembledContext` |
| 2 | `NarrativeStage` | `narrative_anchoring` | 2 | 是 | 旁白文本（≤ 250 字） |
| 3 | `DialogueStage` | `dialogue_generation` | 3 | 是 | `list[Dialogue]`（3–15 轮） |
| 4 | `ChoicesStage` | `choice_branching` | 4 | 是 | `list[Choice]`（2–4 个） |
| 5 | `ValidationStage` | `validation_gate` | 5 | 否 | `ValidationResult` |

所有阶段继承 `StageBase`，实现 `name`、`stage_number`、`execute()`；需要增量推送时额外实现 `execute_stream()`，默认实现会退化为单次结果事件。

### 各阶段要点

**Stage 1 上下文组装**：从 `HarnessInput` 与额外上下文构建 `AssembledContext`，确定出场角色、当前世界线节点、历史摘要与 Prompt 变量，不产生 LLM 调用。

**Stage 2 叙事锚定**：基于系统提示词与剧本上下文生成场景旁白，输出经 `NarrativeStage.truncate()` 截断到 250 字。流式模式下逐字推送 `narration_delta`。

**Stage 3 对话生成**：生成 3–15 轮角色对话，校验 `speaker` 必须属于出场角色（含别名映射），可在对话间穿插少量动作/心理旁白。流式模式下逐条推送 `dialogue` / `action`。

**Stage 4 选择分支**：优先生成剧本中定义的选项（含 `unlock_conditions` 隐藏选项），不足时由 LLM 补足；`impact` 限定为 `advance`、`advance_risky`、`stay_current`、`side_branch`。

**Stage 5 验证门控**：调用 `NarrativeValidator` 执行 8 项检查，`critical` / `high` 级失败即判定不通过并触发重试。

---

## 四、三层 Fallback

对话数量低于 `dialogue_count_min` 时，`DialogueFallback` 依次降级：

| 层级 | 策略 | 说明 |
|:----:|------|------|
| 1 | Pipeline 重试 | 完整重跑 Stage 2–5，最多 `maxRetries` 次 |
| 2 | LLM 极简 Prompt | 收窄上下文，只要求补足缺失轮数，`call_json` 自带重试 |
| 3 | 硬编码保底 | 不依赖 LLM，按角色轮流生成兜底对话 |

保底结果同样会经过 `speaker` 合法性过滤，避免把幻觉角色写入最终输出。

---

## 五、验证门控

`NarrativeValidator.validate_all(narration, dialogues, choices)` 执行 8 项检查：

| 检查 | 级别 | 判定依据 |
|------|------|----------|
| `schema_compliance` | critical | 旁白长度、对话 `speaker`/`text`、选项 `text`/`effect` 完整性 |
| `character_consistency` | critical | `speaker` 必须属于角色卡名称集合 |
| `dialogue_count` | high | 轮数位于 `[min, max]` 区间 |
| `worldline_adherence` | high | 内容命中当前/下一世界线节点关键词 |
| `choice_validity` | high | `impact` 属于合法枚举 |
| `consecutive_speaker` | medium | 同一角色连续发言不超过 2 轮 |
| `personality_match` | medium | 对话关键词与角色性格的启发式匹配度 |
| `choice_diversity` | medium | 选项文本与 `impact` 类型的多样性 |

`critical` 与 `high` 全部通过才算整体通过；`medium` 失败只影响质量评分。

---

## 六、选择驱动叙事

| 机制 | 实现 |
|------|------|
| 选择历史注入 | 玩家选择文本与后果经 `{{choiceHistory}}` 注入 Stage 2/3/4 的 Prompt |
| 选项 ID 持久化 | `choose()` 保存 `option_id`，供 `unlock_conditions` 精确匹配 |
| 隐藏选项解锁 | 满足前置条件（前置场景 + 选项组合）后才进入候选集合 |
| 结局匹配 | 依据完整选择历史与结局条件做关键词匹配 |

---

## 七、数据模型

`src/schemas.py` 使用 `@dataclass` 定义全部输入输出结构：

| 模型 | 用途 |
|------|------|
| `Character` | 角色卡（10 字段，缺失字段可自动推断） |
| `GenerationOptions` | 轮数、模型等生成参数 |
| `PreviousContext` | 上一场景的旁白/对话/选择，用于续写 |
| `HarnessInput` | Pipeline 标准输入 |
| `AssembledContext` | Stage 1 产物，阶段间共享的上下文 |
| `Dialogue` / `Choice` | 对话与选项 |
| `ValidationCheck` / `ValidationResult` | 单项检查与整体结果 |
| `HarnessOutput` | 最终输出（`data` + `validation` + `metadata`） |
| `StageResult` / `HarnessState` | 阶段产物与运行期状态 |

新增字段必须带默认值，保持向后兼容。

---

## 八、配置系统

配置优先级：**环境变量 > `.env` > 默认值**，运行时参数位于 `quillforge/config/quillforge_config.yaml`。

| 配置段 | 关键项 |
|--------|--------|
| `llm` | `apiBase`、`model`、`temperature`、`maxTokens`、`timeout` |
| `stages.narrativeAnchoring` | `promptTemplate`、旁白长度区间 |
| `stages.dialogueGeneration` | `promptTemplate`、`dialogueCount.min/max/default` |
| `stages.choiceBranching` | `promptTemplate`、`choiceCount.min/max/default` |
| `stages.validationGate` | `maxRetries`、`personalityMatchScore`、`worldlineAdherenceScore` |
| `continuation` | `maxHistoryRounds`、`contextWindowStrategy` |
| `logging` | `level`、`logPrompts`、`logResponses` |

Prompt 模板位于 `quillforge/prompts/`，使用 Mustache 风格变量。模板变量名需与 `ExtraContext.to_prompt_variables()` / `StageBase._common_variables()` 对齐。

---

## 九、会话与实时生成

- `dependencies.py` 维护单例（Harness、图片生成器、TTS 生成器）与 `TTLCache` 会话表：普通会话 `maxsize=100`，实时会话 `maxsize=50`，TTL 默认 30 分钟。
- `_RealtimeSessionCache` 重写淘汰回调，在会话过期时关闭线程池，避免资源泄漏。
- `RealtimeStorySession` 负责一句话 → 分场景生成 → 选项 → 结局的完整流程：输入上限 150 字，场景安全上限 8 个。
- 后台预生成使用 `copy.deepcopy` 快照隔离 `scenes` / `history`，避免与主线程并发修改冲突。
- TTS 与图片生成通过 `queue.Queue` 生产-消费模型推送 SSE 事件，后台线程异常必须在任务内捕获。

---

## 十、扩展指南

**新增阶段**：继承 `StageBase`，实现 `name`、`stage_number`、`execute()`；如需流式推送再实现 `execute_stream()`，并在 `Harness` 的组件组装处注册。

**替换模型服务**：修改 `config/quillforge_config.yaml` 的 `llm.apiBase` / `llm.model`，或替换 `NarrativeSUT` 中的 `LLMClient` 实例，其余流程无需改动。

**新增观察者**：实现 `HarnessObserver` 的对应回调并注册到 `Harness`，不要修改编排流程。

**新增 API**：在 `src/routes/` 下按功能域新增路由并注册到 `server.py`；路由内不得直接调用 LLM，应通过引擎或依赖注入获取组件。

**修改 Prompt**：编辑 `quillforge/prompts/` 下的模板后，确认 `_common_variables()` 提供的变量与模板占位符一致，并通过测试验证渲染结果。

---

## 十一、质量保障

```bash
# 全量测试（9 个模块）
pytest

# 架构约束检查（适配器不调 LLM、路由不调 LLM、文件头规范）
python scripts/check_architecture.py

# pre-commit 门禁
pre-commit run --all-files
```

测试覆盖：Validator、Harness 核心流程、SUT、Observer、对话阶段、选择分支、实时引擎、会话管理、Schema 契约。
