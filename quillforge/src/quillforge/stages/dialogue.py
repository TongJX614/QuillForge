# -*- coding: utf-8 -*-
"""
Stage 3: Dialogue Generation — 对话生成

调用 SUT 生成角色对话，包含 speaker 归一化和非角色过滤逻辑。
从原 harness.py 的 _stage_dialogue / _stage_dialogue_stream / _build_speaker_map 提取。
"""

from __future__ import annotations

from typing import Generator

from schemas import HarnessInput, AssembledContext, Dialogue
from ..fixtures import PreparedContext
from ..sut import NarrativeSUT
from .base import StageBase


class DialogueStage(StageBase):
    """Stage 3: 生成角色对话"""

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
    ) -> list[Dialogue]:
        """非流式生成对话"""
        user_prompt = self._build_prompt(ctx.assembled, input_data, extra, narration, sut)
        dialogues: list[Dialogue] = []

        try:
            raw_dialogues = sut.call_json(ctx.rendered_system_prompt, user_prompt)
        except (ValueError, Exception) as e:
            print(f"  [DIALOGUE] LLM JSON 解析失败: {e}")
            return dialogues

        if isinstance(raw_dialogues, list):
            valid_speakers = {c.name for c in ctx.assembled.active_characters}
            speaker_map = self._build_speaker_map(ctx.assembled.active_characters)
            filtered_count = 0
            for d in raw_dialogues:
                if isinstance(d, dict):
                    speaker = d.get("speaker", "").strip()
                    speaker = speaker_map.get(speaker, speaker)
                    speaker = speaker_map.get(speaker.lower(), speaker)
                    if speaker not in valid_speakers:
                        filtered_count += 1
                        print(f"  [FILTER] 过滤 speaker: '{speaker}' (合法: {valid_speakers})")
                        continue
                    dialogues.append(Dialogue(
                        speaker=speaker,
                        text=d.get("text", ""),
                        emotion=d.get("emotion", ""),
                    ))
            print(f"  [DIALOGUE] LLM生成 {len(raw_dialogues)} 轮, 过滤 {filtered_count} 轮, 保留 {len(dialogues)} 轮")
        else:
            print(f"  [DIALOGUE] LLM返回非列表类型: {type(raw_dialogues).__name__}")

        return dialogues

    def execute_stream(
        self,
        ctx: PreparedContext,
        sut: NarrativeSUT,
        input_data: HarnessInput,
        extra: dict,
        narration: str = "",
    ) -> Generator[dict, None, list[Dialogue]]:
        """流式生成对话 + 穿插动作/心理旁白，逐个 yield 字典项"""
        user_prompt = self._build_prompt(ctx.assembled, input_data, extra, narration, sut)
        valid_speakers = {c.name for c in ctx.assembled.active_characters}
        speaker_map = self._build_speaker_map(ctx.assembled.active_characters)

        dialogues: list[Dialogue] = []
        kept = 0
        filtered_count = 0
        action_count = 0

        for d in sut.call_json_stream(ctx.rendered_system_prompt, user_prompt):
            if not isinstance(d, dict):
                continue
            text = str(d.get("text", "")).strip()
            dtype = str(d.get("type", "")).strip().lower()
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
                print(f"  [FILTER] 过滤 speaker: '{speaker}' (合法: {valid_speakers})")
                continue
            kept += 1
            dialogue = Dialogue(
                speaker=speaker,
                text=d.get("text", ""),
                emotion=d.get("emotion", ""),
            )
            dialogues.append(dialogue)
            yield {"kind": "dialogue", "dialogue": dialogue}

        print(f"  [DIALOGUE-STREAM] 对话 {kept} 轮, 动作旁白 {action_count} 条, 过滤 {filtered_count} 轮")
        return dialogues

    def _build_prompt(
        self,
        assembled: AssembledContext,
        inp: HarnessInput,
        extra: dict,
        narration: str,
        sut: NarrativeSUT,
    ) -> str:
        """构建对话生成的 user prompt"""
        opts = inp.options
        variables = {
            "narration": narration,
            "characters": assembled.active_characters,
            "annotatedWorldline": assembled.annotated_worldline,
            "currentNode": assembled.current_node,
            "nextNode": assembled.next_node or "（终局）",
            "worldlineProgress": int(assembled.worldline_progress * 100),
            "playerDecision": assembled.player_decision,
            "choiceHistory": extra.get("_choiceHistory", ""),
            "dialogueCount": (opts.dialogue_count_min + opts.dialogue_count_max) // 2,
            "minDialogues": opts.dialogue_count_min,
            "maxDialogues": opts.dialogue_count_max,
            "previousDialogues": inp.previous_context.dialogues if inp.previous_context else [],
            # 剧本全局上下文
            "events": extra.get("_events", ""),
            "stagesOverview": extra.get("_stagesOverview", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
            "sceneDetails": extra.get("_sceneDetails", ""),
            "worldlines": extra.get("_worldlines", ""),
            "endings": extra.get("_endings", ""),
            "keyChoices": extra.get("_keyChoices", ""),
            "themes": extra.get("_themes", ""),
            "worldbookRules": extra.get("_worldbook", ""),
            "plotSummary": extra.get("_plotSummary", ""),
            # 当前场景专属剧情上下文
            "currentStageContext": extra.get("_currentStageContext", ""),
            "sceneBeats": extra.get("_sceneBeats", ""),
            "sceneHooks": extra.get("_sceneHooks", ""),
        }
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
