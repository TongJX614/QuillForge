# -*- coding: utf-8 -*-
"""
通用剧本解析器 (Generic Script Adapter)
自动识别并解析任意格式的剧本文件（YAML/JSON），转换为 Harness 标准输入

支持的输入格式：
1. 单文件（YAML/JSON）：包含 characters、worldline、scenes 等字段
2. 目录：包含多个 YAML 文件，自动合并
3. 回响格式：通过 echo_adapter 处理
"""

from __future__ import annotations

import os
import re
import json
import yaml
from pathlib import Path
from typing import Any, Optional


class GenericScriptAdapter:
    """
    通用剧本解析器
    自动检测剧本格式，解析为标准 Harness 输入
    """

    def __init__(self):
        self.raw_data: dict = {}
        self.title: str = "未命名剧本"
        self.characters: list[dict] = []
        self.scenes: list[dict] = []
        self.scene_assets: list[dict] = []  # 素材清单里的「场景背景」素材（name/mood/details）
        self.worldline_nodes: list[str] = []
        self.worldbook: str = ""
        self.narrative_style: str = ""

    def load(self, source: str) -> dict:
        """
        加载剧本数据
        source: 文件路径、目录路径、或 JSON 字符串
        返回解析后的标准化数据
        """
        path = Path(source)

        if path.is_dir():
            self._load_directory(path)
        elif path.is_file():
            self._load_file(path)
        elif source.strip().startswith("{"):
            self.raw_data = json.loads(source)
        else:
            raise ValueError(f"无法识别的输入: {source}")

        self._normalize()
        return self.to_summary()

    def _load_file(self, path: Path):
        """加载单个文件"""
        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                self.raw_data = yaml.safe_load(f) or {}
            elif path.suffix == ".json":
                self.raw_data = json.load(f)
            elif path.suffix == ".md":
                self.worldbook = f.read()
                self.raw_data = {}
            else:
                raise ValueError(f"不支持的文件格式: {path.suffix}")

    def _load_directory(self, dir_path: Path):
        """加载目录中的所有 YAML/JSON 文件并合并"""
        merged = {}
        for f in sorted(dir_path.rglob("*")):
            if f.suffix in (".yaml", ".yml"):
                with open(f, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                    merged = self._deep_merge(merged, data)
            elif f.suffix == ".json":
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    merged = self._deep_merge(merged, data)
            elif f.suffix == ".md" and "世界" in f.name:
                with open(f, "r", encoding="utf-8") as fh:
                    self.worldbook = fh.read()
        self.raw_data = merged

    def _normalize(self):
        """将各种格式标准化为统一结构"""
        data = self.raw_data

        # ── 标题 ──
        self.title = (
            data.get("title", "")
            or data.get("script_id", "")
            or data.get("name", "")
            or "未命名剧本"
        )

        # ── 叙事风格 ──
        self.narrative_style = (
            data.get("narrative_style", "")
            or data.get("narrativeStyle", "")
            or ""
        )

        # ── 角色解析（支持多种格式） ──
        self.characters = self._parse_characters(data)

        # ── 世界线解析（支持多种格式） ──
        self.worldline_nodes = self._parse_worldline(data)

        # ── 场景解析（支持多种格式） ──
        self.scenes = self._parse_scenes(data)

        # ── 场景背景素材解析（素材清单.yaml：name/mood/details） ──
        self.scene_assets = self._parse_scene_assets(data)

        # ── 世界书 ──
        if not self.worldbook:
            wb = data.get("worldbook", "") or data.get("world_book", "") or data.get("世界书", "")
            if isinstance(wb, str):
                self.worldbook = wb
            elif isinstance(wb, dict):
                self.worldbook = yaml.dump(wb, allow_unicode=True)

    def _parse_characters(self, data: dict) -> list[dict]:
        """从各种格式中提取角色列表"""
        chars = []

        # 格式1: characters 数组
        raw = data.get("characters", [])
        if isinstance(raw, list):
            for c in raw:
                if isinstance(c, dict):
                    chars.append(self._normalize_character(c))
            if chars:
                return chars

        # 格式2: characters 字典（id -> data）
        if isinstance(raw, dict):
            for cid, c in raw.items():
                if isinstance(c, dict):
                    c.setdefault("id", cid)
                    c.setdefault("name", cid)
                    chars.append(self._normalize_character(c))
            if chars:
                return chars

        # 格式3: 嵌套在 main_plot / script 下
        for key in ("main_plot", "script", "story", "剧本"):
            sub = data.get(key, {})
            if isinstance(sub, dict):
                found = self._parse_characters(sub)
                if found:
                    return found

        return chars

    def _normalize_character(self, c: dict) -> dict:
        """标准化单个角色格式 — 提取全部角色卡字段"""
        name = c.get("name", c.get("id", "未知"))
        role = c.get("role", c.get("occupation", c.get("职业", "")))
        personality = c.get("personality", c.get("性格", ""))

        # personality 标签提取（多行列表 → 逗号分隔）
        if "\n" in str(personality):
            tags = re.findall(r'[-•]\s*([^：:\n]+)[：:]?', str(personality))
            personality = "、".join(t.strip() for t in tags[:4]) if tags else personality[:80]

        # 说话风格
        speaking_style = (
            c.get("speakingStyle", "")
            or c.get("speaking_style", "")
            or c.get("voice_tone", "")
            or c.get("说话风格", "")
        )
        if "\n" in str(speaking_style):
            speaking_style = str(speaking_style).split("\n")[0][:80]

        # 背景故事
        background = str(c.get("background", c.get("backstory", c.get("背景", ""))))
        if len(background) > 150:
            background = background[:150] + "…"

        # 动机
        motivation = str(c.get("motivation", c.get("动机", ""))).strip()
        if "\n" in motivation:
            lines = [l.strip().lstrip("- ") for l in motivation.split("\n") if l.strip()][:2]
            motivation = "；".join(lines)
        if len(motivation) > 100:
            motivation = motivation[:100] + "…"

        # 秘密
        secrets_raw = c.get("secrets", c.get("秘密", []))
        if isinstance(secrets_raw, list):
            secrets = "；".join(str(s).strip() for s in secrets_raw[:3])
        else:
            secrets = str(secrets_raw).strip()
        if len(secrets) > 120:
            secrets = secrets[:120] + "…"

        # 角色关系
        rels_raw = c.get("relationships", c.get("关系", []))
        relationships = []
        if isinstance(rels_raw, list):
            for r in rels_raw[:4]:
                if isinstance(r, dict):
                    relationships.append(
                        f"{r.get('target', '')}（{r.get('type', '')}）：{r.get('description', '')}"
                    )
                elif isinstance(r, str):
                    relationships.append(r)
        rel_str = "；".join(relationships) if relationships else ""
        if len(rel_str) > 150:
            rel_str = rel_str[:150] + "…"

        # 外貌
        appearance = str(c.get("appearance", c.get("外貌", ""))).strip()
        if "\n" in appearance:
            appearance = "；".join(l.strip() for l in appearance.split("\n") if l.strip())
        if len(appearance) > 120:
            appearance = appearance[:120] + "…"

        # 成长弧线
        arc = str(c.get("arc", c.get("角色弧线", ""))).strip()
        if "\n" in arc:
            lines = [l.strip() for l in arc.split("\n") if l.strip()]
            arc = " → ".join(lines[:3])
        if len(arc) > 100:
            arc = arc[:100] + "…"

        # 年龄 / 职业（用于生图提示词；此前 age 被丢弃、occupation 仅作 role 后备）
        age = str(c.get("age", c.get("年龄", ""))).strip()
        occupation = str(c.get("occupation", c.get("职业", ""))).strip()

        return {
            "id": c.get("id", c.get("character_id", "")),
            "name": str(name),
            "role": str(role),
            "age": age,
            "occupation": occupation,
            "personality": str(personality),
            "speakingStyle": str(speaking_style),
            "background": background,
            "motivation": motivation,
            "secrets": secrets,
            "relationships": rel_str,
            "appearance": appearance,
            "arc": arc,
        }

    def _parse_worldline(self, data: dict) -> list[str]:
        """从各种格式中提取世界线节点"""
        # 格式1: worldline 字符串 "A → B → C"
        wl = data.get("worldline", "")
        if isinstance(wl, str) and ("→" in wl or "->" in wl):
            return [n.strip() for n in re.split(r'[→\->]+', wl) if n.strip()]

        # 格式2: world_lines 数组
        wls = data.get("world_lines", [])
        if isinstance(wls, list) and wls:
            main = next((w for w in wls if w.get("id") == "main_line" or w.get("name") == "主线"), wls[0])
            path = main.get("path", [])
            nodes = []
            for p in sorted(path, key=lambda x: x.get("sequence", 0)):
                sid = p.get("scene_id", "")
                # 尝试从场景数据获取名称
                name = self._find_scene_name(sid, data) or sid
                nodes.append(name)
            return nodes

        # 格式3: main_nodes / stages 数组
        for key in ("main_nodes", "stages", "mainNodes", "剧情阶段"):
            arr = data.get(key, [])
            if isinstance(arr, list):
                return [str(item.get("name", item.get("id", ""))) for item in arr if isinstance(item, dict)]

        # 格式4: 嵌套
        for key in ("main_plot", "script", "story"):
            sub = data.get(key, {})
            if isinstance(sub, dict):
                found = self._parse_worldline(sub)
                if found:
                    return found

        return ["开始", "结束"]

    def _parse_scenes(self, data: dict) -> list[dict]:
        """从各种格式中提取场景"""
        # 格式1: scenes 数组
        raw = data.get("scenes", [])
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            return [self._normalize_scene(s) for s in raw]

        # 格式2: 从 world_lines.path 推导
        wls = data.get("world_lines", [])
        if isinstance(wls, list) and wls:
            main = next((w for w in wls if w.get("id") == "main_line"), wls[0])
            path = main.get("path", [])
            scenes = []
            for p in sorted(path, key=lambda x: x.get("sequence", 0)):
                sid = p.get("scene_id", "")
                scene_data = self._find_scene_data(sid, data)
                if scene_data:
                    scenes.append(self._normalize_scene(scene_data))
                else:
                    scenes.append({"id": sid, "name": sid, "description": p.get("description", "")})
            return scenes

        # 格式3: 嵌套
        for key in ("main_plot", "script", "story"):
            sub = data.get(key, {})
            if isinstance(sub, dict):
                found = self._parse_scenes(sub)
                if found:
                    return found

        return []

    def _normalize_scene(self, s: dict) -> dict:
        """标准化单个场景格式"""
        return {
            "id": s.get("id", s.get("scene_id", "")),
            "name": s.get("name", s.get("title", s.get("id", ""))),
            "description": s.get("description", s.get("content", "")),
            "location": s.get("location", ""),
            "time": s.get("time", ""),
            "atmosphere": s.get("atmosphere", s.get("mood", "")),
            "characters_present": s.get("characters_present", s.get("characters", [])),
            "choices": s.get("choices", s.get("options", [])),
            "interactions": s.get("interactions", []),
            "branching_point": s.get("branching_point", False),
            "critical_choice": s.get("critical_choice", ""),
            "narrative_notes": s.get("narrative_notes", s.get("notes", "")),
        }

    def _parse_scene_assets(self, data: dict) -> list[dict]:
        """从素材清单.yaml 的 assets.visual 中提取「场景背景」素材（name/mood/details）。
        找不到时返回 []（此时生图回退用游戏场景自身字段）。"""
        assets = data.get("assets", {})
        visual = assets.get("visual", []) if isinstance(assets, dict) else []
        if isinstance(visual, list):
            for item in visual:
                if isinstance(item, dict) and item.get("type") == "场景背景":
                    raw_scenes = item.get("scenes", [])
                    if isinstance(raw_scenes, list):
                        return [
                            {
                                "name": str(s.get("name", "")).strip(),
                                "mood": str(s.get("mood", "")).strip(),
                                "details": str(s.get("details", "")).strip(),
                            }
                            for s in raw_scenes
                            if isinstance(s, dict)
                        ]
        return []

    def get_scene_bg_sources(self) -> list[dict]:
        """为每个游戏场景匹配用于生图背景的场景素材。
        按顺序一一对应：第 i 个游戏场景配 scene_assets[i]（回响里游戏场景 location
        与素材 name 正好吻合，验证了此顺序）；scene_assets 不够长时用游戏场景自身字段兜底。"""
        result: list[dict] = []
        for i, s in enumerate(self.scenes):
            if i < len(self.scene_assets):
                result.append(self.scene_assets[i])
            else:
                result.append(s)
        return result

    def _find_scene_name(self, scene_id: str, data: dict) -> str:
        """根据 scene_id 查找场景名称"""
        s = self._find_scene_data(scene_id, data)
        if s:
            return s.get("name", s.get("title", scene_id))
        return ""

    def _find_scene_data(self, scene_id: str, data: dict) -> dict:
        """在数据中查找指定 scene_id 的场景数据"""
        scenes = data.get("scenes", [])
        if isinstance(scenes, list):
            for s in scenes:
                if isinstance(s, dict) and s.get("id") == scene_id:
                    return s
        # 嵌套查找
        for key in ("main_plot", "script", "story"):
            sub = data.get(key, {})
            if isinstance(sub, dict):
                found = self._find_scene_data(scene_id, sub)
                if found:
                    return found
        return {}

    def build_harness_input(self, scene_index: int, player_choice: str = "", previous_context: dict = None) -> dict:
        """
        构建 Harness 标准输入
        scene_index: 当前场景索引（0-based）
        """
        if scene_index >= len(self.scenes):
            raise ValueError(f"场景索引越界: {scene_index} >= {len(self.scenes)}")

        scene = self.scenes[scene_index]

        # 构建场景描述
        scene_desc_parts = []
        if scene.get("name"):
            scene_desc_parts.append(f"【{scene['name']}】")
        if scene.get("location"):
            scene_desc_parts.append(f"地点：{scene['location']}")
        if scene.get("description"):
            scene_desc_parts.append(scene["description"])

        current_scene = "\n".join(scene_desc_parts) if scene_desc_parts else "场景描述"

        # 确定在场角色
        chars_present = scene.get("characters_present", [])
        if chars_present:
            # 只包含本场景在场的角色（同时匹配 name 和 id）
            characters = [
                c for c in self.characters
                if c.get("name") in chars_present
                or c.get("id") in chars_present
            ]
        else:
            # 默认所有角色
            characters = self.characters

        if not characters:
            characters = [{"name": "旁白", "role": "叙述者", "personality": "客观冷静"}]

        # 世界线字符串
        worldline_str = " → ".join(self.worldline_nodes) if self.worldline_nodes else "开始 → 结束"

        harness_input = {
            "currentScene": current_scene,
            "characters": characters,
            "worldline": worldline_str,
            "playerChoice": player_choice,
        }

        if previous_context:
            harness_input["previousContext"] = previous_context

        # 注入世界书和叙事风格
        harness_input["_worldbook"] = self._extract_worldbook_rules()
        harness_input["_narrativeStyle"] = self.narrative_style or "第二人称叙事"
        harness_input["_sceneAtmosphere"] = scene.get("atmosphere", "")
        harness_input["_narrativeNotes"] = scene.get("narrative_notes", "")
        harness_input["_emotionalArc"] = self._extract_emotional_arc(scene_index)

        # ── 剧本全局上下文注入（与 auto-run 流对齐） ──
        scene_id = scene.get("id", "")
        harness_input["_plotSummary"] = self._extract_plot_summary()
        harness_input["_coreConflict"] = self._extract_core_conflict()
        harness_input["_themes"] = self._extract_themes()
        harness_input["_relationshipNetwork"] = self._extract_relationship_network()
        harness_input["_stagesOverview"] = self._extract_stages_overview()
        harness_input["_currentStageContext"] = self._extract_current_stage_context(scene_id)
        harness_input["_sceneBeats"] = self._extract_beats_for_scene(scene_id)
        harness_input["_sceneHooks"] = self._extract_hooks_for_scene(scene_id)
        harness_input["_events"] = self._extract_events()
        harness_input["_keyChoices"] = self._extract_key_choices()
        harness_input["_endings"] = self._extract_endings()
        harness_input["_worldlines"] = self._extract_worldlines()
        harness_input["_sceneDetails"] = self._extract_scene_details(scene)

        # 当本场景有剧本选项时，标记跳过 Stage 4 的 LLM 生成
        script_choices = self.get_scene_choices(scene_index)
        if script_choices:
            harness_input["_hasScriptChoices"] = True
            harness_input["_scriptChoices"] = script_choices

        return harness_input

    def get_scene_choices(self, scene_index: int) -> list[dict]:
        """获取场景的预设选择项
        
        优先级：
        1. scene.top_level choices（直接定义在场景上的选项）
        2. scene.interactions[].options[]（场景互动中的选项）
        3. scene.critical_choice → key_choices[].options（通过关键选择ID匹配）
        """
        if scene_index >= len(self.scenes):
            return []
        scene = self.scenes[scene_index]

        # 1. 顶层 choices
        raw_choices = scene.get("choices", [])

        # 2. 从 interactions[].options[] 提取
        if not raw_choices:
            interactions = scene.get("interactions", [])
            for interaction in (interactions if isinstance(interactions, list) else []):
                if isinstance(interaction, dict):
                    opts = interaction.get("options", [])
                    if opts and isinstance(opts, list):
                        raw_choices = raw_choices + opts

        # 3. 通过 critical_choice ID 从 key_choices 匹配
        if not raw_choices:
            critical_id = scene.get("critical_choice", "")
            if critical_id:
                key_choices = self.raw_data.get("key_choices", [])
                for kc in (key_choices if isinstance(key_choices, list) else []):
                    if isinstance(kc, dict) and kc.get("id") == critical_id:
                        raw_choices = kc.get("options", [])
                        break

        result = []
        for c in (raw_choices if isinstance(raw_choices, list) else []):
            if isinstance(c, dict):
                consequences = c.get("consequences", {})
                if isinstance(consequences, dict):
                    effect = consequences.get("immediate", consequences.get("delayed", ""))
                else:
                    effect = str(consequences) if consequences else ""
                result.append({
                    "id": c.get("id", ""),
                    "text": c.get("text", c.get("label", "")),
                    "description": c.get("description", effect or ""),
                    "effect": effect or c.get("description", ""),
                })
            elif isinstance(c, str):
                result.append({"id": str(len(result)), "text": c, "description": ""})
        return result

    def _extract_worldbook_rules(self) -> str:
        """从世界书提取关键约束（扩展版：含世界背景、核心科技、组织设定、社会规则）"""
        if not self.worldbook:
            return ""
        rules = []
        # 约束类章节（优先级最高）
        for section_name in ["必须遵守", "禁止出现", "情感基调", "故事边界"]:
            match = re.search(rf'###?\s*{section_name}\s*\n(.*?)(?=###?|\Z)', self.worldbook, re.DOTALL)
            if match:
                rules.append(f"【{section_name}】{match.group(1).strip()}")
        # 设定类章节（提供世界观背景）
        for section_name in ["世界背景", "核心科技", "组织设定", "社会规则"]:
            match = re.search(rf'##?\s*{section_name}\s*\n(.*?)(?=##?|\Z)', self.worldbook, re.DOTALL)
            if match:
                content = match.group(1).strip()
                # 截断过长的设定（避免 prompt 过长）
                if len(content) > 400:
                    content = content[:400] + "…"
                rules.append(f"【{section_name}】{content}")
        if rules:
            return "\n\n".join(rules)
        # 如果没找到任何章节，返回世界书前 1000 字
        return self.worldbook[:1000]

    # ───────────────────────────────────────────────────
    # 剧本上下文提取方法（与 server.py _format_* 系列对齐）
    # ───────────────────────────────────────────────────

    def _get_main_plot(self) -> dict:
        """获取 main_plot 数据（兼容多种嵌套格式）"""
        data = self.raw_data
        if "main_plot" in data:
            return data["main_plot"] if isinstance(data["main_plot"], dict) else {}
        for key in ("script", "story", "剧本"):
            sub = data.get(key, {})
            if isinstance(sub, dict) and "main_plot" in sub:
                return sub["main_plot"]
        return {}

    def _extract_plot_summary(self) -> str:
        """提取剧情概要"""
        plot = self._get_main_plot()
        return str(plot.get("summary", "")).strip()

    def _extract_core_conflict(self) -> str:
        """提取核心冲突"""
        plot = self._get_main_plot()
        return str(plot.get("core_conflict", "")).strip()

    def _extract_themes(self) -> str:
        """提取故事主题"""
        plot = self._get_main_plot()
        themes = plot.get("themes", [])
        if not themes:
            return ""
        return "；".join(str(t)[:50] for t in themes[:5])

    def _extract_relationship_network(self) -> str:
        """提取角色关系网络（含动态变化）"""
        rels = self.raw_data.get("relationships", [])
        if not rels:
            return ""
        parts = []
        for r in rels[:6]:
            if isinstance(r, dict):
                src = r.get("source", "")
                tgt = r.get("target", "")
                rtype = r.get("type", "")
                desc = str(r.get("description", "")).strip()[:80]
                line = f"{src} - {tgt}（{rtype}）：{desc}"
                # 动态发展
                development = r.get("development", [])
                if isinstance(development, list) and development:
                    dev_strs = []
                    for d in development[:3]:
                        if isinstance(d, dict):
                            trigger = str(d.get("trigger", ""))[:30]
                            change = str(d.get("change", ""))[:30]
                            dev_strs.append(f"{trigger}→{change}")
                    if dev_strs:
                        line += f"\n  动态变化：{'；'.join(dev_strs)}"
                parts.append(line)
        return "\n".join(parts)

    def _extract_stages_overview(self) -> str:
        """提取主线剧情阶段总览"""
        plot = self._get_main_plot()
        stages = plot.get("stages", [])
        if not stages:
            return ""
        parts = []
        for i, s in enumerate(stages):
            if isinstance(s, dict):
                name = s.get("name", f"阶段{i+1}")
                scenes_val = s.get("scenes", s.get("scene", ""))
                scene_str = ", ".join(str(x) for x in scenes_val) if isinstance(scenes_val, list) else str(scenes_val)
                desc = str(s.get("description", "")).strip()[:80]
                key_events = s.get("key_events", []) or []
                events_str = "；".join(
                    (str(e.get("name", e))[:20] if isinstance(e, dict) else str(e)[:20])
                    for e in key_events[:3]
                ) if key_events else ""
                emotional_arc = str(s.get("emotional_arc", "")).strip()
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

    def _extract_current_stage_context(self, scene_id: str) -> str:
        """提取当前场景对应阶段的剧情要求"""
        plot = self._get_main_plot()
        stages = plot.get("stages", [])
        if not stages or not scene_id:
            return ""
        # 查找包含当前场景的阶段
        current_stage = None
        for s in stages:
            if isinstance(s, dict):
                scenes = s.get("scenes", [])
                if isinstance(scenes, list) and scene_id in scenes:
                    current_stage = s
                    break
        if not current_stage:
            return ""
        parts = []
        name = current_stage.get("name", "")
        desc = str(current_stage.get("description", "")).strip()
        if name:
            parts.append(f"当前阶段：{name}")
        if desc:
            parts.append(f"阶段目标：{desc}")
        key_events = current_stage.get("key_events", []) or []
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
        emotional_arc = str(current_stage.get("emotional_arc", "")).strip()
        if emotional_arc:
            parts.append(f"情感弧线走向：{emotional_arc}")
        return "\n".join(parts)

    def _extract_beats_for_scene(self, scene_id: str) -> str:
        """提取当前场景的剧情节拍"""
        plot = self._get_main_plot()
        beats = plot.get("beats", [])
        if not beats or not scene_id:
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

    def _extract_hooks_for_scene(self, scene_id: str) -> str:
        """提取当前场景的剧情钩子"""
        plot = self._get_main_plot()
        hooks = plot.get("hooks", [])
        if not hooks or not scene_id:
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

    def _extract_events(self) -> str:
        """提取事件清单"""
        events = self.raw_data.get("events", [])
        if not events:
            return ""
        parts = []
        for e in events[:8]:
            if isinstance(e, dict):
                name = e.get("name", e.get("title", ""))
                tc_raw = e.get("trigger_conditions", [])
                if isinstance(tc_raw, list):
                    trigger_cond = "；".join(
                        str(c.get("value", c.get("type", c))).strip()[:30]
                        for c in tc_raw[:3] if isinstance(c, dict)
                    )
                else:
                    trigger_cond = str(tc_raw).strip()[:60]
                content = str(e.get("content", e.get("description", ""))).strip()[:100]
                chars_involved = e.get("characters_involved", [])
                line = f"【{name}】"
                if trigger_cond:
                    line += f"\n  触发条件：{trigger_cond}"
                if chars_involved:
                    line += f"\n  涉及角色：{', '.join(str(c) for c in chars_involved[:3])}"
                if content:
                    line += f"\n  内容：{content}"
                parts.append(line)
        return "\n\n".join(parts)

    def _extract_key_choices(self) -> str:
        """提取关键选择"""
        choices = self.raw_data.get("choices", self.raw_data.get("key_choices", []))
        if not choices:
            return ""
        parts = []
        for c in choices[:6]:
            if isinstance(c, dict):
                name = c.get("prompt", c.get("name", c.get("title", "")))
                if not name:
                    name = str(c.get("description", ""))[:60]
                position = c.get("scene_id", "")
                opts = c.get("options", []) or []
                option_strs = []
                for o in opts[:4]:
                    if isinstance(o, dict):
                        text = str(o.get("text", o.get("label", ""))).strip()[:40]
                        opt_desc = str(o.get("description", "")).strip()[:50]
                        cons_raw = o.get("consequences", {})
                        if isinstance(cons_raw, dict):
                            consequence = str(cons_raw.get("immediate", "")).strip()[:60]
                        else:
                            consequence = str(cons_raw).strip()[:60]
                        personality = str(o.get("personality_impact", "")).strip()[:40]
                        leads_to = o.get("leads_to_ending", "")
                        opt = f"「{text}」"
                        if opt_desc:
                            opt += f" — {opt_desc}"
                        if consequence:
                            opt += f"\n     即时后果：{consequence}"
                        if personality:
                            opt += f"\n     性格体现：{personality}"
                        if leads_to:
                            opt += f"\n     导向结局：{leads_to}"
                        option_strs.append(opt)
                line = f"【{name}】"
                if position:
                    line += f"（场景：{position}）"
                if option_strs:
                    line += "\n  " + "\n  ".join(option_strs)
                parts.append(line)
        return "\n\n".join(parts)

    def _extract_endings(self) -> str:
        """提取结局系统"""
        endings = self.raw_data.get("endings", [])
        if not endings:
            return ""
        parts = []
        for e in endings[:5]:
            if isinstance(e, dict):
                name = e.get("title", e.get("name", ""))
                etype = e.get("type", "")
                cond_val = e.get("trigger_conditions", [])
                if isinstance(cond_val, list):
                    condition = "；".join(
                        str(c.get("value", c))[:30] for c in cond_val[:3] if isinstance(c, dict)
                    ) if cond_val else ""
                else:
                    condition = str(cond_val).strip()[:60]
                narrative = str(e.get("narrative", "")).strip()[:80]
                line = f"结局「{name}」（{etype}）"
                if condition:
                    line += f"\n  达成条件：{condition}"
                if narrative:
                    line += f"\n  叙事：{narrative}"
                parts.append(line)
        return "\n\n".join(parts)

    def _extract_worldlines(self) -> str:
        """提取世界线结构（含分支路径、汇聚点、规则）"""
        world_lines = self.raw_data.get("world_lines", [])
        if not world_lines:
            return ""
        parts = []
        for wl in world_lines[:4]:
            if isinstance(wl, dict):
                name = wl.get("name", wl.get("id", ""))
                desc = str(wl.get("description", "")).strip()[:80]
                path = wl.get("path", [])
                path_strs = []
                for p in sorted(path, key=lambda x: x.get("sequence", 0)) if isinstance(path, list) else []:
                    if isinstance(p, dict):
                        sid = p.get("scene_id", "")
                        pdesc = str(p.get("description", "")).strip()[:30]
                        path_strs.append(f"{sid}({pdesc})" if pdesc else sid)
                path_line = " → ".join(path_strs) if path_strs else ""
                conditions = wl.get("conditions", [])
                cond_str = ""
                if isinstance(conditions, list) and conditions:
                    cond_str = "；".join(
                        str(c.get("value", c))[:30] for c in conditions[:2] if isinstance(c, dict)
                    )
                consequences = str(wl.get("consequences", "")).strip()[:80]
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
        convergence = self.raw_data.get("convergence_points", [])
        if convergence:
            conv_strs = []
            for cp in convergence[:3]:
                if isinstance(cp, dict):
                    sid = cp.get("scene_id", "")
                    cdesc = str(cp.get("description", "")).strip()[:50]
                    conv_strs.append(f"{sid}: {cdesc}" if cdesc else sid)
            if conv_strs:
                parts.append("\n【汇聚点】" + "；".join(conv_strs))
        # 规则
        rules = self.raw_data.get("rules", [])
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

    def _extract_scene_details(self, scene: dict) -> str:
        """提取当前场景的详细信息（氛围、叙事指导、互动、分支点）"""
        if not scene:
            return ""
        parts = []
        atmosphere = str(scene.get("atmosphere", "")).strip()
        if atmosphere:
            parts.append(f"氛围：{atmosphere}")
        time_val = str(scene.get("time", "")).strip()
        if time_val:
            parts.append(f"时间：{time_val}")
        notes = str(scene.get("narrative_notes", "")).strip()
        if notes:
            parts.append(f"叙事指导：{notes}")
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
        if critical_choice_id:
            choices = self.raw_data.get("choices", self.raw_data.get("key_choices", []))
            for kc in (choices if isinstance(choices, list) else []):
                if isinstance(kc, dict) and kc.get("id") == critical_choice_id:
                    prompt = str(kc.get("prompt", "")).strip()
                    if prompt:
                        parts.append(f"本场景关键拉择：{prompt}")
                    break
        return "\n".join(parts)

    def _extract_emotional_arc(self, scene_index: int) -> str:
        """提取当前场景对应阶段的情感弧线"""
        plot = self._get_main_plot()
        stages = plot.get("stages", [])
        if not stages:
            return ""
        # 尝试通过场景 ID 匹配
        scene_id = ""
        if scene_index < len(self.scenes):
            scene_id = self.scenes[scene_index].get("id", "")
        for s in stages:
            if isinstance(s, dict):
                scenes = s.get("scenes", [])
                if isinstance(scenes, list) and scene_id in scenes:
                    return str(s.get("emotional_arc", "")).strip()
        # 回退：按索引匹配
        if scene_index < len(stages):
            return str(stages[scene_index].get("emotional_arc", "")).strip()
        return ""

    def to_summary(self) -> dict:
        """返回剧本摘要信息"""
        return {
            "title": self.title,
            "characters": [c["name"] for c in self.characters],
            "character_details": self.characters,
            "worldline": " → ".join(self.worldline_nodes),
            "worldline_nodes": self.worldline_nodes,
            "total_scenes": len(self.scenes),
            "scene_names": [s.get("name", s.get("id", "")) for s in self.scenes],
            "has_worldbook": bool(self.worldbook),
        }

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """深度合并两个字典"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = GenericScriptAdapter._deep_merge(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                result[key] = result[key] + value
            else:
                result[key] = value
        return result


# ═══════════════════════════════════════════════════════
# 剧本加载工具函数（从 harness/story_loader.py 合并）
# ═══════════════════════════════════════════════════════

def _load_yaml(path: str) -> dict | list:
    """加载单个 YAML 文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _find_yaml(directory: str, keyword: str) -> Optional[str]:
    """在目录树中按关键词查找 YAML 文件"""
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.endswith((".yaml", ".yml")) and keyword in f:
                return os.path.join(root, f)
    return None


def _find_file(directory: str, keyword: str, ext: str = "") -> Optional[str]:
    """在目录树中按关键词查找指定类型文件"""
    for root, dirs, files in os.walk(directory):
        for f in files:
            if keyword in f and (not ext or f.endswith(ext)):
                return os.path.join(root, f)
    return None


def _fill_character_gaps(characters: list[dict]) -> None:
    """从 personality/backstory 文本中推断缺失的角色字段（降级策略）"""
    # 收集所有角色名，用于关系推断
    all_names = [c.get("name", "") for c in characters]

    for c in characters:
        personality = c.get("personality", "")
        backstory = c.get("background", "")
        role = c.get("role", "")
        combined = f"{personality}。{backstory}"

        # ── motivation: 从 backstory 中提取目的/动机 ──
        if not c.get("motivation"):
            patterns = [
                r'(为了[^，。；\n]{3,40})',
                r'(急需[^，。；\n]{3,30})',
                r'(想要[^，。；\n]{3,30})',
                r'(前来[^，。；\n]{3,30})',
                r'(特意[^，。；\n]{3,30})',
                r'(赶来[^，。；\n]{3,30})',
                r'(需要[^，。；\n]{3,30})',
                r'(决心[^，。；\n]{3,30})',
                r'(一直在[^，。；\n]{3,30})',
                r'(不愿[^，。；\n]{3,25})',
                r'(坚持[^，。；\n]{3,25})',
            ]
            for pat in patterns:
                m = re.search(pat, backstory)
                if m:
                    c["motivation"] = m.group(1).strip()
                    break
            if not c.get("motivation") and personality:
                trait = re.search(r'([^\s，。；]{2,6}(?:感|欲|心|力))', personality)
                if trait:
                    c["motivation"] = f"{trait.group(1)}驱使"
            if not c.get("motivation"):
                c["motivation"] = f"基于{role.split('/')[0].strip()}身份参与故事"

        # ── secrets: 从 backstory 中提取隐藏信息 ──
        if not c.get("secrets"):
            patterns = [
                r'([^，。；\n]{0,10}(?:不知道的是|秘密|隐藏|隐瞒)[^。；\n]{3,50})',
                r'([^，。；\n]{0,10}(?:始终拒绝|不愿|不敢|未曾|始终)[^。；\n]{3,40})',
                r'([^，。；\n]{0,10}(?:其实|实际上|真相是|真相)[^。；\n]{3,40})',
                r'([^，。；\n]{0,10}(?:耿耿于怀|心事|愧疚|负罪)[^。；\n]{3,40})',
                r'([^，。；\n]{0,10}(?:被窃取|被夺走|失去|丧失)[^。；\n]{3,40})',
                r'([^，。；\n]{0,5}(?:但他|但她)[^。；\n]{0,5}(?:越|其实|从不)[^。；\n]{3,30})',
            ]
            for pat in patterns:
                m = re.search(pat, combined)
                if m:
                    secret = m.group(1).strip()
                    if len(secret) > 80:
                        secret = secret[:80] + "…"
                    c["secrets"] = secret
                    break
            if not c.get("secrets"):
                c["secrets"] = "故事中逐渐揭示的隐藏过往"

        # ── relationships: 从 backstory 提取与其他角色的关系 ──
        if not c.get("relationships"):
            rel_patterns = [
                # 亲属
                r'([^，。；\n]{0,10}(?:父亲|母亲|女儿|儿子|姐姐|妹妹|哥哥|弟弟|外婆|爷爷|奶奶|丈夫|妻子)[^。；\n]{3,40})',
                # 社交关系
                r'([^，。；\n]{0,10}(?:搭档|同事|朋友|上司|下属|对手|闺蜜|青梅竹马|导师|恩师|患者)[^。；\n]{3,30})',
                # 暗恋/恋爱
                r'([^，。；\n]{0,10}(?:暗恋|相恋|喜欢|心动|恋[爱人])[^。；\n]{3,30})',
                # 通过其他角色名匹配
            ]
            rels = []
            for pat in rel_patterns:
                for m in re.finditer(pat, combined):
                    rel = m.group(1).strip()
                    if len(rel) > 60:
                        rel = rel[:60] + "…"
                    rels.append(rel)
                    if len(rels) >= 2:
                        break
                if rels:
                    break
            # 尝试通过其他角色名匹配关系
            if not rels:
                my_name = c.get("name", "")
                for other in all_names:
                    if other and other != my_name and other in backstory:
                        # 查找该角色名附近的上下文
                        idx = backstory.find(other)
                        start = max(0, idx - 15)
                        end = min(len(backstory), idx + len(other) + 30)
                        snippet = backstory[start:end].strip()
                        rels.append(snippet)
                        if len(rels) >= 2:
                            break
            if rels:
                c["relationships"] = "；".join(rels)
            else:
                c["relationships"] = "与其他角色在故事进程中逐步建立联系"

        # ── appearance: 从 personality 中提取外貌/气质相关描述 ──
        if not c.get("appearance"):
            patterns = [
                r'([^，。；\n]{0,10}(?:外表|外貌|穿着|装扮|形象|随[^，。\n]{0,6}(?:携带|带着))[。；\n]{3,40})',
                r'([^，。；\n]{0,5}(?:眼神|笑容|声音|表情|目光|气质)[^。；\n]{3,35})',
                r'([^，。；\n]{0,5}(?:老人|老太|青年|年轻人|女孩|男孩|大叔)[^。；\n]{3,25})',
                r'([^，。；\n]{0,5}(?:饱经沧桑|温柔|知性|阳光|沉稳)[^。；\n]{0,5}(?:外表|气质|形象|感觉)[^。；\n]{3,20})',
            ]
            for pat in patterns:
                m = re.search(pat, personality)
                if m:
                    c["appearance"] = m.group(1).strip()
                    break
            if not c.get("appearance"):
                # 从 voice_tone（已在 speakingStyle 中）提取气质
                style = c.get("speakingStyle", "")
                if style:
                    style_match = re.search(r'([^，。；\n]{2,15}(?:温暖|冷静|沉稳|温柔|爽朗|沧桑|低沉|活泼)[^。；\n]{0,15})', style)
                    if style_match:
                        c["appearance"] = f"说话{style_match.group(1).strip()}"
            if not c.get("appearance"):
                c["appearance"] = f"符合{role.split('/')[0].strip()}身份的外表"

        # ── arc: 从 backstory 提取角色变化趋势 ──
        if not c.get("arc"):
            if backstory:
                patterns = [
                    r'([^，。；\n]{0,5}(?:发现|意识到|觉醒|改变|成长|面对|渐渐)[^。；\n]{3,40})',
                ]
                for pat in patterns:
                    m = re.search(pat, backstory)
                    if m:
                        c["arc"] = m.group(1).strip()
                        break
            if not c.get("arc"):
                c["arc"] = f"从{role.split('/')[0].strip()}身份出发，经历内心冲突后做出关键抉择"


def load_story(path: str) -> dict:
    """
    从剧本目录加载所有数据（兼容 harness/story_loader.load_story）。
    返回结构化剧本字典，包含全部剧本信息。
    """
    story: dict = {
        "title": "",
        "characters": [],
        "scenes": [],
        "stages": [],
        "world_lines": [],
        "convergence_points": [],
        "worldline_rules": [],
        "key_choices": [],
        # 新增字段
        "narrative_style": "",
        "genre": "",
        "worldbook": "",
        "character_relationships": [],
        "events": [],
        "endings": [],
        "plot_summary": "",
        "core_conflict": "",
        "themes": [],
        "beats": [],
        "hooks": [],
    }

    # 元数据
    meta_file = _find_yaml(path, "元数据")
    if meta_file:
        meta = _load_yaml(meta_file)
        story["title"] = meta.get("title", "未命名剧本")
        story["narrative_style"] = str(meta.get("narrative_style", "")).strip()
        story["genre"] = meta.get("genre", "")

    # 角色
    char_file = _find_yaml(path, "主要角色") or _find_yaml(path, "角色")
    if char_file:
        data = _load_yaml(char_file)
        for c in data.get("characters", []):
            # personality: 提取标签（与 _normalize_character 统一）
            personality = str(c.get("personality", "")).strip()
            if "\n" in personality:
                tags = re.findall(r'[-•]\s*([^：:\n]+)[：:]?', personality)
                personality = "、".join(t.strip() for t in tags[:4]) if tags else personality[:80]

            # speaking_style: 说话风格（voice_tone / speaking_style）
            speaking_style = str(c.get("voice_tone", c.get("speaking_style", ""))).strip()
            if "\n" in speaking_style:
                speaking_style = speaking_style.split("\n")[0][:80]

            # backstory: 角色背景故事
            backstory = str(c.get("backstory", c.get("background", ""))).strip()
            if len(backstory) > 150:
                backstory = backstory[:150] + "…"

            # motivation: 角色动机
            motivation = str(c.get("motivation", "")).strip()
            if "\n" in motivation:
                lines = [l.strip().lstrip("- ") for l in motivation.split("\n") if l.strip()][:2]
                motivation = "；".join(lines)
            if len(motivation) > 100:
                motivation = motivation[:100] + "…"

            # secrets: 角色秘密
            secrets_raw = c.get("secrets", [])
            if isinstance(secrets_raw, list):
                secrets = "；".join(str(s).strip() for s in secrets_raw[:3])
            else:
                secrets = str(secrets_raw).strip()
            if len(secrets) > 120:
                secrets = secrets[:120] + "…"

            # relationships: 角色关系
            rels_raw = c.get("relationships", [])
            relationships = []
            if isinstance(rels_raw, list):
                for r in rels_raw[:4]:
                    if isinstance(r, dict):
                        relationships.append(
                            f"{r.get('target', '')}（{r.get('type', '')}）：{r.get('description', '')}"
                        )
                    elif isinstance(r, str):
                        relationships.append(r)
            rel_str = "；".join(relationships) if relationships else ""
            if len(rel_str) > 150:
                rel_str = rel_str[:150] + "…"

            # appearance: 外貌
            appearance = str(c.get("appearance", "")).strip()
            if "\n" in appearance:
                appearance = "；".join(l.strip() for l in appearance.split("\n") if l.strip())
            if len(appearance) > 120:
                appearance = appearance[:120] + "…"

            # arc: 成长弧线
            arc = str(c.get("arc", "")).strip()
            if "\n" in arc:
                lines = [l.strip() for l in arc.split("\n") if l.strip()]
                arc = " → ".join(lines[:3])
            if len(arc) > 100:
                arc = arc[:100] + "…"

            story["characters"].append({
                "id": c.get("id", c.get("name", "")),
                "name": c.get("name", ""),
                "role": c.get("role", "") + (f"，{c.get('occupation', '')}" if c.get("occupation") else ""),
                "personality": personality if personality else "未定义",
                "speakingStyle": speaking_style,
                "background": backstory,
                "motivation": motivation,
                "secrets": secrets,
                "relationships": rel_str,
                "appearance": appearance,
                "arc": arc,
            })

    # 场景
    scene_file = _find_yaml(path, "场景清单") or _find_yaml(path, "场景")
    if scene_file:
        data = _load_yaml(scene_file)
        story["scenes"] = data.get("scenes", [])

    # 主线阶段
    plot_file = _find_yaml(path, "主线剧情") or _find_yaml(path, "主线")
    if plot_file:
        plot = _load_yaml(plot_file).get("main_plot", {})
        story["stages"] = plot.get("stages", [])

    # 世界线
    wl_file = _find_yaml(path, "世界线")
    if wl_file:
        wl_data = _load_yaml(wl_file)
        story["world_lines"] = wl_data.get("world_lines", [])
        story["convergence_points"] = wl_data.get("convergence_points", [])
        story["worldline_rules"] = wl_data.get("rules", [])

    # 关键选择
    choice_file = _find_yaml(path, "关键选择")
    if choice_file:
        story["key_choices"] = _load_yaml(choice_file).get("choices", [])

    # 角色关系
    rel_file = _find_yaml(path, "角色关系")
    if rel_file:
        rel_data = _load_yaml(rel_file)
        story["character_relationships"] = rel_data.get("relationships", [])

    # 事件清单
    event_file = _find_yaml(path, "事件清单")
    if event_file:
        story["events"] = _load_yaml(event_file).get("events", [])

    # 结局系统
    ending_file = _find_yaml(path, "结局")
    if ending_file:
        story["endings"] = _load_yaml(ending_file).get("endings", [])

    # 世界书（Markdown）
    wb_file = _find_file(path, "世界书", ".md")
    if wb_file:
        with open(wb_file, "r", encoding="utf-8") as f:
            story["worldbook"] = f.read()

    # 主线剧情扩展：summary + core_conflict + themes + beats + hooks
    if plot_file:
        plot = _load_yaml(plot_file).get("main_plot", {})
        story["plot_summary"] = str(plot.get("summary", "")).strip()
        story["core_conflict"] = str(plot.get("core_conflict", "")).strip()
        story["themes"] = plot.get("themes", [])
        story["beats"] = plot.get("beats", [])
        story["hooks"] = plot.get("hooks", [])

    # 角色缺失字段降级推断
    _fill_character_gaps(story["characters"])

    return story


def get_scene_characters(story: dict, scene: dict) -> list[dict]:
    """获取当前场景在场角色的角色卡列表"""
    present = scene.get("characters_present", [])
    char_map = {c["id"]: c for c in story["characters"]}
    name_map = {c["name"]: c for c in story["characters"]}
    result = []
    for ref in present:
        if ref in char_map:
            result.append(char_map[ref])
        elif ref in name_map:
            result.append(name_map[ref])
    return result if result else story["characters"][:2]


def build_worldline(story: dict, scene_index: int) -> str:
    """根据主线阶段构建世界线字符串，标注当前节点"""
    stages = story.get("stages", [])
    if not stages:
        return f"第{scene_index + 1}幕"
    names = [s.get("name", "") for s in stages]
    current = min(scene_index, len(names) - 1)
    return "→".join(f"{'【' + n + '】' if i == current else n}" for i, n in enumerate(names))


def build_scene_text(scene: dict) -> str:
    """将场景字典格式化为场景描述文本"""
    parts = []
    if scene.get("name"):
        parts.append(f"【场景：{scene['name']}】")
    for key in ("location", "time", "atmosphere"):
        if scene.get(key):
            parts.append(f"{key}：{scene[key]}")
    parts.append("")
    desc = scene.get("description", "")
    parts.append(str(desc).strip() if desc else "")
    return "\n".join(parts)
