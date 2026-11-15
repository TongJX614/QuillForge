# -*- coding: utf-8 -*-
"""
Stage 3: Dialogue Generation — 对话生成

调用 SUT 生成角色对话，包含 speaker 归一化和非角色过滤逻辑。
从原 harness.py 的 _stage_dialogue / _stage_dialogue_stream / _build_speaker_map 提取。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Generator

_SRC_DIR = str(Path(__file__).resolve().parent.parent.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from logger import get_logger
from schemas import HarnessInput, AssembledContext, Dialogue, Choice
from ..fixtures import PreparedContext
from ..sut import NarrativeSUT
from .base import StageBase

logger = get_logger("stage.dialogue")


class DialogueStage(StageBase):
    """Stage 3: 生成角色对话（并在同一次 LLM 调用末尾附带产出玩家选项）"""

    @property
    def name(self) -> str:
        return "dialogue_generation"

    @property
    def stage_number(self) -> int:
        return 3

    def execute(
        self,
        ctx: PreparedContext,
        sut: NarrativeSUT,
        input_data: HarnessInput,
        extra: dict,
        narration: str = "",
        feedback: str = "",
    ) -> tuple[list[Dialogue], list[Choice]]:
        """非流式生成对话，返回 (dialogues, merged_choices)。

        merged_choices 是同一 LLM 输出末尾附带的选项（type=choices 对象），
        可能为空列表；为空时由调用方回退到独立的 ChoicesStage 调用。
        """
        user_prompt = self._build_prompt(ctx.assembled, input_data, extra, narration, sut, feedback=feedback)
        dialogues: list[Dialogue] = []
        merged_choices: list[Choice] = []

        try:
            raw_dialogues = sut.call_json(ctx.rendered_system_prompt, user_prompt,
                                          model=input_data.options.model or "")
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            logger.info("  [DIALOGUE] LLM JSON 解析失败: %s", e)
            return dialogues, merged_choices

        if isinstance(raw_dialogues, list):
            valid_speakers = {c.name for c in ctx.assembled.active_characters}
            speaker_map = self._build_speaker_map(ctx.assembled.active_characters)
            filtered_count = 0
            for d in raw_dialogues:
                if not isinstance(d, dict):
                    continue
                # 对话末尾附带的选项对象
                if str(d.get("type", "")).strip().lower() == "choices":
                    for c in d.get("choices", []):
                        if isinstance(c, dict):
                            merged_choices.append(Choice(
                                text=c.get("text", ""),
                                effect=c.get("effect", ""),
                                worldline_impact=c.get("worldlineImpact", "advance"),
                            ))
                    continue
                speaker = d.get("speaker", "").strip()
                speaker = speaker_map.get(speaker, speaker)
                speaker = speaker_map.get(speaker.lower(), speaker)
                if speaker not in valid_speakers:
                    filtered_count += 1
                    logger.debug("  [FILTER] 过滤 speaker: '%s' (合法: %s)", speaker, valid_speakers)
                    continue
                dialogues.append(Dialogue(
                    speaker=speaker,
                    text=d.get("text", ""),
                    emotion=d.get("emotion", ""),
                ))
            logger.info("  [DIALOGUE] LLM生成 %d 轮, 过滤 %d 轮, 保留 %d 轮, 附带选项 %d 个",
                        len(raw_dialogues), filtered_count, len(dialogues), len(merged_choices))
        else:
            logger.warning("  [DIALOGUE] LLM返回非列表类型: %s", type(raw_dialogues).__name__)

        return dialogues, merged_choices

    def execute_stream(
        self,
        ctx: PreparedContext,
        sut: NarrativeSUT,
        input_data: HarnessInput,
        extra: dict,
        narration: str = "",
        feedback: str = "",
    ) -> Generator[dict, None, list[Dialogue]]:
        """流式生成对话 + 穿插动作/心理旁白，逐个 yield 字典项。

        产出项：{"kind": "dialogue"|"action"|"choices", ...}；
        choices 项在 LLM 输出末尾附带选项时最后产出一次。
        """
        user_prompt = self._build_prompt(ctx.assembled, input_data, extra, narration, sut, feedback=feedback)
        valid_speakers = {c.name for c in ctx.assembled.active_characters}
        speaker_map = self._build_speaker_map(ctx.assembled.active_characters)

        dialogues: list[Dialogue] = []
        kept = 0
        filtered_count = 0
        action_count = 0

        for d in sut.call_json_stream(ctx.rendered_system_prompt, user_prompt,
                                      model=input_data.options.model or ""):
            if not isinstance(d, dict):
                continue
            text = str(d.get("text", "")).strip()
            dtype = str(d.get("type", "")).strip().lower()

            # 对话末尾附带的选项对象
            if dtype == "choices":
                merged_choices = []
                for c in d.get("choices", []):
                    if isinstance(c, dict):
                        merged_choices.append(Choice(
                            text=c.get("text", ""),
                            effect=c.get("effect", ""),
                            worldline_impact=c.get("worldlineImpact", "advance"),
                        ))
                if merged_choices:
                    yield {"kind": "choices", "choices": merged_choices}
                continue

            speaker = str(d.get("speaker", "")).strip()
            speaker = speaker_map.get(speaker, speaker)
            speaker = speaker_map.get(speaker.lower(), speaker)

            # 动作/神态/心理旁白穿插项
            if dtype in ("action", "narration", "beat") or (not speaker and text):
                if not text:
                    continue
                subject = str(d.get("subject", "") or speaker).strip()
                subject = speaker_map.get(subject, subject)
                subject = speaker_map.get(subject.lower(), subject)
                if subject not in valid_speakers:
                    subject = ""
                action_count += 1
                yield {"kind": "action", "subject": subject, "text": text}
                continue

            if speaker not in valid_speakers:
                filtered_count += 1
                logger.debug("  [FILTER] 过滤 speaker: '%s' (合法: %s)", speaker, valid_speakers)
                continue
            kept += 1
            dialogue = Dialogue(
                speaker=speaker,
                text=d.get("text", ""),
                emotion=d.get("emotion", ""),
            )
            dialogues.append(dialogue)
            yield {"kind": "dialogue", "dialogue": dialogue}

        logger.info("  [DIALOGUE-STREAM] 对话 %d 轮, 动作旁白 %d 条, 过滤 %d 轮", kept, action_count, filtered_count)
        return dialogues

    def _build_prompt(
        self,
        assembled: AssembledContext,
        inp: HarnessInput,
        extra: dict,
        narration: str,
        sut: NarrativeSUT,
        feedback: str = "",
    ) -> str:
        """构建对话生成的 user prompt"""
        opts = inp.options
        variables = self._common_variables(extra)
        variables.update({
            "narration": narration,
            "characters": assembled.active_characters,
            "annotatedWorldline": assembled.annotated_worldline,
            "worldline": assembled.raw_worldline,
            "currentNode": assembled.current_node,
            "nextNode": assembled.next_node or "（终局）",
            "worldlineProgress": int(assembled.worldline_progress * 100),
            "playerDecision": assembled.player_decision,
            "dialogueCount": (opts.dialogue_count_min + opts.dialogue_count_max) // 2,
            "minDialogues": opts.dialogue_count_min,
            "maxDialogues": opts.dialogue_count_max,
            "previousDialogues": inp.previous_context.dialogues if inp.previous_context else [],
            "retryFeedback": feedback,
        })
        return sut.render_template(sut.prompts["dialogue"], variables)

    @staticmethod
    def _build_speaker_map(characters: list) -> dict:
        """构建 speaker 归一化映射：角色ID/小写名/拼音等 → 角色中文名"""
        speaker_map = {}
        for c in characters:
            speaker_map[c.name] = c.name
            speaker_map[c.name.lower()] = c.name
            if c.id:
                speaker_map[c.id] = c.name
                speaker_map[c.id.lower()] = c.name
                speaker_map[c.id.replace("_", "")] = c.name
                speaker_map[c.id.replace("_", "").lower()] = c.name
        return speaker_map
