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

        return {
            "name": str(name),
            "role": str(role),
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
            # 只包含本场景在场的角色
            characters = [c for c in self.characters if c["name"] in chars_present]
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
        harness_input["_emotionalArc"] = ""

        return harness_input

    def get_scene_choices(self, scene_index: int) -> list[dict]:
        """获取场景的预设选择项"""
        if scene_index >= len(self.scenes):
            return []
        scene = self.scenes[scene_index]
        raw_choices = scene.get("choices", [])
        result = []
        for c in raw_choices:
            if isinstance(c, dict):
                result.append({
                    "id": c.get("id", ""),
                    "text": c.get("text", c.get("label", "")),
                    "description": c.get("description", c.get("effect", "")),
                })
            elif isinstance(c, str):
                result.append({"id": str(len(result)), "text": c, "description": ""})
        return result

    def _extract_worldbook_rules(self) -> str:
        """从世界书提取关键约束"""
        if not self.worldbook:
            return ""
        rules = []
        for section_name in ["必须遵守", "禁止出现", "情感基调", "故事边界"]:
            match = re.search(rf'###?\s*{section_name}\s*\n(.*?)(?=###?|\Z)', self.worldbook, re.DOTALL)
            if match:
                rules.append(f"【{section_name}】{match.group(1).strip()}")
        return "\n\n".join(rules) if rules else self.worldbook[:500]

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

    # 主线剧情扩展：summary + core_conflict
    if plot_file:
        plot = _load_yaml(plot_file).get("main_plot", {})
        story["plot_summary"] = str(plot.get("summary", "")).strip()
        story["core_conflict"] = str(plot.get("core_conflict", "")).strip()

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
