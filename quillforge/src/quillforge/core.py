# -*- coding: utf-8 -*-
"""
Harness Core — 薄编排器

这是整个 Harness 架构的核心。Harness 类只负责：
  1. 组装组件（SUT、Stages、Validator、Fallback、Observers）
  2. 驱动执行流程（Fixture setup → Stage 1-5 → Fallback → Fixture teardown）
  3. 重试循环编排
  4. 通知观察者

Harness 不包含任何业务逻辑：
  - 不调用 LLM（委托给 SUT）
  - 不渲染 Prompt（委托给 SUT）
  - 不验证内容（委托给 Validator）
  - 不生成保底对话（委托给 Fallback）
  - 不直接推送 SSE（委托给 Observer）
"""

from __future__ import annotations

import time
from typing import Optional, Generator

from schemas import (
    HarnessInput, HarnessOutput, AssembledContext,
    Dialogue, Choice, ValidationResult,
    HarnessState,
)
from validator import NarrativeValidator
from .sut import NarrativeSUT, load_config
from .stages.base import StageBase
from .stages.context_assembly import ContextAssemblyStage
from .stages.narrative import NarrativeStage
from .stages.dialogue import DialogueStage
from .stages.choices import ChoicesStage
from .stages.validation import ValidationStage
from .observer import HarnessObserver, LoggingObserver, SSEObserver, MetricsObserver
from .fixtures import PreparedContext, ContextFixture, PromptFixture
from .fallback import FallbackStrategy, DialogueFallback


class Harness:
    """
    Harness 编排器：只负责组装和驱动，不含业务逻辑。

    流程：
      1. Fixture setup → 准备 PreparedContext
      2. Stage 1: Context Assembly（无重试，无 SUT 调用）
      3. 重试循环 (max_retries):
         a. Stage 2: Narrative Anchoring → narration
         b. Stage 3: Dialogue Generation → dialogues
         c. Stage 4: Choice Branching → choices
         d. Stage 5: Validation Gate → validation result
         e. 验证通过 → break；失败 → on_retry
      4. Fallback 检查（对话不足时触发三层保底）
      5. 构建 HarnessOutput
      6. Fixture teardown
    """

    def __init__(
        self,
        config: dict,
        sut: NarrativeSUT,
        stages: dict[str, StageBase],
        fallback: FallbackStrategy,
        observers: list[HarnessObserver] | None = None,
    ):
        self.config = config
        self.sut = sut
        self.stages = stages
        self.fallback = fallback
        self.observers = observers or []

    def _notify(self, method_name: str, *args, **kwargs) -> None:
        """通知所有观察者"""
        for obs in self.observers:
            method = getattr(obs, method_name, None)
            if method:
                method(*args, **kwargs)

    @staticmethod
    def _prepare_input(input_data: dict | HarnessInput) -> tuple[HarnessInput, dict]:
        """提取额外上下文并构建 HarnessInput"""
        if isinstance(input_data, dict):
            extra = {k: v for k, v in input_data.items() if k.startswith("_")}
            harness_input = HarnessInput.from_dict(input_data)
        else:
            extra = {}
            harness_input = input_data

        # 覆盖模型配置
        if harness_input.options.model:
            sut = None  # 由调用方处理
        return harness_input, extra

    def _build_prepared_context(
        self,
        harness_input: HarnessInput,
        extra: dict,
    ) -> PreparedContext:
        """Fixture: 准备上下文"""
        # ContextFixture: 构建 AssembledContext
        assembled = AssembledContext.from_input(harness_input)
        # PromptFixture: 渲染系统提示词
        rendered_sys_prompt = self.sut.render_system_prompt(extra, harness_input)
        return PreparedContext(
            assembled=assembled,
            rendered_system_prompt=rendered_sys_prompt,
            extra=extra,
        )

    def run(self, input_data: dict | HarnessInput) -> dict:
        """
        执行完整 Harness 流程（非流式），返回结果字典。
        返回格式与旧版兼容。
        """
        start_time = time.time()

        # ── 0. 准备输入 ──
        harness_input, extra = self._prepare_input(input_data)
        if harness_input.options.model:
            self.sut.llm.model = harness_input.options.model

        # ── 通知 observers: run start ──
        self._notify("on_run_start", harness_input)

        # ── 1. Fixture: 准备上下文 ──
        ctx = self._build_prepared_context(harness_input, extra)

        # ── 2. Stage 1: Context Assembly（已在 Fixture 中完成）──
        self._notify("on_stage_start", 1, "context_assembly")
        print("[Stage 1/5] 上下文组装...")

        # ── 3. 重试循环: Stage 2-5 ──
        max_retries = (
            self.config.get("quillforge", {})
            .get("stages", {})
            .get("validationGate", {})
            .get("maxRetries", 2)
        )

        narration = ""
        dialogues: list[Dialogue] = []
        choices: list[Choice] = []
        validation: Optional[ValidationResult] = None

        narrative_stage: NarrativeStage = self.stages["narrative"]
        dialogue_stage: DialogueStage = self.stages["dialogue"]
        choices_stage: ChoicesStage = self.stages["choices"]
        validation_stage: ValidationStage = self.stages["validation"]

        for attempt in range(max_retries + 1):
            if attempt > 0:
                self._notify("on_retry", attempt)
                print(f"  [RETRY] 重试第 {attempt} 次...")

            # ── Stage 2: Narrative Anchoring ──
            self._notify("on_stage_start", 2, "narrative_anchoring")
            print(f"[Stage 2/5] 叙事锚定 (attempt {attempt + 1})...")
            narration = narrative_stage.execute(ctx, self.sut, harness_input, extra)
            narration = NarrativeStage.truncate(narration, 250)
            self._notify("on_narration_complete", narration)
            self._notify("on_stage_complete", 2, narration)

            # ── Stage 3: Dialogue Generation ──
            self._notify("on_stage_start", 3, "dialogue_generation")
            print(f"[Stage 3/5] 对话生成 (attempt {attempt + 1})...")
            dialogues = dialogue_stage.execute(ctx, self.sut, harness_input, extra, narration=narration)
            dialogues = dialogues[:15]  # 硬上限15轮
            self._notify("on_stage_complete", 3, dialogues)

            # ── Stage 4: Choice Branching ──
            self._notify("on_stage_start", 4, "choice_branching")
            choices = choices_stage.execute(ctx, self.sut, harness_input, extra, narration=narration, dialogues=dialogues)
            self._notify("on_choices", choices)
            self._notify("on_stage_complete", 4, choices)

            # ── Stage 5: Validation Gate ──
            self._notify("on_stage_start", 5, "validation_gate")
            print(f"[Stage 5/5] 验证门控 (attempt {attempt + 1})...")
            validator = NarrativeValidator(
                ctx.assembled,
                min_dialogues=harness_input.options.dialogue_count_min,
                max_dialogues=harness_input.options.dialogue_count_max,
            )
            validation = validator.validate_all(narration, dialogues, choices, retry_count=attempt)
            self._notify("on_validation", validation)
            self._notify("on_stage_complete", 5, validation)

            if validation.passed:
                print("  [OK] 验证通过")
                break
            else:
                failed = [c.name for c in validation.checks if not c.passed]
                print(f"  [FAIL] 验证未通过: {failed}")

        # ── 4. Fallback 检查 ──
        min_req = harness_input.options.dialogue_count_min
        if len(dialogues) < min_req:
            print(f"  [FALLBACK] 对话不足 ({len(dialogues)}/{min_req})，启用保底生成...")
            self._notify("on_fallback", {"reason": "dialogue_insufficient", "count": len(dialogues), "min": min_req})

            state = HarnessState(
                narration=narration,
                dialogues=dialogues,
                choices=choices,
                validation=validation,
            )
            # 设置 min_count：将 dialogues 列表长度设为 min_req，以便 Fallback 读取
            # 这里通过 narration + ctx 生成保底对话
            fallback_dialogues = self.fallback.execute(ctx.assembled, self.sut, state)

            # 注意：fallback.execute 内部使用 min_count = len(state.dialogues)
            # 但我们需要 min_req，所以要修正
            # 实际上 fallback._hardcode_fallback 接受 min_count，我们直接传入 min_req
            # 让我们直接调用 fallback 的方法
            if len(fallback_dialogues) < min_req:
                # 使用硬编码保底
                fallback_dialogues = self.fallback._hardcode_fallback(ctx.assembled, min_req)

            if fallback_dialogues:
                dialogues = fallback_dialogues
                print(f"  [FALLBACK] 保底生成 {len(fallback_dialogues)} 轮对话")

            # 重新验证
            validator = NarrativeValidator(
                ctx.assembled,
                min_dialogues=min_req,
                max_dialogues=harness_input.options.dialogue_count_max,
            )
            validation = validator.validate_all(narration, dialogues, choices, retry_count=max_retries + 1)

        # ── 5. 构建 HarnessOutput ──
        elapsed = int((time.time() - start_time) * 1000)
        worldline_state = {
            "currentNode": ctx.assembled.current_node,
            "nodeIndex": ctx.assembled.node_index,
            "totalNodes": ctx.assembled.total_nodes,
            "progress": round(ctx.assembled.worldline_progress, 2),
        }

        output = HarnessOutput(
            success=validation.passed if validation else False,
            narration=narration,
            dialogues=dialogues,
            next_choices=choices,
            worldline_state=worldline_state,
            validation=validation or ValidationResult(passed=False, checks=[]),
            metadata={
                "generationTime": elapsed,
                "model": self.sut.llm.model,
            },
        )

        result = output.to_dict()
        self._notify("on_run_complete", result)
        print(f"\n{'=' * 50}")
        print(f"Pipeline 完成 | 耗时: {elapsed}ms | 验证: {'通过' if output.success else '未通过'}")
        print(f"{'=' * 50}")
        return result

    def run_stream(self, input_data: dict | HarnessInput) -> Generator[dict, None, None]:
        """
        流式 Harness 流程：逐阶段 yield 事件字典，供 SSE 推送。

        事件类型与旧版完全一致：
        stage / narration_delta / narration / dialogue / action / choices / retry / done / error
        """
        start_time = time.time()

        try:
            # ── 0. 准备输入 ──
            harness_input, extra = self._prepare_input(input_data)
            if harness_input.options.model:
                self.sut.llm.model = harness_input.options.model

            # ── 1. Fixture: 准备上下文 ──
            ctx = self._build_prepared_context(harness_input, extra)

            # ── 2. Stage 1: Context Assembly ──
            yield {"event": "stage", "stage": 1, "message": "正在组装上下文..."}
            print("[Stream Stage 1/5] 上下文组装...")

            # ── 3. 重试循环: Stage 2-5 ──
            max_retries = (
                self.config.get("quillforge", {})
                .get("stages", {})
                .get("validationGate", {})
                .get("maxRetries", 2)
            )

            narration = ""
            dialogues: list[Dialogue] = []
            choices: list[Choice] = []
            validation: Optional[ValidationResult] = None

            narrative_stage: NarrativeStage = self.stages["narrative"]
            dialogue_stage: DialogueStage = self.stages["dialogue"]
            choices_stage: ChoicesStage = self.stages["choices"]

            for attempt in range(max_retries + 1):
                if attempt > 0:
                    yield {"event": "retry", "attempt": attempt}
                    print(f"  [Stream RETRY] 重试第 {attempt} 次...")

                # ── Stage 2: Narrative Anchoring（逐字流式推送，硬截断≤250字）──
                yield {"event": "stage", "stage": 2, "message": "正在生成旁白..."}
                print(f"[Stream Stage 2/5] 叙事锚定 (attempt {attempt + 1})...")
                yield {"event": "narration_start"}
                narration = ""
                _narr_cutoff = False
                for evt in narrative_stage.execute_stream(ctx, self.sut, harness_input, extra):
                    if evt.get("event") == "narration_delta":
                        narration += evt["delta"]
                        if len(narration) <= 250:
                            yield evt
                        elif not _narr_cutoff:
                            _narr_cutoff = True
                    elif evt.get("event") == "narration":
                        # 最终完整旁白
                        pass
                narration = NarrativeStage.truncate(narration, 250)
                yield {"event": "narration", "text": narration}

                # ── Stage 3: Dialogue Generation（流式：逐条推送）──
                yield {"event": "stage", "stage": 3, "message": "正在生成对话..."}
                print(f"[Stream Stage 3/5] 对话生成 (attempt {attempt + 1})...")
                dialogues = []
                _action_count = 0
                _dlg_since_last_action = 0
                for item in dialogue_stage.execute_stream(ctx, self.sut, harness_input, extra, narration=narration):
                    if item["kind"] == "dialogue":
                        if len(dialogues) >= 15:
                            continue
                        d = item["dialogue"]
                        dialogues.append(d)
                        _dlg_since_last_action += 1
                        yield {"event": "dialogue", "speaker": d.speaker, "text": d.text, "emotion": d.emotion}
                    else:  # action 动作/心理旁白穿插
                        if _dlg_since_last_action >= 3:
                            _action_count += 1
                            _dlg_since_last_action = 0
                            yield {"event": "action", "subject": item["subject"], "text": item["text"]}

                # ── Stage 4: Choice Branching ──
                yield {"event": "stage", "stage": 4, "message": "正在生成选项..."}
                choices = choices_stage.execute(ctx, self.sut, harness_input, extra, narration=narration, dialogues=dialogues)
                yield {"event": "choices", "choices": [c.to_dict() for c in choices]}

                # ── Stage 5: Validation Gate ──
                yield {"event": "stage", "stage": 5, "message": "正在验证..."}
                print(f"[Stream Stage 5/5] 验证门控 (attempt {attempt + 1})...")
                validator = NarrativeValidator(
                    ctx.assembled,
                    min_dialogues=harness_input.options.dialogue_count_min,
                    max_dialogues=harness_input.options.dialogue_count_max,
                )
                validation = validator.validate_all(narration, dialogues, choices, retry_count=attempt)

                if validation.passed:
                    print("  [OK] 验证通过")
                    break
                else:
                    failed = [c.name for c in validation.checks if not c.passed]
                    print(f"  [FAIL] 验证未通过: {failed}")

            # ── 4. Fallback 检查 ──
            min_req = harness_input.options.dialogue_count_min
            if len(dialogues) < min_req:
                print(f"  [Stream FALLBACK] 对话不足 ({len(dialogues)}/{min_req})，启用保底生成...")
                state = HarnessState(
                    narration=narration,
                    dialogues=dialogues,
                    choices=choices,
                    validation=validation,
                )
                fallback_dialogues = self.fallback.execute(ctx.assembled, self.sut, state)
                if len(fallback_dialogues) < min_req:
                    fallback_dialogues = self.fallback._hardcode_fallback(ctx.assembled, min_req)
                if fallback_dialogues:
                    dialogues = fallback_dialogues
                    yield {"event": "retry", "attempt": max_retries + 1}
                    yield {"event": "narration", "text": narration}
                    for d in dialogues:
                        yield {"event": "dialogue", "speaker": d.speaker, "text": d.text, "emotion": d.emotion}
                    yield {"event": "choices", "choices": [c.to_dict() for c in choices]}
                validator = NarrativeValidator(
                    ctx.assembled,
                    min_dialogues=min_req,
                    max_dialogues=harness_input.options.dialogue_count_max,
                )
                validation = validator.validate_all(narration, dialogues, choices, retry_count=max_retries + 1)

            # ── 5. 构建 HarnessOutput ──
            elapsed = int((time.time() - start_time) * 1000)
            worldline_state = {
                "currentNode": ctx.assembled.current_node,
                "nodeIndex": ctx.assembled.node_index,
                "totalNodes": ctx.assembled.total_nodes,
                "progress": round(ctx.assembled.worldline_progress, 2),
            }
            output = HarnessOutput(
                success=validation.passed if validation else False,
                narration=narration,
                dialogues=dialogues,
                next_choices=choices,
                worldline_state=worldline_state,
                validation=validation or ValidationResult(passed=False, checks=[]),
                metadata={"generationTime": elapsed, "model": self.sut.llm.model},
            )
            result = output.to_dict()
            print(f"\n{'=' * 50}")
            print(f"Stream Pipeline 完成 | 耗时: {elapsed}ms | 验证: {'通过' if output.success else '未通过'}")
            print(f"{'=' * 50}")

            yield {
                "event": "done",
                "success": output.success,
                "validation": result.get("validation", {}),
                "elapsed_ms": elapsed,
                "result": result,
            }

        except Exception as e:
            yield {"event": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════

def create_default_harness(config_path: Optional[str] = None) -> Harness:
    """
    工厂函数：用默认组件组装一个标准 Harness。

    Args:
        config_path: 配置文件路径（可选，默认加载 quillforge_config.yaml）

    Returns:
        组装好的 Harness 实例
    """
    if config_path:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    else:
        config = load_config()

    # 1. 创建 SUT
    sut = NarrativeSUT(config)

    # 2. 创建 5 个 Stage
    stages = {
        "context_assembly": ContextAssemblyStage(),
        "narrative": NarrativeStage(),
        "dialogue": DialogueStage(),
        "choices": ChoicesStage(),
        "validation": ValidationStage(),
    }

    # 3. 创建 Fallback
    fallback = DialogueFallback()

    # 4. 创建默认 Observers（LoggingObserver + MetricsObserver）
    observers: list[HarnessObserver] = [
        LoggingObserver(),
        MetricsObserver(),
    ]

    # 5. 组装 Harness
    return Harness(
        config=config,
        sut=sut,
        stages=stages,
        fallback=fallback,
        observers=observers,
    )
