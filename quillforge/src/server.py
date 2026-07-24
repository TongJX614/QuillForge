# -*- coding: utf-8 -*-
"""
QuillForge — 通用 FastAPI 后端
支持上传任意剧本文件，通用解析，交互式生成

状态机：
  IDLE → (generate) → GENERATED → (choose) → CHOSEN → (generate) → ...
"""

from __future__ import annotations

import io
import sys
import os
import uuid
import json
import time
import tempfile
import shutil
import concurrent.futures
from enum import Enum
from pathlib import Path
from typing import Optional, Any

if sys.platform == "win32":
    try:
        # 只在有真实终端 buffer 时才包装（跳过 pytest/unittest 环境）
        if hasattr(sys.stdout, 'buffer') and hasattr(sys.stdout.buffer, 'raw'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generic_adapter import (
    GenericScriptAdapter,
    load_story, get_scene_characters, build_worldline, build_scene_text,
)
from quillforge import Harness, create_default_harness
from schemas import HarnessInput
from image_gen import (
    ImageGenerator, build_character_prompt, build_background_prompt,
    slugify, IMAGES_DIR, DEFAULT_BG_SIZE, DEFAULT_CHAR_SIZE,
)
from tts_gen import TTSGenerator, AUDIO_DIR
from script_generator import generate_script
from realtime_engine import RealtimeStorySession

# 美术素材并行生成并发数（Seedream IPM 充足，6 路并发兼顾速度与稳定；可经 IMAGE_CONCURRENCY 调整）
IMAGE_CONCURRENCY = int(os.getenv("IMAGE_CONCURRENCY", "6"))
# 开局优先预加载的场景数（这些场景的背景 + 出场角色立绘先生成，剩余素材后台补）
IMAGE_BG_PRELOAD_COUNT = int(os.getenv("IMAGE_BG_PRELOAD_COUNT", "2"))


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def _extract_worldbook_rules(worldbook: str) -> str:
    """从世界书提取关键约束规则（含世界背景、历史信息）"""
    if not worldbook:
        return ""
    import re
    rules = []
    # 约束类章节
    for section in ["必须遵守", "禁止出现", "情感基调", "故事边界"]:
        match = re.search(rf'###?\s*{section}\s*\n(.*?)(?=###?|\Z)', worldbook, re.DOTALL)
        if match:
            rules.append(f"【{section}】{match.group(1).strip()}")
    # 设定类章节（含历史背景）
    for section in ["世界背景", "核心科技", "组织设定", "社会规则", "历史背景", "时间线", "大事记"]:
        match = re.search(rf'##?\s*{section}\s*\n(.*?)(?=##?|\Z)', worldbook, re.DOTALL)
        if match:
            content = match.group(1).strip()
            if len(content) > 800:
                content = content[:800] + "…"
            rules.append(f"【{section}】{content}")
    return "\n\n".join(rules) if rules else worldbook[:500]


def _get_current_stage(stages: list, scene_id: str) -> dict | None:
    """找到当前场景所属的剧情阶段"""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        scenes = stage.get("scenes", [])
        if isinstance(scenes, list) and scene_id in scenes:
            return stage
        if isinstance(scenes, str) and scenes == scene_id:
            return stage
    return None


def _format_current_stage_context(stage: dict | None) -> str:
    """格式化当前阶段的剧情要求（关键事件+情感弧线）"""
    if not stage:
        return ""
    parts = []
    name = stage.get("name", "")
    desc = str(stage.get("description", "")).strip()
    if name:
        parts.append(f"当前阶段：{name}")
    if desc:
        parts.append(f"阶段目标：{desc}")
    key_events = stage.get("key_events", []) or []
    if key_events:
        events = []
        for e in key_events:
            if isinstance(e, dict):
                events.append(str(e.get("name", e.get("description", "")))[:40])
            else:
                events.append(str(e)[:40])
        parts.append("本场景必须体现的关键事件：")
        for i, ev in enumerate(events, 1):
            parts.append(f"  {i}. {ev}")
    emotional_arc = str(stage.get("emotional_arc", "")).strip()
    if emotional_arc:
        parts.append(f"情感弧线走向：{emotional_arc}")
    return "\n".join(parts)


def _format_beats_for_scene(beats: list, scene_id: str) -> str:
    """格式化当前场景的剧情节拍"""
    if not beats:
        return ""
    relevant = [b for b in beats if isinstance(b, dict) and b.get("scene") == scene_id]
    if not relevant:
        return ""
    parts = []
    for b in relevant:
        name = b.get("name", "")
        desc = str(b.get("description", "")).strip()[:80]
        purpose = str(b.get("purpose", "")).strip()[:60]
        line = f"● {name}"
        if desc:
            line += f"：{desc}"
        if purpose:
            line += f"（叙事目的：{purpose}）"
        parts.append(line)
    return "\n".join(parts)


def _format_hooks_for_scene(hooks: list, scene_id: str) -> str:
    """格式化当前场景的剧情钩子"""
    if not hooks:
        return ""
    relevant = [h for h in hooks if isinstance(h, dict) and h.get("scene") == scene_id]
    if not relevant:
        return ""
    parts = []
    for h in relevant:
        htype = h.get("type", "")
        desc = str(h.get("description", "")).strip()[:80]
        parts.append(f"[{htype}] {desc}")
    return "\n".join(parts)


def _format_themes(themes: list) -> str:
    """格式化故事主题"""
    if not themes:
        return ""
    return "；".join(str(t)[:50] for t in themes[:5])


def _format_relationships(rels: list) -> str:
    """格式化关系网络为可读字符串"""
    if not rels:
        return ""
    parts = []
    for r in rels[:6]:
        if isinstance(r, dict):
            src = r.get("source", "")
            tgt = r.get("target", "")
            rtype = r.get("type", "")
            desc = str(r.get("description", "")).strip()[:80]
            parts.append(f"{src} ↔ {tgt}（{rtype}）：{desc}")
    return "\n".join(parts)


def _format_stages_overview(stages: list) -> str:
    """格式化主线剧情阶段为时间线概览"""
    if not stages:
        return ""
    parts = []
    for i, s in enumerate(stages):
        if isinstance(s, dict):
            name = s.get("name", f"阶段{i+1}")
            # 兼容 scenes(list) 和 scene(str)
            scenes_val = s.get("scenes", s.get("scene", ""))
            scene_str = ", ".join(str(x) for x in scenes_val) if isinstance(scenes_val, list) else str(scenes_val)
            desc = str(s.get("description", s.get("summary", ""))).strip()[:80]
            key_events = s.get("key_events", s.get("keyEvents", [])) or []
            # 兼容字符串列表和字典列表
            events_str = "；".join(
                (str(e.get("name", e))[:20] if isinstance(e, dict) else str(e)[:20])
                for e in key_events[:3]
            ) if key_events else ""
            emotional_arc = str(s.get("emotional_arc", s.get("emotionalArc", ""))).strip()
            line = f"{'→' if i > 0 else '●'} {name}"
            if scene_str:
                line += f"（场景：{scene_str}）"
            if desc:
                line += f" — {desc}"
            if events_str:
                line += f"\n   关键事件：{events_str}"
            if emotional_arc:
                line += f"\n   情感弧线：{emotional_arc}"
            parts.append(line)
    return "\n".join(parts)


def _format_events(events: list) -> str:
    """格式化事件清单为可读字符串"""
    if not events:
        return ""
    parts = []
    for e in events[:8]:
        if isinstance(e, dict):
            name = e.get("name", e.get("title", ""))
            # 触发条件: 兼容 trigger_conditions(list) / triggerCondition(str) / trigger_stage(str)
            tc_raw = e.get("trigger_conditions", e.get("triggerCondition", e.get("trigger_condition", "")))
            if isinstance(tc_raw, list):
                trigger_cond = "；".join(
                    str(c.get("value", c.get("type", c))).strip()[:30]
                    for c in tc_raw[:3]
                )
            else:
                trigger_cond = str(tc_raw).strip()[:60]
            trigger_stage = e.get("triggerStage", e.get("trigger_stage", ""))
            content = str(e.get("content", e.get("description", ""))).strip()[:100]
            outcome = str(e.get("outcome", e.get("result", ""))).strip()[:60]
            chars_involved = e.get("characters_involved", [])
            line = f"【{name}】"
            if trigger_stage:
                line += f" 触发于「{trigger_stage}」"
            if trigger_cond:
                line += f"\n  触发条件：{trigger_cond}"
            if chars_involved:
                line += f"\n  涉及角色：{', '.join(str(c) for c in chars_involved[:3])}"
            if content:
                line += f"\n  内容：{content}"
            if outcome:
                line += f"\n  结果：{outcome}"
            parts.append(line)
    return "\n\n".join(parts)


def _format_key_choices(choices: list) -> str:
    """格式化关键选择为可读字符串（含后果/性格影响/关系变化）"""
    if not choices:
        return ""
    parts = []
    for c in choices[:6]:
        if isinstance(c, dict):
            # 兼容多种字段名: name/title/description, scene_id/position/scene, prompt
            name = c.get("name", c.get("title", c.get("prompt", "")))
            if not name:
                name = str(c.get("description", ""))[:60]
            position = c.get("scene_id", c.get("position", c.get("scene", "")))
            desc = str(c.get("description", "")).strip()[:80]
            opts = c.get("options", []) or []
            option_strs = []
            for o in opts[:4]:
                if isinstance(o, dict):
                    text = str(o.get("text", o.get("label", ""))).strip()[:40]
                    opt_desc = str(o.get("description", "")).strip()[:50]
                    # 后果: 兼容 consequences(dict)/consequence(str)/result(str)
                    cons_raw = o.get("consequences", o.get("consequence", o.get("result", "")))
                    if isinstance(cons_raw, dict):
                        immediate = str(cons_raw.get("immediate", "")).strip()[:60]
                        delayed = str(cons_raw.get("delayed", "")).strip()[:60]
                        consequence = immediate
                    else:
                        consequence = str(cons_raw).strip()[:60]
                    # 性格影响
                    personality = str(o.get("personality_impact", "")).strip()[:40]
                    # 关系变化
                    rel_changes = o.get("relationship_changes", [])
                    rel_str = ""
                    if isinstance(rel_changes, list) and rel_changes:
                        rel_items = []
                        for r in rel_changes[:2]:
                            if isinstance(r, dict):
                                ch = r.get("character", "")
                                val = r.get("change", 0)
                                reason = str(r.get("reason", ""))[:30]
                                rel_items.append(f"{ch}{'↑' if val > 0 else '↓'}{reason}")
                        rel_str = "；".join(rel_items)
                    # 导向结局
                    leads_to = o.get("leads_to_ending", "")
                    # 组装选项文本
                    opt = f"「{text}」"
                    if opt_desc:
                        opt += f" — {opt_desc}"
                    if consequence:
                        opt += f"\n     即时后果：{consequence}"
                    if personality:
                        opt += f"\n     性格体现：{personality}"
                    if rel_str:
                        opt += f"\n     关系变化：{rel_str}"
                    if leads_to:
                        opt += f"\n     导向结局：{leads_to}"
                    option_strs.append(opt)
            line = f"【{name}】"
            if position:
                line += f"（场景：{position}）"
            if desc and desc != name:
                line += f"\n  {desc}"
            if option_strs:
                line += "\n  " + "\n  ".join(option_strs)
            parts.append(line)
    return "\n\n".join(parts)


def _format_endings(endings: list) -> str:
    """格式化结局系统为可读字符串"""
    if not endings:
        return ""
    parts = []
    for e in endings[:5]:
        if isinstance(e, dict):
            name = e.get("title", e.get("name", ""))
            etype = e.get("type", "")
            # 兼容 trigger_conditions(list) 和 condition(str)
            cond_val = e.get("trigger_conditions", e.get("condition", ""))
            if isinstance(cond_val, list):
                condition = "；".join(
                    str(c.get("value", c))[:30] for c in cond_val[:3]
                ) if cond_val else ""
            else:
                condition = str(cond_val).strip()[:60]
            narrative = str(e.get("narrative", "")).strip()[:80]
            epilogue = str(e.get("epilogue", "")).strip()[:60]
            replay = str(e.get("replay_value", "")).strip()[:60]
            line = f"结局「{name}」（{etype}）"
            if condition:
                line += f"\n  达成条件：{condition}"
            if narrative:
                line += f"\n  叙事：{narrative}"
            if epilogue:
                line += f"\n  尾声：{epilogue}"
            if replay:
                line += f"\n  价值：{replay}"
            parts.append(line)
    return "\n\n".join(parts)


def _format_worldlines(world_lines: list, convergence_points: list = None, rules: list = None) -> str:
    """格式化世界线结构（含分支路径、汇聚点、规则）"""
    if not world_lines:
        return ""
    parts = []
    for wl in world_lines[:4]:
        if isinstance(wl, dict):
            name = wl.get("name", wl.get("id", ""))
            desc = str(wl.get("description", "")).strip()[:80]
            # 路径
            path = wl.get("path", [])
            path_strs = []
            for p in sorted(path, key=lambda x: x.get("sequence", 0)):
                if isinstance(p, dict):
                    sid = p.get("scene_id", "")
                    pdesc = str(p.get("description", "")).strip()[:30]
                    path_strs.append(f"{sid}({pdesc})" if pdesc else sid)
            path_line = " → ".join(path_strs) if path_strs else ""
            # 条件
            conditions = wl.get("conditions", [])
            cond_str = ""
            if isinstance(conditions, list) and conditions:
                cond_str = "；".join(
                    str(c.get("value", c))[:30] for c in conditions[:2]
                )
            # 后果
            consequences = str(wl.get("consequences", "")).strip()[:80]
            # 结局
            end_ending = wl.get("end结局", wl.get("end_ending", ""))

            line = f"● {name}"
            if desc:
                line += f" — {desc}"
            if path_line:
                line += f"\n  路径：{path_line}"
            if cond_str:
                line += f"\n  触发条件：{cond_str}"
            if consequences:
                line += f"\n  后果：{consequences}"
            if end_ending:
                line += f"\n  结局：{end_ending}"
            parts.append(line)

    # 汇聚点
    if convergence_points:
        conv_strs = []
        for cp in convergence_points[:3]:
            if isinstance(cp, dict):
                sid = cp.get("scene_id", "")
                desc = str(cp.get("description", "")).strip()[:50]
                conv_strs.append(f"{sid}: {desc}" if desc else sid)
        if conv_strs:
            parts.append("\n【汇聚点】" + "；".join(conv_strs))

    # 规则
    if rules:
        rule_strs = []
        for r in rules[:4]:
            if isinstance(r, dict):
                rname = r.get("name", r.get("rule_id", ""))
                rdesc = str(r.get("description", "")).strip()[:60]
                cond = str(r.get("condition", "")).strip()[:40]
                effect = str(r.get("effect", "")).strip()[:40]
                rule_strs.append(f"{rname}: {rdesc}（条件：{cond} → {effect}）")
        if rule_strs:
            parts.append("\n【世界线规则】" + "\n  " + "\n  ".join(rule_strs))

    return "\n\n".join(parts)


def _extract_script_choices(scene: dict, key_choices: list) -> list[dict]:
    """从场景数据中提取剧本定义的选项（非 LLM 生成）
    
    优先级（key_choices 含最完整数据，优先查找）：
    1. critical_choice → key_choices/choices[].options（含 consequences/unlock_conditions）
    2. interactions[].options[]
    3. 顶层 choices
    """
    raw = []
    # 1. 优先通过 critical_choice 从 key_choices 匹配
    cid = scene.get("critical_choice", "")
    if cid:
        for kc in (key_choices if isinstance(key_choices, list) else []):
            if isinstance(kc, dict) and kc.get("id") == cid:
                raw = kc.get("options", [])
                break
    # 2. interactions[].options[]
    if not raw:
        for interaction in (scene.get("interactions", []) or []):
            if isinstance(interaction, dict):
                opts = interaction.get("options", [])
                if opts and isinstance(opts, list):
                    raw = raw + opts
    # 3. 顶层 choices
    if not raw:
        raw = scene.get("choices", [])
    
    result = []
    for c in (raw if isinstance(raw, list) else []):
        if isinstance(c, dict):
            consequences = c.get("consequences", {})
            if isinstance(consequences, dict):
                effect = consequences.get("immediate", consequences.get("delayed", ""))
            else:
                effect = str(consequences) if consequences else ""
            result.append({
                "text": c.get("text", ""),
                "effect": effect or c.get("description", ""),
            })
    return result


def _format_scene_details(scene: dict, key_choices: list = None) -> str:
    """格式化当前场景的详细信息（叙事笔记、互动、关键选择）"""
    if not scene:
        return ""
    parts = []

    # 氛围
    atmosphere = str(scene.get("atmosphere", "")).strip()
    if atmosphere:
        parts.append(f"氛围：{atmosphere}")

    # 叙事笔记（导演指导）
    notes = str(scene.get("narrative_notes", "")).strip()
    if notes:
        parts.append(f"叙事指导：{notes}")

    # 在场角色
    chars = scene.get("characters_present", [])
    if chars:
        parts.append(f"在场角色：{', '.join(str(c) for c in chars)}")

    # 互动
    interactions = scene.get("interactions", [])
    if isinstance(interactions, list) and interactions:
        for inter in interactions[:2]:
            if isinstance(inter, dict):
                itype = inter.get("type", "")
                idesc = str(inter.get("description", "")).strip()[:60]
                iprompt = str(inter.get("prompt", "")).strip()[:80]
                line = f"互动（{itype}）：{idesc}"
                if iprompt:
                    line += f"\n  提示：{iprompt}"
                # 互动选项
                iopts = inter.get("options", [])
                if isinstance(iopts, list) and iopts:
                    opt_strs = []
                    for o in iopts[:4]:
                        if isinstance(o, dict):
                            txt = str(o.get("text", "")).strip()[:30]
                            odesc = str(o.get("description", "")).strip()[:40]
                            opt_strs.append(f"「{txt}」{odesc}")
                    if opt_strs:
                        line += "\n  可选行动：" + "；".join(opt_strs)
                parts.append(line)

    # 分支点
    if scene.get("branching_point"):
        parts.append("⚠ 本场景是分支决策点，选择将影响后续走向")

    # 关联的关键选择
    critical_choice_id = scene.get("critical_choice")
    if critical_choice_id and key_choices:
        for kc in key_choices:
            if isinstance(kc, dict) and kc.get("id") == critical_choice_id:
                prompt = str(kc.get("prompt", "")).strip()
                if prompt:
                    parts.append(f"本场景关键抉择：{prompt}")
                break

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════

class SessionState(str, Enum):
    IDLE = "idle"
    GENERATED = "generated"
    CHOSEN = "chosen"
    FINISHED = "finished"


class ChoiceRequest(BaseModel):
    choice_text: str
    choice_index: int


class GameSession:
    """游戏会话"""

    def __init__(self, adapter: GenericScriptAdapter, worldline_id: str = "main"):
        self.session_id = str(uuid.uuid4())[:8]
        self.adapter = adapter
        self.state = SessionState.IDLE
        self.current_scene_index = 0
        self.history: list[dict] = []
        self.previous_result: Optional[dict] = None
        self.current_result: Optional[dict] = None

    @property
    def total_scenes(self) -> int:
        return len(self.adapter.scenes) if self.adapter.scenes else len(self.adapter.worldline_nodes)

    @property
    def progress(self) -> float:
        total = self.total_scenes
        return self.current_scene_index / total if total > 0 else 1.0

    def generate(self, harness: Harness) -> dict:
        if self.state == SessionState.GENERATED:
            raise HTTPException(400, "当前场景已生成，请先做出选择后才能继续")
        if self.state == SessionState.FINISHED:
            raise HTTPException(400, "故事已结束")

        if self.current_scene_index >= self.total_scenes:
            raise HTTPException(400, "没有更多场景")

        player_choice = ""
        if self.history:
            player_choice = self.history[-1].get("player_choice", "")

        harness_input = self.adapter.build_harness_input(
            self.current_scene_index,
            player_choice=player_choice,
            previous_context=self.previous_result.get("data") if self.previous_result else None,
            choice_history=self.history or None,
        )

        result = harness.run(harness_input)
        self.current_result = result
        self.state = SessionState.GENERATED

        scene = self.adapter.scenes[self.current_scene_index] if self.current_scene_index < len(self.adapter.scenes) else {}
        script_choices = self.adapter.get_scene_choices(self.current_scene_index, choice_history=self.history or None)

        return {
            **result,
            "scene_meta": {
                "scene_id": scene.get("id", ""),
                "scene_name": scene.get("name", f"场景 {self.current_scene_index + 1}"),
                "location": scene.get("location", ""),
                "scene_index": self.current_scene_index + 1,
                "total_scenes": self.total_scenes,
            },
            "available_choices": script_choices,
            "session_state": self.state.value,
            "progress": round(self.progress, 2),
        }

    def generate_stream(self, harness: Harness):
        """流式生成：逐阶段 yield SSE 事件，完成后更新会话状态"""
        if self.state == SessionState.GENERATED:
            yield {"event": "error", "message": "当前场景已生成，请先做出选择后才能继续"}
            return
        if self.state == SessionState.FINISHED:
            yield {"event": "error", "message": "故事已结束"}
            return
        if self.current_scene_index >= self.total_scenes:
            yield {"event": "error", "message": "没有更多场景"}
            return

        player_choice = ""
        if self.history:
            player_choice = self.history[-1].get("player_choice", "")

        harness_input = self.adapter.build_harness_input(
            self.current_scene_index,
            player_choice=player_choice,
            previous_context=self.previous_result.get("data") if self.previous_result else None,
            choice_history=self.history or None,
        )

        scene = self.adapter.scenes[self.current_scene_index] if self.current_scene_index < len(self.adapter.scenes) else {}
        script_choices = self.adapter.get_scene_choices(self.current_scene_index, choice_history=self.history or None)

        def _meta_event() -> dict:
            return {
                "event": "meta",
                "scene_meta": {
                    "scene_id": scene.get("id", ""),
                    "scene_name": scene.get("name", f"场景 {self.current_scene_index + 1}"),
                    "location": scene.get("location", ""),
                    "scene_index": self.current_scene_index + 1,
                    "total_scenes": self.total_scenes,
                },
                "available_choices": script_choices,
                "session_state": self.state.value,
                "progress": round(self.progress, 2),
            }

        # 先下发场景元信息：场景 id/名称/位置在生成前就已知，提前下发可让前端
        # 在旁白流式开始前就应用背景图、显示场景名，避免整段旁白期间背景停留在
        # 占位色、场景信息停留在“生成中…”（修复“旁白已出但背景不显示/生成完还要等”）
        yield _meta_event()

        result = None
        for evt in harness.run_stream(harness_input):
            if evt.get("event") == "done":
                result = evt.get("result")
            yield evt

        # 流结束后更新会话状态
        if result:
            self.current_result = result
            self.state = SessionState.GENERATED

        # 再次下发 meta：携带生成完成后的最终 session_state（generated）与 progress，
        # 前端据此显示选项
        yield _meta_event()

    def choose(self, choice_index: int, choice_text: str) -> dict:
        if self.state != SessionState.GENERATED:
            raise HTTPException(400, f"当前状态为 {self.state.value}，无法做出选择")

        scene = self.adapter.scenes[self.current_scene_index] if self.current_scene_index < len(self.adapter.scenes) else {}
        script_choices = self.adapter.get_scene_choices(self.current_scene_index, choice_history=self.history or None)

        chosen = {"text": choice_text, "id": str(choice_index), "description": ""}
        if 0 <= choice_index < len(script_choices):
            chosen = script_choices[choice_index]

        # 从选项中提取后果信息（供下一场景生成时参考）
        consequences = ""
        cons_data = chosen.get("consequences", {})
        if isinstance(cons_data, dict):
            consequences = cons_data.get("immediate", "") or cons_data.get("delayed", "")
        elif cons_data:
            consequences = str(cons_data)

        self.history.append({
            "scene_index": self.current_scene_index,
            "scene_id": scene.get("id", scene.get("scene_id", "")),
            "scene_name": scene.get("name", ""),
            "narration": self.current_result.get("data", {}).get("narration", ""),
            "dialogues": self.current_result.get("data", {}).get("dialogues", []),
            "player_choice": chosen.get("text", choice_text),
            "option_id": chosen.get("id", str(choice_index)),
            "consequences": consequences,
        })

        self.previous_result = self.current_result
        self.current_scene_index += 1

        if self.current_scene_index >= self.total_scenes:
            self.state = SessionState.FINISHED
            # 保存结局场景的生成结果和结局数据
            last_result = self.current_result
            endings_data = self.adapter.raw_data.get("endings", [])
            matched_ending = self._match_ending(endings_data, self.history)
            self.current_result = None
            return {
                "chosen": chosen,
                "session_state": self.state.value,
                "progress": round(self.progress, 2),
                "message": "故事已结束",
                "ending": {
                    "narration": last_result.get("data", {}).get("narration", "") if last_result else "",
                    "dialogues": last_result.get("data", {}).get("dialogues", []) if last_result else [],
                    "matched_ending": matched_ending,
                },
            }
        else:
            self.state = SessionState.CHOSEN
            self.current_result = None

        return {
            "chosen": chosen,
            "session_state": self.state.value,
            "progress": round(self.progress, 2),
            "message": "选择已确认",
        }

    def _match_ending(self, endings: list, history: list) -> dict | None:
        """根据玩家历史选择匹配最合适的结局"""
        import re as _re
        if not endings:
            return None

        # 英文关键词 → 中文同义词映射
        keyword_synonyms = {
            "stop": ["停止", "放弃", "拒绝", "不", "算了", "退出", "删除", "不去"],
            "continue": ["继续", "深入", "坚持", "前进", "探索", "接受"],
            "negotiate": ["谈判", "协议", "妥协", "交易", "达成", "协商", "合作"],
            "reveal": ["公开", "揭露", "真相", "曝光", "公布", "揭发", "展示"],
            "investigate": ["调查", "追查", "查明", "探究", "追踪", "查"],
            "hidden": ["隐藏", "秘密", "暗中", "悄悄", "潜伏", "偷偷"],
            "accept": ["接受", "同意", "答应", "确认"],
            "reject": ["拒绝", "否决", "不接受", "回绝"],
            "trust": ["信任", "相信", "信赖"],
            "distrust": ["不信任", "怀疑", "质疑"],
        }

        # 构建玩家选择历史: {scene_id: choice_text}
        player_choices = {}
        for h in history:
            sid = h.get("scene_id", "")
            choice = h.get("player_choice", "").lower()
            if sid and choice:
                player_choices[sid] = choice

        def parse_condition(cond_value: str):
            """解析条件值，返回 (scene_id, keyword) 或 None"""
            cond_value = cond_value.strip()
            # 格式: "在scene_XX选择YYY" 或 "在scene_XX选择YYY且..."
            m = _re.search(r'(?:在)?(scene_\d+).{0,4}(?:选择|选|select|choose)\s*(\w+)', cond_value, _re.IGNORECASE)
            if m:
                return m.group(1), m.group(2).lower()
            return None, None

        def choice_matches_keyword(choice_text: str, keyword: str) -> bool:
            """检查玩家选择文本是否匹配关键词"""
            keyword = keyword.lower()
            # 直接包含
            if keyword in choice_text:
                return True
            # 同义词匹配
            synonyms = keyword_synonyms.get(keyword, [])
            for syn in synonyms:
                if syn in choice_text:
                    return True
            return False

        best_match = None
        best_score = -1

        for ending in endings:
            if not isinstance(ending, dict):
                continue
            score = 0
            total_conditions = 0

            # 匹配 trigger_conditions
            for cond in ending.get("trigger_conditions", []):
                if not isinstance(cond, dict):
                    continue
                total_conditions += 1
                cond_val = str(cond.get("value", ""))
                scene_id, keyword = parse_condition(cond_val)
                if scene_id and keyword and scene_id in player_choices:
                    if choice_matches_keyword(player_choices[scene_id], keyword):
                        score += 2  # trigger_conditions 权重更高

            # 匹配 unlock_conditions
            for uc in ending.get("unlock_conditions", []):
                if not isinstance(uc, str):
                    continue
                total_conditions += 1
                scene_id, keyword = parse_condition(uc)
                if scene_id and keyword and scene_id in player_choices:
                    if choice_matches_keyword(player_choices[scene_id], keyword):
                        score += 1

            # 归一化分数（避免条件少的结局因为容易全匹配而胜出）
            normalized = score / max(total_conditions, 1)
            if normalized > best_score or (normalized == best_score and score > best_match.get("_raw_score", 0) if best_match else True):
                best_score = normalized
                best_match = {**ending, "_raw_score": score, "_normalized_score": normalized}

        if best_match:
            # 清除内部字段
            best_match.pop("_raw_score", None)
            best_match.pop("_normalized_score", None)
            return {
                "title": best_match.get("title", ""),
                "type": best_match.get("type", ""),
                "narrative": best_match.get("narrative", ""),
                "epilogue": best_match.get("epilogue", ""),
                "replay_value": best_match.get("replay_value", ""),
            }
        return None

    def get_state(self) -> dict:
        scene = self.adapter.scenes[self.current_scene_index] if self.current_scene_index < len(self.adapter.scenes) else {}
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "progress": round(self.progress, 2),
            "current_scene": {
                "id": scene.get("id", ""),
                "name": scene.get("name", ""),
            } if scene else None,
            "total_scenes": self.total_scenes,
            "completed_scenes": self.current_scene_index,
        }


# ═══════════════════════════════════════════════════════
# 上传文件管理
# ═══════════════════════════════════════════════════════

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
UPLOAD_DIR.mkdir(exist_ok=True)
SAMPLES_DIR.mkdir(exist_ok=True)

uploaded_scripts: dict[str, dict] = {}  # script_id -> {path, adapter, summary}
_loaded_stories: dict[str, dict] = {}  # path -> story data (轻量 API 缓存)


def save_upload(filename: str, content: bytes) -> str:
    """保存上传的文件，返回 script_id"""
    script_id = str(uuid.uuid4())[:8]
    script_dir = UPLOAD_DIR / script_id
    script_dir.mkdir(exist_ok=True)

    file_path = script_dir / filename
    with open(file_path, "wb") as f:
        f.write(content)

    return script_id


def parse_script(script_id: str) -> GenericScriptAdapter:
    """解析已上传的剧本"""
    script_dir = UPLOAD_DIR / script_id
    if not script_dir.exists():
        raise HTTPException(404, f"剧本不存在: {script_id}")

    adapter = GenericScriptAdapter()
    # 如果是目录，加载目录；如果是单文件，加载文件
    files = list(script_dir.glob("*"))
    if len(files) == 1:
        adapter.load(str(files[0]))
    else:
        adapter.load(str(script_dir))

    return adapter


# ═══════════════════════════════════════════════════════
# FastAPI App
# ═══════════════════════════════════════════════════════

app = FastAPI(title="QuillForge API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict[str, GameSession] = {}

# AI 实时生成管线专用会话存储（与上传管线完全独立）
realtime_sessions: dict[str, RealtimeStorySession] = {}
harness: Optional[Harness] = None


def get_harness() -> Harness:
    global harness
    if harness is None:
        harness = create_default_harness()
    return harness


image_generator: Optional[ImageGenerator] = None


def get_image_generator() -> ImageGenerator:
    """生图器单例（未配置 IMAGE_API_KEY 时 enabled=False，调用方回退占位图）"""
    global image_generator
    if image_generator is None:
        image_generator = ImageGenerator()
    return image_generator


tts_generator: Optional[TTSGenerator] = None


def get_tts_generator() -> TTSGenerator:
    """TTS 语音合成器单例（未配置 TTS_API_KEY 时 enabled=False，回退纯文字模式）"""
    global tts_generator
    if tts_generator is None:
        tts_generator = TTSGenerator()
    return tts_generator


# ───────────────────────────────────────────────────
# API 路由
# ───────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_script(file: UploadFile = File(...)):
    """上传剧本文件"""
    content = await file.read()
    script_id = save_upload(file.filename, content)

    try:
        adapter = parse_script(script_id)
        summary = adapter.to_summary()
        uploaded_scripts[script_id] = {
            "path": str(UPLOAD_DIR / script_id),
            "adapter": adapter,
            "summary": summary,
        }
        return {"script_id": script_id, **summary}
    except Exception as e:
        raise HTTPException(400, f"剧本解析失败: {str(e)}")


@app.post("/api/load-folder", summary="从本地文件夹路径加载剧本")
async def load_folder(body: dict) -> dict:
    """
    直接指定本地文件夹路径加载剧本（支持嵌套目录结构）。
    适用于剧本以文件夹形式存放的场景，如：
      测试剧本/回魂公寓/
        ├── 元数据.yaml
        ├── 主要角色.yaml
        ├── 场景清单.yaml
        └── 主线剧情.yaml
    """
    path = body.get("path", "")
    if not path:
        raise HTTPException(400, "缺少 path 参数")

    folder = Path(path)
    if not folder.exists():
        raise HTTPException(404, f"路径不存在：{path}")
    if not folder.is_dir():
        raise HTTPException(400, f"路径不是文件夹：{path}")

    script_id = str(uuid.uuid4())[:8]
    try:
        # 方式1：用 GenericScriptAdapter 加载（适合结构化单/多文件）
        adapter = GenericScriptAdapter()
        adapter.load(str(folder))
        summary = adapter.to_summary()
        uploaded_scripts[script_id] = {
            "path": str(folder),
            "adapter": adapter,
            "summary": summary,
        }

        # 同时用 load_story 加载（兼容轻量 API 的按关键词查找模式）
        story = load_story(str(folder))
        _loaded_stories[str(folder)] = story

        return {
            "script_id": script_id,
            "path": str(folder),
            **summary,
            "story_title": story.get("title", ""),
            "story_scenes": len(story.get("scenes", [])),
            "story_characters": len(story.get("characters", [])),
        }
    except Exception as e:
        raise HTTPException(400, f"剧本解析失败: {str(e)}")


class AutoRunRequest(BaseModel):
    path: str
    max_scenes: Optional[int] = None        # 最多跑几个场景（默认全部）
    start_scene: Optional[int] = 0          # 从第几个场景开始（0-based）


def _build_scene_harness_input(story: dict, scene: dict, idx: int, previous_data: dict | None = None) -> dict:
    """构建单个场景的 harness_input（供 auto_run 和 auto_run_stream 共用）"""
    scene_id = scene.get("id", scene.get("scene_id", ""))
    characters = get_scene_characters(story, scene)
    worldline = build_worldline(story, idx)
    scene_text = build_scene_text(scene)
    current_stage = _get_current_stage(story.get("stages", []), scene_id)

    # get_scene_characters 已返回格式化后的角色数据（secrets/relationships 等已转为字符串）
    harness_input = {
        "currentScene": scene_text,
        "characters": characters,
        "worldline": worldline,
        "playerChoice": "",
        "_worldbook": _extract_worldbook_rules(story.get("worldbook", "")),
        "_narrativeStyle": story.get("narrative_style", "") or "第二人称叙事",
        "_plotSummary": story.get("plot_summary", ""),
        "_coreConflict": story.get("core_conflict", ""),
        "_themes": _format_themes(story.get("themes", [])),
        "_relationshipNetwork": _format_relationships(story.get("character_relationships", [])),
        "_stagesOverview": _format_stages_overview(story.get("stages", [])),
        "_events": _format_events(story.get("events", [])),
        "_keyChoices": _format_key_choices(story.get("key_choices", [])),
        "_endings": _format_endings(story.get("endings", [])),
        "_sceneDetails": _format_scene_details(scene, story.get("key_choices", [])),
        "_worldlines": _format_worldlines(
            story.get("world_lines", []),
            story.get("convergence_points", []),
            story.get("worldline_rules", []),
        ),
        "_currentStageContext": _format_current_stage_context(current_stage),
        "_sceneBeats": _format_beats_for_scene(story.get("beats", []), scene_id),
        "_sceneHooks": _format_hooks_for_scene(story.get("hooks", []), scene_id),
    }
    # 当本场景有剧本选项时，标记跳过 Stage 4 的 LLM 生成
    _scene_script_choices = _extract_script_choices(scene, story.get("key_choices", []))
    if _scene_script_choices:
        harness_input["_hasScriptChoices"] = True
        harness_input["_scriptChoices"] = _scene_script_choices
    if previous_data:
        harness_input["previousContext"] = previous_data
    return harness_input


@app.post("/api/auto-run", summary="一键运行：加载剧本文件夹并自动生成全部场景")
async def auto_run(req: AutoRunRequest) -> dict:
    """
    给定文件夹路径，自动完成：加载剧本 → 逐场景生成对话 → 返回完整结果。
    无需手动调用 /api/start → /generate → /choose 等步骤。
    """
    folder = Path(req.path)
    if not folder.exists():
        raise HTTPException(404, f"路径不存在：{req.path}")
    if not folder.is_dir():
        raise HTTPException(400, f"路径不是文件夹：{req.path}")

    # 1. 加载剧本
    story = load_story(str(folder))
    scenes = story.get("scenes", [])
    if not scenes:
        raise HTTPException(400, "剧本中没有找到场景数据")

    start = req.start_scene or 0
    end = min(req.max_scenes + start, len(scenes)) if req.max_scenes else len(scenes)
    if start >= len(scenes):
        raise HTTPException(400, f"start_scene({start}) 超出范围（共 {len(scenes)} 个场景）")

    # 2. 逐场景生成
    h = get_harness()
    results = []
    previous_data = None

    for idx in range(start, end):
        scene = scenes[idx]
        harness_input = _build_scene_harness_input(story, scene, idx, previous_data)

        try:
            result = h.run(harness_input)
            data = result.get("data", {})
            # 优先使用剧本定义的选项，而不是 LLM 生成的
            script_choices = _extract_script_choices(scene, story.get("key_choices", []))
            results.append({
                "scene_index": idx,
                "scene_name": scene.get("name", f"场景 {idx + 1}"),
                "location": scene.get("location", ""),
                "success": result.get("success", False),
                "narration": data.get("narration", ""),
                "dialogues": data.get("dialogues", []),
                "nextChoices": script_choices if script_choices else data.get("nextChoices", []),
                "validation": result.get("validation", {}),
            })
            previous_data = data
        except Exception as e:
            results.append({
                "scene_index": idx,
                "scene_name": scene.get("name", f"场景 {idx + 1}"),
                "success": False,
                "error": str(e),
            })
            break

    # 提取结局数据（最后一个场景的结果作为结局）
    ending_result = None
    if results and results[-1].get("success"):
        last = results[-1]
        endings_data = story.get("endings", [])
        # auto-run没有玩家选择历史，展示所有结局
        all_endings = []
        if endings_data and isinstance(endings_data, list):
            for e in endings_data:
                if isinstance(e, dict):
                    all_endings.append({
                        "title": e.get("title", ""),
                        "type": e.get("type", ""),
                        "narrative": e.get("narrative", ""),
                        "epilogue": e.get("epilogue", ""),
                        "replay_value": e.get("replay_value", ""),
                    })
        ending_result = {
            "narration": last.get("narration", ""),
            "dialogues": last.get("dialogues", []),
            "all_endings": all_endings,  # auto-run 展示全部结局
        }

    return {
        "title": story.get("title", ""),
        "total_scenes": len(scenes),
        "generated_scenes": len(results),
        "scene_range": [start, end - 1],
        "results": results,
        "ending": ending_result,
    }


@app.post("/api/auto-run/stream", summary="流式一键运行：SSE 逐场景推送生成结果")
async def auto_run_stream(req: AutoRunRequest):
    """
    SSE 流式版本的 auto-run。
    每个场景生成完毕后立刻推送给前端，无需等待全部场景完成。

    SSE 事件类型：
    - event: start    → {"title": "...", "total_scenes": N}
    - event: scene    → 单个场景的完整结果（与 auto_run 中的 results 元素相同）
    - event: ending   → 结局数据（仅最后一个场景成功后推送）
    - event: done     → {"generated_scenes": M, "total_time_ms": T}
    - event: error    → {"message": "..."}
    """
    folder = Path(req.path)
    if not folder.exists():
        raise HTTPException(404, f"路径不存在：{req.path}")
    if not folder.is_dir():
        raise HTTPException(400, f"路径不是文件夹：{req.path}")

    story = load_story(str(folder))
    scenes = story.get("scenes", [])
    if not scenes:
        raise HTTPException(400, "剧本中没有找到场景数据")

    start = req.start_scene or 0
    end = min(req.max_scenes + start, len(scenes)) if req.max_scenes else len(scenes)
    if start >= len(scenes):
        raise HTTPException(400, f"start_scene({start}) 超出范围（共 {len(scenes)} 个场景）")

    def event_stream():
        import asyncio
        h = get_harness()
        previous_data = None
        generated = 0
        t0 = time.time()

        # 推送 start 事件
        start_payload = json.dumps({
            "title": story.get("title", ""),
            "total_scenes": len(scenes),
            "scene_range": [start, end - 1],
        }, ensure_ascii=False)
        yield f"event: start\ndata: {start_payload}\n\n"

        for idx in range(start, end):
            scene = scenes[idx]
            harness_input = _build_scene_harness_input(story, scene, idx, previous_data)

            try:
                result = h.run(harness_input)
                data = result.get("data", {})
                script_choices = _extract_script_choices(scene, story.get("key_choices", []))

                scene_result = {
                    "scene_index": idx,
                    "scene_name": scene.get("name", f"场景 {idx + 1}"),
                    "location": scene.get("location", ""),
                    "success": result.get("success", False),
                    "narration": data.get("narration", ""),
                    "dialogues": data.get("dialogues", []),
                    "nextChoices": script_choices if script_choices else data.get("nextChoices", []),
                    "validation": result.get("validation", {}),
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
                previous_data = data
                generated += 1

                scene_json = json.dumps(scene_result, ensure_ascii=False)
                yield f"event: scene\ndata: {scene_json}\n\n"

                # 最后一个场景成功后，推送结局数据
                if idx == end - 1 and result.get("success"):
                    endings_data = story.get("endings", [])
                    all_endings = []
                    if endings_data and isinstance(endings_data, list):
                        for e in endings_data:
                            if isinstance(e, dict):
                                all_endings.append({
                                    "title": e.get("title", ""),
                                    "type": e.get("type", ""),
                                    "narrative": e.get("narrative", ""),
                                    "epilogue": e.get("epilogue", ""),
                                    "replay_value": e.get("replay_value", ""),
                                })
                    ending_payload = json.dumps({
                        "narration": data.get("narration", ""),
                        "dialogues": data.get("dialogues", []),
                        "all_endings": all_endings,
                    }, ensure_ascii=False)
                    yield f"event: ending\ndata: {ending_payload}\n\n"

            except Exception as e:
                err_payload = json.dumps({
                    "scene_index": idx,
                    "scene_name": scene.get("name", f"场景 {idx + 1}"),
                    "message": str(e),
                }, ensure_ascii=False)
                yield f"event: error\ndata: {err_payload}\n\n"
                break

        # 推送 done 事件
        done_payload = json.dumps({
            "generated_scenes": generated,
            "total_time_ms": int((time.time() - t0) * 1000),
        }, ensure_ascii=False)
        yield f"event: done\ndata: {done_payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 兼容
        },
    )


@app.post("/api/upload-directory")
async def upload_directory(files: list[UploadFile] = File(...)):
    """上传多个剧本文件（目录形式，保留子目录结构）"""
    script_id = str(uuid.uuid4())[:8]
    script_dir = UPLOAD_DIR / script_id
    script_dir.mkdir(exist_ok=True)

    for file in files:
        content = await file.read()
        # 保留子目录结构（浏览器 webkitdirectory 会传完整相对路径）
        file_path = script_dir / file.filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(content)

    try:
        adapter = GenericScriptAdapter()
        adapter.load(str(script_dir))
        summary = adapter.to_summary()
        uploaded_scripts[script_id] = {
            "path": str(script_dir),
            "adapter": adapter,
            "summary": summary,
        }
        return {"script_id": script_id, **summary}
    except Exception as e:
        raise HTTPException(400, f"剧本解析失败: {str(e)}")


class GenerateScriptRequest(BaseModel):
    prompt: str


@app.post("/api/generate-script")
async def api_generate_script(body: GenerateScriptRequest):
    """AI 生成剧本（SSE 流式返回进度）"""
    user_prompt = body.prompt.strip()
    if not user_prompt:
        raise HTTPException(400, "请输入剧本描述")
    if len(user_prompt) > 150:
        raise HTTPException(400, f"描述不能超过150字（当前{len(user_prompt)}字）")

    def event_stream():
        final_script_id = None
        final_path = None
        for event in generate_script(user_prompt):
            evt_type = event.get("type", "progress")
            payload = json.dumps(event, ensure_ascii=False)
            yield f"event: {evt_type}\ndata: {payload}\n\n"
            if evt_type == "done":
                final_script_id = event.get("script_id")
                final_path = event.get("path")

        if final_script_id and final_path:
            try:
                adapter = GenericScriptAdapter()
                adapter.load(final_path)
                summary = adapter.to_summary()
                uploaded_scripts[final_script_id] = {
                    "path": final_path,
                    "adapter": adapter,
                    "summary": summary,
                }
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/samples")
def list_samples():
    """列出内置示例剧本（支持单文件和目录结构）"""
    samples = []
    if SAMPLES_DIR.exists():
        for f in sorted(SAMPLES_DIR.glob("*")):
            if f.is_dir():
                # 目录类型剧本：检查是否包含 yaml/json 文件
                has_data = any(
                    p.suffix in (".yaml", ".yml", ".json")
                    for p in f.rglob("*") if p.is_file()
                )
                if not has_data:
                    continue
                try:
                    adapter = GenericScriptAdapter()
                    adapter.load(str(f))
                    samples.append({
                        "id": f.name,
                        "filename": f.name,
                        "path": str(f),
                        **adapter.to_summary(),
                    })
                except Exception:
                    samples.append({"id": f.name, "filename": f.name, "error": "解析失败"})
            elif f.suffix in (".yaml", ".yml", ".json"):
                try:
                    adapter = GenericScriptAdapter()
                    adapter.load(str(f))
                    samples.append({
                        "id": f.stem,
                        "filename": f.name,
                        "path": str(f),
                        **adapter.to_summary(),
                    })
                except Exception:
                    samples.append({"id": f.stem, "filename": f.name, "error": "解析失败"})
    return {"samples": samples}


@app.post("/api/upload-sample-path")
def upload_sample_path(path: str = ""):
    """从文件路径加载内置示例剧本"""
    if not path:
        raise HTTPException(400, "缺少 path 参数")

    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(404, f"文件不存在: {path}")

    # 安全检查：只允许加载 samples 目录下的文件
    try:
        file_path.resolve().relative_to(SAMPLES_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "只能加载 samples 目录下的剧本文件")

    script_id = str(uuid.uuid4())[:8]
    try:
        adapter = GenericScriptAdapter()
        adapter.load(str(file_path))
        summary = adapter.to_summary()
        uploaded_scripts[script_id] = {
            "path": str(file_path),
            "adapter": adapter,
            "summary": summary,
        }
        return {"script_id": script_id, **summary}
    except Exception as e:
        raise HTTPException(400, f"剧本解析失败: {str(e)}")


@app.post("/api/start")
def start_game(script_id: str = ""):
    """开始游戏会话"""
    if script_id and script_id in uploaded_scripts:
        adapter = uploaded_scripts[script_id]["adapter"]
    else:
        raise HTTPException(400, "请先上传剧本文件")

    session = GameSession(adapter)
    sessions[session.session_id] = session

    return {
        "session_id": session.session_id,
        "script": adapter.to_summary(),
        "state": session.get_state(),
    }


@app.post("/api/session/{session_id}/generate")
def generate_scene(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session.generate(get_harness())


@app.post("/api/session/{session_id}/generate-stream", summary="流式生成场景（SSE）")
def generate_scene_stream(session_id: str):
    """
    SSE 流式版本的场景生成。
    Pipeline 各阶段产出时立即推送给前端，无需等待全部完成。
    当 TTS 启用时，dialogue 事件之后会额外下发 tts 事件（音频URL），
    前端据此播放语音，旁白和动作旁白不发语音。
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    tts_gen = get_tts_generator()
    script_slug = slugify(session.adapter.title)
    characters = session.adapter.characters or []

    def event_stream():
        import concurrent.futures as _cf
        # TTS 合成策略：
        # - 第一句 dialogue：同步生成 TTS，确保文字和音频同时到达，消除首句延迟
        # - 后续 dialogue：异步提交到线程池，不阻塞主流
        tts_futures: dict = {}  # future -> (speaker, line_idx)
        first_dialogue_tts = True  # 首句标志

        # 追踪前端 vnState.queue 的索引：哪些事件会 push 到队列
        # narration_start → 不 push（设标志）
        # narration_delta（首条）→ push（+1）
        # narration（流式模式）→ 不 push（更新已有项）
        # narration（非流式回退）→ push（+1）
        # dialogue → push（+1）
        # action → push（+1）
        queue_idx = -1
        narration_delta_first = False  # narration_start 后等待首条 delta

        with _cf.ThreadPoolExecutor(max_workers=2) as tts_pool:
            for evt in session.generate_stream(get_harness()):
                evt_type = evt.get("event", "message")

                # 追踪前端 queue 索引
                if evt_type == "narration_start":
                    narration_delta_first = True
                elif evt_type == "narration_delta":
                    if narration_delta_first:
                        queue_idx += 1
                        narration_delta_first = False
                elif evt_type == "narration":
                    if narration_delta_first:
                        # 非流式回退（旧后端直接发 narration）
                        queue_idx += 1
                    narration_delta_first = False
                elif evt_type in ("dialogue", "action"):
                    queue_idx += 1

                # ── 第一句 dialogue：同步生成 TTS，文字+音频一起下发 ──
                if (evt_type == "dialogue" and tts_gen.enabled
                        and first_dialogue_tts):
                    first_dialogue_tts = False
                    speaker = evt.get("speaker", "")
                    text = evt.get("text", "")
                    tts_url = None
                    if speaker and text:
                        voice_id = tts_gen.get_voice_id(speaker, characters)
                        # 同步生成（命中缓存时瞬间返回，首次播放需 2-3s）
                        tts_url = tts_gen.generate(
                            script_slug, speaker, text, voice_id
                        )
                    # 下发 dialogue 事件
                    payload = json.dumps(evt, ensure_ascii=False)
                    yield f"event: {evt_type}\ndata: {payload}\n\n"
                    # 紧接着下发 tts 事件（前端同时开始打字机+播放音频）
                    if tts_url:
                        _tts_evt = {"event": "tts", "speaker": speaker,
                                    "url": tts_url, "lineIdx": queue_idx}
                        _tts_payload = json.dumps(_tts_evt, ensure_ascii=False)
                        yield f"event: tts\ndata: {_tts_payload}\n\n"
                    # 收取已完成的异步 TTS
                    _done = [f for f in tts_futures if f.done()]
                    for fut in _done:
                        _sp, _idx = tts_futures.pop(fut)
                        try:
                            _u = fut.result()
                            if _u:
                                _te = {"event": "tts", "speaker": _sp,
                                       "url": _u, "lineIdx": _idx}
                                yield f"event: tts\ndata: {json.dumps(_te, ensure_ascii=False)}\n\n"
                        except Exception:
                            pass
                    continue

                # ── 正常流程：先下发事件 ──
                payload = json.dumps(evt, ensure_ascii=False)
                yield f"event: {evt_type}\ndata: {payload}\n\n"

                # 拦截后续 dialogue 事件：异步提交 TTS 任务（不阻塞主流）
                if evt_type == "dialogue" and tts_gen.enabled:
                    speaker = evt.get("speaker", "")
                    text = evt.get("text", "")
                    if speaker and text:
                        voice_id = tts_gen.get_voice_id(speaker, characters)
                        fut = tts_pool.submit(
                            tts_gen.generate, script_slug, speaker, text, voice_id
                        )
                        # 关联当前 dialogue 的 queue 索引
                        tts_futures[fut] = (speaker, queue_idx)

                # 非阻塞地收取已完成的 TTS 结果
                _done = [f for f in tts_futures if f.done()]
                for fut in _done:
                    _speaker, _idx = tts_futures.pop(fut)
                    try:
                        _url = fut.result()
                        if _url:
                            _tts_evt = {"event": "tts", "speaker": _speaker,
                                        "url": _url, "lineIdx": _idx}
                            _tts_payload = json.dumps(_tts_evt, ensure_ascii=False)
                            yield f"event: tts\ndata: {_tts_payload}\n\n"
                    except Exception:
                        pass

            # 主流结束后，等待所有 TTS 任务完成并下发
            if tts_futures:
                for fut in _cf.as_completed(tts_futures):
                    _speaker, _idx = tts_futures.pop(fut)
                    try:
                        _url = fut.result()
                        if _url:
                            _tts_evt = {"event": "tts", "speaker": _speaker,
                                        "url": _url, "lineIdx": _idx}
                            _tts_payload = json.dumps(_tts_evt, ensure_ascii=False)
                            yield f"event: tts\ndata: {_tts_payload}\n\n"
                    except Exception:
                        pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/session/{session_id}/assets-stream", summary="预生成美术素材（SSE）")
def assets_stream(session_id: str):
    """
    分两阶段预生成美术素材（SSE 推送进度）：
      Phase 1（阻塞）：前 N 个场景的背景 + 这些场景中出场的角色立绘 → assets_ready，前端立即开始游戏
      Phase 2（后台）：剩余场景背景 + 剩余角色立绘，玩家阅读前几段时异步生成并推送热更新
    已缓存的图片直接复用（命中磁盘缓存，与是否配置 IMAGE_API_KEY 无关），不重复调用生图 API。
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")

    gen = get_image_generator()
    script_slug = slugify(session.adapter.title)
    characters = session.adapter.characters or []
    scenes = session.adapter.scenes or []

    # ─── 按需生图：只为至少出现在一个场景中的角色生成立绘 ───
    _all_scene_refs: set[str] = set()
    for _s in scenes:
        for _ref in (_s.get("characters_present", []) or []):
            _all_scene_refs.add(str(_ref).strip())
    # 建立 id/name → character 的映射，确定哪些角色被场景引用
    _referenced_chars: list[dict] = []
    for _c in characters:
        _cid = str(_c.get("id", "")).strip()
        _cname = str(_c.get("name", "")).strip()
        if _cid in _all_scene_refs or _cname in _all_scene_refs:
            _referenced_chars.append(_c)
    # 如果过滤后为空（数据异常），回退全量
    if not _referenced_chars and characters:
        _referenced_chars = characters

    def event_stream():
        char_urls: dict[str, str] = {}
        scene_urls: dict[str, str] = {}

        # ─── 构建任务列表（仅被场景引用的角色） ───
        char_tasks: list[dict] = []
        bg_tasks: list[dict] = []

        for c in _referenced_chars:
            name = str(c.get("name", "")).strip()
            cid = str(c.get("id", "") or name)
            if not name:
                continue
            char_tasks.append({"kind": "character", "cache_kind": "char", "cache_id": cid,
                               "name": name, "prompt": build_character_prompt(c),
                               "size": DEFAULT_CHAR_SIZE, "store": ("char", name, ""),
                               "_cid": cid})

        bg_sources = session.adapter.get_scene_bg_sources()
        for i, s in enumerate(scenes):
            sid = str(s.get("id", "") or s.get("name", ""))
            sname = str(s.get("name", "") or sid)
            asset = bg_sources[i] if i < len(bg_sources) else s
            bg_tasks.append({"kind": "scene", "cache_kind": "bg", "cache_id": sid,
                             "name": sname, "prompt": build_background_prompt(asset),
                             "size": DEFAULT_BG_SIZE, "store": ("scene", sid, sname)})

        # ─── 确定前 N 个场景中出场的角色（Phase 1 必须生成的立绘） ───
        preload_n = min(IMAGE_BG_PRELOAD_COUNT, len(scenes))
        preload_char_names: set[str] = set()
        # 建立 id→name 和 name→name 的映射（characters_present 可能是 id 或 name）
        cid_to_name = {}
        for c in characters:
            cname = str(c.get("name", "")).strip()
            ccid = str(c.get("id", "") or cname)
            if cname:
                cid_to_name[ccid] = cname
                cid_to_name[cname] = cname
        for s in scenes[:preload_n]:
            for ref in (s.get("characters_present", []) or []):
                ref_str = str(ref).strip()
                mapped = cid_to_name.get(ref_str, ref_str)
                preload_char_names.add(mapped)

        # ─── 拆分任务为 Phase 1 / Phase 2 ───
        phase1_tasks = bg_tasks[:preload_n] + [
            t for t in char_tasks if t["name"] in preload_char_names
        ]
        phase2_tasks = bg_tasks[preload_n:] + [
            t for t in char_tasks if t["name"] not in preload_char_names
        ]

        total = len(phase1_tasks) + len(phase2_tasks)
        yield {"event": "assets_meta", "enabled": gen.enabled, "total": total,
               "phase1_total": len(phase1_tasks),
               "message": (f"正在生成前 {preload_n} 个场景的素材（共 {total} 张）" if gen.enabled
                           else "加载美术素材（缓存模式）")}

        # ─── Phase 1：前 N 场景背景 + 出场角色立绘（阻塞，完成后即可开始游戏） ───
        phase1_done = 0
        if phase1_tasks:
            max_workers = min(len(phase1_tasks), IMAGE_CONCURRENCY)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(gen.generate, script_slug, t["cache_kind"],
                                    t["cache_id"], t["prompt"], t["size"]): t
                    for t in phase1_tasks
                }
                # 超时 180s 兜底：防止单任务挂起导致前端永久卡在加载界面
                try:
                    for fut in concurrent.futures.as_completed(future_map, timeout=180):
                        t = future_map[fut]
                        try:
                            url = fut.result()
                        except Exception:
                            url = None
                        if url:
                            store_kind, key_a, key_b = t["store"]
                            if store_kind == "char":
                                char_urls[key_a] = url
                            else:
                                scene_urls[key_a] = url
                                if key_b and key_b != key_a:
                                    scene_urls[key_b] = url
                        phase1_done += 1
                        yield {"event": "asset_progress", "kind": t["kind"], "name": t["name"],
                               "done": phase1_done, "total": len(phase1_tasks),
                               "status": "done" if url else "fallback", "url": url}
                except concurrent.futures.TimeoutError:
                    print("[assets_stream] Phase 1 超时(180s)，跳过未完成任务", flush=True)

        # Phase 1 完成 → 前端可立即开始游戏
        yield {"event": "assets_ready", "enabled": gen.enabled,
               "characters": dict(char_urls), "scenes": dict(scene_urls),
               "remaining": len(phase2_tasks)}

        # ─── Phase 2：剩余背景 + 剩余角色立绘（后台生成，逐个推送热更新） ───
        if phase2_tasks:
            max_workers = min(len(phase2_tasks), IMAGE_CONCURRENCY)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(gen.generate, script_slug, t["cache_kind"],
                                    t["cache_id"], t["prompt"], t["size"]): t
                    for t in phase2_tasks
                }
                try:
                    for fut in concurrent.futures.as_completed(future_map, timeout=300):
                        t = future_map[fut]
                        try:
                            url = fut.result()
                        except Exception:
                            url = None
                        store_kind, key_a, key_b = t["store"]
                        if url:
                            if store_kind == "char":
                                char_urls[key_a] = url
                            else:
                                scene_urls[key_a] = url
                                if key_b and key_b != key_a:
                                    scene_urls[key_b] = url
                        # 按类型推送不同事件，前端分别热更新
                        if store_kind == "char":
                            yield {"event": "asset_char_ready",
                                   "char_name": key_a, "url": url, "name": t["name"],
                                   "status": "done" if url else "fallback"}
                        else:
                            yield {"event": "asset_bg_ready",
                                   "scene_id": key_a, "scene_name": key_b,
                                   "url": url, "name": t["name"],
                                   "status": "done" if url else "fallback"}
                except concurrent.futures.TimeoutError:
                    print("[assets_stream] Phase 2 超时(300s)，跳过未完成任务", flush=True)

        # 全部完成
        yield {"event": "assets_done", "enabled": gen.enabled,
               "characters": char_urls, "scenes": scene_urls}

    def sse():
        for evt in event_stream():
            evt_type = evt.get("event", "message")
            yield f"event: {evt_type}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/generated_images/{path:path}", summary="读取生成的美术素材图片")
def serve_generated_image(path: str):
    base = IMAGES_DIR.resolve()
    file_path = (IMAGES_DIR / path).resolve()
    # 安全检查：必须落在缓存目录内且确实存在
    if base not in file_path.parents or not file_path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(file_path, media_type="image/png",
                        headers={"Cache-Control": "no-cache"})  # 不缓存：用户可能手动替换图片


@app.get("/generated_audio/{path:path}", summary="读取生成的 TTS 语音音频")
def serve_generated_audio(path: str):
    """读取磁盘缓存中的 TTS 语音音频文件"""
    base = AUDIO_DIR.resolve()
    file_path = (AUDIO_DIR / path).resolve()
    # 安全检查：必须落在缓存目录内且确实存在
    if base not in file_path.parents or not file_path.is_file():
        raise HTTPException(404, "音频不存在")
    media_type = "audio/wav" if path.lower().endswith(".wav") else "audio/mpeg"
    return FileResponse(file_path, media_type=media_type,
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/session/{session_id}/choose")
def submit_choice(session_id: str, req: ChoiceRequest):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session.choose(req.choice_index, req.choice_text)


@app.get("/api/session/{session_id}/state")
def get_session_state(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session.get_state()


@app.get("/api/session/{session_id}/history")
def get_history(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return {"history": session.history}


@app.get("/")
def index():
    html_path = Path(__file__).resolve().parent / "static" / "index.html"
    if html_path.exists():
        # no-cache：浏览器每次请求都向服务器校验，避免缓存旧版前端代码导致修复不生效
        return FileResponse(html_path, headers={"Cache-Control": "no-cache"})
    return {"message": "QuillForge API v3.0 - Visit /docs"}


@app.get("/game", summary="游戏页面（VN Galgame 引擎）")
def game_page():
    """返回 VN Galgame 游戏页面（打字机/立绘/TTS/结局），从首页跳转进入"""
    html_path = Path(__file__).resolve().parent / "static" / "game.html"
    if html_path.exists():
        return FileResponse(html_path, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "game.html 不存在")


# ═══════════════════════════════════════════════════════
# 轻量 API（来自 harness/main.py，无需会话管理）
# ═══════════════════════════════════════════════════════

_WEB_PLAY_HTML_PATH = Path(__file__).resolve().parent / "static" / "web_play.html"


class PlayRequest(BaseModel):
    storyPath: str
    sceneIndex: int
    playerChoice: Optional[str] = None
    previousDialogues: Optional[list[dict]] = None


@app.get("/play", summary="打开轻量版互动剧情 Web 界面")
async def web_play(
    path: str = Query("", description="剧本目录路径"),
) -> HTMLResponse:
    """返回轻量版互动剧情 HTML 页面（基于路径直接加载剧本）"""
    if not _WEB_PLAY_HTML_PATH.exists():
        raise HTTPException(404, "web_play.html 不存在")
    html = _WEB_PLAY_HTML_PATH.read_text(encoding="utf-8")
    # 注入默认剧本路径
    html = html.replace("__DEFAULT_PATH__", path)
    return HTMLResponse(html)


@app.post("/api/story", summary="加载剧本数据")
async def api_load_story(body: dict) -> dict:
    """加载指定目录的剧本 YAML 数据（轻量 API，无需会话）"""
    path = body.get("path", "")
    if not path or not os.path.isdir(path):
        raise HTTPException(400, f"剧本目录不存在：{path}")
    story = load_story(path)
    _loaded_stories[path] = story
    return {
        "title": story["title"],
        "genre": story.get("genre", ""),
        "narrativeStyle": story.get("narrative_style", ""),
        "characters": story["characters"],
        "totalScenes": len(story["scenes"]),
        "scenes": [
            {
                "name": s.get("name", ""),
                "location": s.get("location", ""),
                "time": s.get("time", ""),
            }
            for s in story["scenes"]
        ],
        "worldLines": story.get("world_lines", []),
        "characterRelationships": story.get("character_relationships", []),
        "events": story.get("events", []),
        "endings": story.get("endings", []),
        "hasWorldbook": bool(story.get("worldbook", "")),
        "plotSummary": story.get("plot_summary", ""),
    }


@app.post("/api/scene", summary="生成/续写当前场景（轻量 API）")
async def api_play_scene(body: PlayRequest) -> dict:
    """根据剧本和玩家选择生成当前场景对话（无需会话，直接传入路径）"""
    path = body.storyPath
    story = _loaded_stories.get(path)
    if not story:
        story = load_story(path)
        _loaded_stories[path] = story

    scenes = story.get("scenes", [])
    idx = body.sceneIndex
    if idx < 0 or idx >= len(scenes):
        raise HTTPException(400, f"场景索引 {idx} 超出范围（共 {len(scenes)} 个场景）")

    scene = scenes[idx]
    scene_id = scene.get("id", scene.get("scene_id", ""))
    characters = get_scene_characters(story, scene)
    worldline = build_worldline(story, idx)
    scene_text = build_scene_text(scene)

    # 提取当前场景的剧情阶段上下文
    current_stage = _get_current_stage(story.get("stages", []), scene_id)

    # get_scene_characters 已返回格式化后的角色数据
    harness_input = {
        "currentScene": scene_text,
        "characters": characters,
        "worldline": worldline,
        "playerChoice": body.playerChoice or "",
        # 注入全局剧本上下文
        "_worldbook": _extract_worldbook_rules(story.get("worldbook", "")),
        "_narrativeStyle": story.get("narrative_style", "") or "第二人称叙事",
        "_plotSummary": story.get("plot_summary", ""),
        "_coreConflict": story.get("core_conflict", ""),
        "_themes": _format_themes(story.get("themes", [])),
        "_relationshipNetwork": _format_relationships(story.get("character_relationships", [])),
        "_stagesOverview": _format_stages_overview(story.get("stages", [])),
        "_events": _format_events(story.get("events", [])),
        "_keyChoices": _format_key_choices(story.get("key_choices", [])),
        "_endings": _format_endings(story.get("endings", [])),
        "_sceneDetails": _format_scene_details(scene, story.get("key_choices", [])),
        "_worldlines": _format_worldlines(
            story.get("world_lines", []),
            story.get("convergence_points", []),
            story.get("worldline_rules", []),
        ),
        # 当前场景专属剧情上下文
        "_currentStageContext": _format_current_stage_context(current_stage),
        "_sceneBeats": _format_beats_for_scene(story.get("beats", []), scene_id),
        "_sceneHooks": _format_hooks_for_scene(story.get("hooks", []), scene_id),
    }
    # 当本场景有剧本选项时，标记跳过 Stage 4 的 LLM 生成
    _scene_script_choices = _extract_script_choices(scene, story.get("key_choices", []))
    if _scene_script_choices:
        harness_input["_hasScriptChoices"] = True
        harness_input["_scriptChoices"] = _scene_script_choices
    if body.previousDialogues:
        harness_input["previousContext"] = {
            "dialogues": body.previousDialogues,
        }

    h = get_harness()
    try:
        result = h.run(harness_input)
        data = result.get("data", {})
        # 返回与原 harness/main.py 兼容的格式，并附加场景元信息
        return {
            "narration": data.get("narration", ""),
            "dialogues": data.get("dialogues", []),
            "nextChoices": data.get("nextChoices", []),
            "_sceneName": scene.get("name", ""),
            "_location": scene.get("location", ""),
            "_time": scene.get("time", ""),
            "_choicePrompt": (
                scene.get("interactions", [{}])[0].get("prompt", "请选择你的行动")
                if scene.get("interactions") else "请选择你的行动"
            ),
        }
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(503, f"AI 服务调用失败: {e}")


# ═══════════════════════════════════════════════════════
# AI 实时生成管线 API（与上传管线完全独立）
# ═══════════════════════════════════════════════════════

class RealtimeStartRequest(BaseModel):
    prompt: str


class RealtimeChooseRequest(BaseModel):
    choice_index: int


def _realtime_tts_events(session: RealtimeStorySession, scene_event: dict) -> list[str]:
    """为实时场景中的对话行并行生成 TTS 事件（SSE 字符串列表）。
    使用 ThreadPoolExecutor 并行提交所有 TTS 请求，总耗时≈单条耗时而非 N*单条。"""
    tts_gen = get_tts_generator()
    if not tts_gen.enabled:
        return []
    script_slug = slugify(session.seed.get("title", "realtime"))
    characters = session.seed.get("characters", [])
    # 收集需要生成 TTS 的对话行
    tasks = []  # [(speaker, text, voice_id, queue_idx)]
    queue_idx = 0  # narration = idx 0
    for d in scene_event.get("dialogues", []):
        if d.get("type") == "action":
            queue_idx += 1
            continue
        speaker = d.get("speaker", "") or d.get("speaker_name", "")
        text = d.get("text", "")
        queue_idx += 1
        if speaker and text:
            voice_id = tts_gen.get_voice_id(speaker, characters)
            tasks.append((speaker, text, voice_id, queue_idx))
    if not tasks:
        return []
    # 并行生成
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as pool:
        future_map = {
            pool.submit(tts_gen.generate, script_slug, sp, txt, vid): (sp, qi)
            for sp, txt, vid, qi in tasks
        }
        for fut in concurrent.futures.as_completed(future_map, timeout=60):
            sp, qi = future_map[fut]
            try:
                tts_url = fut.result()
                if tts_url:
                    evt = {"event": "tts", "speaker": sp, "url": tts_url, "lineIdx": qi}
                    results.append(f"event: tts\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n")
            except Exception:
                pass
    return results


def _realtime_bg_event(session: RealtimeStorySession, scene_event: dict) -> str | None:
    """为实时场景生成背景图，返回 asset_bg_ready SSE 事件字符串（失败返回 None）。"""
    gen = get_image_generator()
    script_slug = slugify(session.seed.get("title", "realtime"))
    scene_id = scene_event.get("scene_id", f"scene_{scene_event.get('scene_index', 0)+1:02d}")
    scene_name = scene_event.get("scene_name", scene_id)
    bg_data = {
        "name": scene_name,
        "location": scene_event.get("location", ""),
        "atmosphere": scene_event.get("atmosphere", ""),
        "details": scene_event.get("image_description", ""),
    }
    prompt = build_background_prompt(bg_data)
    try:
        url = gen.generate(script_slug, "bg", scene_id, prompt, DEFAULT_BG_SIZE)
        if url:
            evt = {"event": "asset_bg_ready", "scene_id": scene_id, "scene_name": scene_name, "url": url}
            return f"event: asset_bg_ready\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
    except Exception:
        pass
    return None


def _realtime_start_char_gen(session: RealtimeStorySession):
    """在后台并行生成所有角色立绘（fire-and-forget，结果会被磁盘缓存，assets-stream 调用时秒回）。"""
    gen = get_image_generator()
    if not gen.enabled:
        return
    script_slug = slugify(session.seed.get("title", "realtime"))
    characters = session.seed.get("characters", [])
    for c in characters:
        name = str(c.get("name", "")).strip()
        cid = str(c.get("id", "") or name)
        if not name:
            continue
        prompt = build_character_prompt(c)
        session._pre_gen_executor.submit(
            gen.generate, script_slug, "char", cid, prompt, DEFAULT_CHAR_SIZE
        )


@app.post("/api/realtime/start", summary="AI实时生成剧本-开始（SSE流式）")
async def realtime_start(body: RealtimeStartRequest):
    """
    仿照 InfiPlot 架构的实时剧本生成管线。
    输入一句话描述，流式返回故事种子 + 第一个场景。
    与 /api/generate-script（预生成全量YAML）完全独立，本接口逐场景实时生成。
    场景生成后附带 TTS 语音和背景图热更新事件。
    """
    user_prompt = body.prompt.strip()
    if not user_prompt:
        raise HTTPException(400, "请输入剧本描述")
    if len(user_prompt) > 150:
        raise HTTPException(400, f"描述不能超过150字（当前{len(user_prompt)}字）")

    session = RealtimeStorySession(user_prompt)
    realtime_sessions[session.session_id] = session

    def event_stream():
        for event in session.initialize():
            evt_type = event.get("type", "progress")
            # 故事种子出来后，立即后台并行生成角色立绘（不等完，结果缓存到磁盘）
            if evt_type == "story_seed":
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {evt_type}\ndata: {payload}\n\n"
                _realtime_start_char_gen(session)
                continue
            # 场景事件：先阻塞生成背景图，确保图先到 → 再 yield 场景 → 最后 TTS
            if evt_type == "scene":
                bg_sse = _realtime_bg_event(session, event)
                if bg_sse:
                    yield bg_sse
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {evt_type}\ndata: {payload}\n\n"
                for tts_sse in _realtime_tts_events(session, event):
                    yield tts_sse
                continue
            payload = json.dumps(event, ensure_ascii=False)
            yield f"event: {evt_type}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/realtime/{session_id}/choose", summary="AI实时生成剧本-选择（SSE流式）")
async def realtime_choose(session_id: str, body: RealtimeChooseRequest):
    """玩家做出选择，流式返回下一场景。\n    顺序：背景图先到 → 场景文本 → TTS 并行生成。"""
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")

    def event_stream():
        for event in session.choose(body.choice_index):
            evt_type = event.get("type", "progress")
            # 场景事件：先阻塞生成背景图，确保图先到 → 再 yield 场景 → 最后 TTS
            if evt_type == "scene":
                bg_sse = _realtime_bg_event(session, event)
                if bg_sse:
                    yield bg_sse
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {evt_type}\ndata: {payload}\n\n"
                for tts_sse in _realtime_tts_events(session, event):
                    yield tts_sse
                continue
            payload = json.dumps(event, ensure_ascii=False)
            yield f"event: {evt_type}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/realtime/{session_id}/state", summary="AI实时生成剧本-状态查询")
def realtime_state(session_id: str):
    """查询实时生成会话的当前状态。"""
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")
    return session.get_state()


@app.get("/api/realtime/{session_id}/assets-stream", summary="AI实时生成-预生成角色立绘+场景背景（SSE）")
def realtime_assets_stream(session_id: str):
    """
    为实时生成会话预生成美术素材（SSE 推送进度）。
    Phase 1（阻塞）：所有角色立绘 + 已生成场景的背景图 → assets_ready
    Phase 2（后台）：无（实时模式下后续场景背景由 choose 流热更新）
    与上传管线的 /api/session/{id}/assets-stream 格式完全一致，前端可复用。
    """
    session = realtime_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "实时生成会话不存在")

    gen = get_image_generator()
    script_slug = slugify(session.seed.get("title", "realtime"))
    characters = session.seed.get("characters", [])
    scenes = session.scenes or []

    def event_stream():
        char_urls: dict[str, str] = {}
        scene_urls: dict[str, str] = {}

        # ─── 构建任务列表 ───
        char_tasks: list[dict] = []
        for c in characters:
            name = str(c.get("name", "")).strip()
            cid = str(c.get("id", "") or name)
            if not name:
                continue
            char_tasks.append({
                "kind": "character", "cache_kind": "char", "cache_id": cid,
                "name": name, "prompt": build_character_prompt(c),
                "size": DEFAULT_CHAR_SIZE, "store": ("char", name, ""),
            })

        bg_tasks: list[dict] = []
        for s in scenes:
            sid = s.get("scene_id", f"scene_{s.get('scene_index', 0)+1:02d}")
            sname = s.get("scene_name", sid)
            bg_data = {
                "name": sname,
                "location": s.get("location", ""),
                "atmosphere": s.get("atmosphere", ""),
                "details": s.get("image_description", ""),
            }
            bg_tasks.append({
                "kind": "scene", "cache_kind": "bg", "cache_id": sid,
                "name": sname, "prompt": build_background_prompt(bg_data),
                "size": DEFAULT_BG_SIZE, "store": ("scene", sid, sname),
            })

        all_tasks = char_tasks + bg_tasks
        total = len(all_tasks)
        yield {"event": "assets_meta", "enabled": gen.enabled, "total": total,
               "phase1_total": total,
               "message": (f"正在生成角色立绘和场景背景（共 {total} 张）" if gen.enabled
                           else "加载美术素材（缓存模式）")}

        # ─── 并行生成所有素材 ───
        done_count = 0
        if all_tasks:
            max_workers = min(len(all_tasks), IMAGE_CONCURRENCY)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(gen.generate, script_slug, t["cache_kind"],
                                    t["cache_id"], t["prompt"], t["size"]): t
                    for t in all_tasks
                }
                try:
                    for fut in concurrent.futures.as_completed(future_map, timeout=180):
                        t = future_map[fut]
                        try:
                            url = fut.result()
                        except Exception:
                            url = None
                        if url:
                            store_kind, key_a, key_b = t["store"]
                            if store_kind == "char":
                                char_urls[key_a] = url
                            else:
                                scene_urls[key_a] = url
                                if key_b and key_b != key_a:
                                    scene_urls[key_b] = url
                        done_count += 1
                        yield {"event": "asset_progress", "kind": t["kind"], "name": t["name"],
                               "done": done_count, "total": total,
                               "status": "done" if url else "fallback", "url": url}
                except concurrent.futures.TimeoutError:
                    print("[realtime_assets_stream] 超时(180s)，跳过未完成任务", flush=True)

        # Phase 1 完成 → 前端可立即开始游戏
        yield {"event": "assets_ready", "enabled": gen.enabled,
               "characters": dict(char_urls), "scenes": dict(scene_urls),
               "remaining": 0}
        yield {"event": "assets_done", "enabled": gen.enabled,
               "characters": char_urls, "scenes": scene_urls}

    def sse():
        for evt in event_stream():
            evt_type = evt.get("event", "message")
            yield f"event: {evt_type}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="QuillForge Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    if not os.environ.get("OPENAI_API_KEY"):
        print("[WARNING] 未设置 API Key")

    print(f"服务地址: http://{args.host}:{args.port}")
    print(f"API 文档: http://{args.host}:{args.port}/docs")

    # 后台预热 rembg 模型（避免首次生成立绘时卡顿）
    def _warmup_rembg():
        try:
            from image_gen import _get_rembg_session, _REMOVE_BG_ENABLED
            if _REMOVE_BG_ENABLED:
                print("[warmup] 正在预加载 rembg (isnet-anime) 模型...", flush=True)
                _get_rembg_session()
                print("[warmup] rembg 模型预加载完成", flush=True)
        except Exception as e:
            print(f"[warmup] rembg 预加载跳过: {e}", flush=True)

    import threading
    threading.Thread(target=_warmup_rembg, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
