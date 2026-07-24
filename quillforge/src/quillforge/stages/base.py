# -*- coding: utf-8 -*-
"""
Stage Base — 所有 Pipeline 阶段的统一接口

每个 Stage 是一个独立的、可替换的组件。
Harness 编排器按顺序调用各 Stage 的 execute / execute_stream 方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from ..sut import NarrativeSUT
    from ..fixtures import PreparedContext
    from schemas import HarnessInput


class StageBase(ABC):
    """Pipeline 阶段抽象基类

    子类必须实现：
      - name: 阶段名称（如 "context_assembly"）
      - stage_number: 阶段编号（1-5）
      - execute(): 非流式执行，返回阶段产物

    可选覆盖：
      - execute_stream(): 流式执行，yield 事件字典。
        默认实现回退到 execute()，不支持增量推送。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """阶段名称（如 'narrative_anchoring'）"""
        ...

    @property
    @abstractmethod
    def stage_number(self) -> int:
        """阶段编号（1-5）"""
        ...

    @abstractmethod
    def execute(
        self,
        ctx: "PreparedContext",
        sut: "NarrativeSUT",
        input_data: "HarnessInput",
        extra: dict,
    ) -> Any:
        """
        非流式执行阶段逻辑。

        Args:
            ctx: PreparedContext（含 AssembledContext + 渲染后的 system prompt）
            sut: System Under Test（LLM + Prompt 渲染器）
            input_data: 原始 HarnessInput
            extra: 额外上下文（世界书、叙事风格等，键以 _ 开头）

        Returns:
            阶段产物（str / list[Dialogue] / list[Choice] / ValidationResult）
        """
        ...

    def execute_stream(
        self,
        ctx: "PreparedContext",
        sut: "NarrativeSUT",
        input_data: "HarnessInput",
        extra: dict,
    ) -> Generator[dict, None, Any]:
        """
        流式执行阶段逻辑，yield 事件字典。

        默认实现：调用 execute() 并包装为单个事件。
        子类可覆盖以支持增量推送（如旁白逐字、对话逐条）。
        """
        result = self.execute(ctx, sut, input_data, extra)
        yield {"event": "stage_result", "stage": self.stage_number, "result": result}
