# -*- coding: utf-8 -*-
"""
回响剧本适配器
动态读取《回响》YAML 剧本文件，转换为 Narrative Harness 输入格式
不写死任何剧情内容，完全由剧本数据驱动
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml


class EchoScriptAdapter:
    """
    回响剧本适配器
    从 YAML 剧本文件动态构建 Harness 输入，不硬编码任何剧情内容
    """

    def __init__(self, script_dir: str):
        self.script_dir = Path(script_dir)
        self._load_all()

    def _load_all(self):
        """加载所有剧本 YAML 文件"""
        self.metadata = self._load_yaml("剧本元数据.yaml")
        self.main_plot = self._load_yaml("剧情/主线剧情.yaml")
        self.worldline = self._load_yaml("剧情/世界线.yaml")
        self.events = self._load_yaml("剧情/事件/事件清单.yaml")
        self.choices = self._load_yaml("剧情/事件/关键选择.yaml")
        self.endings = self._load_yaml("剧情/结局系统.yaml")
        self.characters_data = self._load_yaml("角色/主要角色.yaml")
        self.relationships = self._load_yaml("角色/角色关系.yaml")
        self.scenes_data = self._load_yaml("场景/场景清单.yaml")
        self.assets = self._load_yaml("素材/素材清单.yaml")

        # 加载世界书（markdown）
        worldbook_path = self.script_dir / "世界书.md"
        self.worldbook = ""
        if worldbook_path.exists():
            with open(worldbook_path, "r", encoding="utf-8") as f:
                self.worldbook = f.read()

        # 构建索引
        self._build_indexes()

    def _load_yaml(self, relative_path: str) -> dict:
        path = self.script_dir / relative_path
        if not path.exists():
            print(f"[WARNING] 文件不存在: {path}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _build_indexes(self):
        """构建快速查找索引"""
        # 角色索引: id -> character data
        self.char_index = {}
        for c in self.characters_data.get("characters", []):
            self.char_index[c["id"]] = c

        # 场景索引: id -> scene data
        self.scene_index = {}
        for s in self.scenes_data.get("scenes", []):
            self.scene_index[s["id"]] = s

        # 选择索引: id -> choice data
        self.choice_index = {}
        for c in self.choices.get("choices", []):
            self.choice_index[c["id"]] = c

        # 事件索引: id -> event data
        self.event_index = {}
        for e in self.events.get("events", []):
            self.event_index[e["id"]] = e

        # 结局索引: id -> ending data
        self.ending_index = {}
        for e in self.endings.get("endings", []):
            self.ending_index[e["id"]] = e

        # 世界线索引
        self.worldline_index = {}
        for wl in self.worldline.get("world_lines", []):
            self.worldline_index[wl["id"]] = wl

    def get_scene_ids(self) -> list[str]:
        """获取所有场景 ID（按顺序）"""
        return [s["id"] for s in self.scenes_data.get("scenes", [])]

    def get_worldline_path(self, line_id: str = "main_line") -> list[str]:
        """获取指定世界线的场景路径"""
        wl = self.worldline_index.get(line_id, {})
        path = wl.get("path", [])
        return [p["scene_id"] for p in sorted(path, key=lambda x: x.get("sequence", 0))]

    def build_worldline_string(self, line_id: str = "main_line") -> str:
        """
        从世界线数据动态构建世界线字符串
        例如: "信号 → 碎片 → 对峙 → 回响"
        """
        scene_ids = self.get_worldline_path(line_id)
        names = []
        for sid in scene_ids:
            scene = self.scene_index.get(sid, {})
            names.append(scene.get("name", sid))
        return " → ".join(names) if names else ""

    def build_character_card(self, char_id: str, scene_id: str = "") -> dict:
        """
        从角色 YAML 数据动态构建角色卡
        根据场景上下文调整角色状态
        """
        char = self.char_index.get(char_id, {})
        if not char:
            return {}

        # 提取性格特征（从 personality 字段解析）
        personality_raw = char.get("personality", "")
        personality = self._extract_personality_tags(personality_raw)

        # 提取说话风格
        voice = char.get("voice_tone", "")
        speaking_style = self._summarize_voice(voice)

        # 提取背景故事摘要
        background = char.get("background", "")
        background_summary = self._summarize_text(background, max_chars=100)

        card = {
            "name": char.get("name", char_id),
            "role": char.get("occupation", char.get("role", "")),
            "personality": personality,
            "speakingStyle": speaking_style,
            "background": background_summary,
        }

        # 根据场景上下文调整情绪
        if scene_id:
            card["mood"] = self._infer_mood(char_id, scene_id)

        return card

    def build_harness_input(
        self,
        scene_id: str,
        player_choice: str = "",
        previous_context: dict = None,
        worldline_id: str = "main_line",
    ) -> dict:
        """
        构建完整的 Harness 输入
        完全从剧本数据动态生成，不硬编码任何内容
        """
        scene = self.scene_index.get(scene_id, {})
        if not scene:
            raise ValueError(f"场景不存在: {scene_id}")

        # 1. 构建场景描述（从剧本的场景数据动态生成）
        current_scene = self._build_scene_description(scene)

        # 2. 构建角色卡列表（只包含本场景在场的角色）
        characters_present = scene.get("characters_present", [])
        characters = []
        for char_id in characters_present:
            card = self.build_character_card(char_id, scene_id)
            if card:
                characters.append(card)

        # 3. 构建世界线字符串
        worldline_str = self.build_worldline_string(worldline_id)

        # 4. 获取本场景的关键选择提示（如果有）
        critical_choice_id = scene.get("critical_choice")
        if critical_choice_id and not player_choice:
            choice_data = self.choice_index.get(critical_choice_id, {})
            # 不预设选择，让 AI 根据场景生成选择项

        # 5. 组装 Harness 输入
        harness_input = {
            "currentScene": current_scene,
            "characters": characters,
            "worldline": worldline_str,
            "playerChoice": player_choice,
        }

        if previous_context:
            harness_input["previousContext"] = previous_context

        # 6. 注入世界书约束（作为额外上下文）
        harness_input["_worldbook"] = self._extract_worldbook_rules()
        harness_input["_narrativeStyle"] = self.metadata.get("narrative_style", "")
        harness_input["_sceneAtmosphere"] = scene.get("atmosphere", "")
        harness_input["_narrativeNotes"] = scene.get("narrative_notes", "")
        harness_input["_emotionalArc"] = self._get_emotional_arc(scene_id)

        return harness_input

    def build_continuation_input(
        self,
        scene_id: str,
        choice_text: str,
        previous_result: dict,
    ) -> dict:
        """
        构建续写输入：玩家做出选择后，进入下一阶段
        """
        previous_context = previous_result.get("data", {})
        return self.build_harness_input(
            scene_id=scene_id,
            player_choice=choice_text,
            previous_context=previous_context,
        )

    # ───────────────────────────────────────────────────
    # 辅助方法
    # ───────────────────────────────────────────────────

    def _build_scene_description(self, scene: dict) -> str:
        """从场景 YAML 数据构建场景描述"""
        parts = []

        # 基本信息
        name = scene.get("name", "")
        location = scene.get("location", "")
        time = scene.get("time", "")
        if name:
            parts.append(f"【{name}】")
        if location:
            parts.append(f"地点：{location}")
        if time:
            parts.append(f"时间：{time}")

        # 场景描述
        desc = scene.get("description", "")
        if desc:
            parts.append(desc.strip())

        return "\n".join(parts)

    def _extract_personality_tags(self, personality_text: str) -> str:
        """从角色 personality 字段提取性格标签"""
        if not personality_text:
            return ""
        # 匹配 "- xxx：" 或 "- xxx:" 格式
        tags = re.findall(r'-\s*([^：:：]+)[：:]', personality_text)
        if tags:
            return "、".join(t.strip() for t in tags[:4])
        # 回退：取前 50 字
        return personality_text[:50].replace("\n", " ")

    def _summarize_voice(self, voice_text: str) -> str:
        """从 voice_tone 字段提取说话风格摘要"""
        if not voice_text:
            return ""
        # 取第一行或前 60 字
        first_line = voice_text.strip().split("\n")[0]
        return first_line[:80] if len(first_line) > 80 else first_line

    def _summarize_text(self, text: str, max_chars: int = 100) -> str:
        """截断长文本为摘要"""
        if not text:
            return ""
        text = text.strip().replace("\n", " ")
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."

    def _infer_mood(self, char_id: str, scene_id: str) -> str:
        """根据角色和场景推断情绪状态"""
        # 从主线剧情的 emotional_arc 推断
        scene = self.scene_index.get(scene_id, {})
        if not scene:
            return ""

        # 查找该场景所属的 stage
        stages = self.main_plot.get("main_plot", {}).get("stages", [])
        for stage in stages:
            if scene_id in stage.get("scenes", []):
                arc = stage.get("emotional_arc", "")
                # 解析情绪弧线，取中间或末尾状态
                emotions = [e.strip() for e in re.split(r'[→>]', arc)]
                if emotions:
                    return emotions[-1] if len(emotions) > 1 else emotions[0]

        return ""

    def _get_emotional_arc(self, scene_id: str) -> str:
        """获取场景对应的情绪弧线"""
        stages = self.main_plot.get("main_plot", {}).get("stages", [])
        for stage in stages:
            if scene_id in stage.get("scenes", []):
                return stage.get("emotional_arc", "")
        return ""

    def _extract_worldbook_rules(self) -> str:
        """从世界书中提取 AI 生成约束规则"""
        if not self.worldbook:
            return ""

        # 提取"故事边界"和"情感基调"部分
        rules = []

        # 找"必须遵守"部分
        must_match = re.search(r'###\s*必须遵守\s*\n(.*?)(?=###|##|\Z)', self.worldbook, re.DOTALL)
        if must_match:
            rules.append("【必须遵守】" + must_match.group(1).strip())

        # 找"禁止出现"部分
        ban_match = re.search(r'###\s*禁止出现\s*\n(.*?)(?=###|##|\Z)', self.worldbook, re.DOTALL)
        if ban_match:
            rules.append("【禁止出现】" + ban_match.group(1).strip())

        # 找"情感基调"部分
        tone_match = re.search(r'##\s*情感基调\s*\n(.*?)(?=##|\Z)', self.worldbook, re.DOTALL)
        if tone_match:
            rules.append("【情感基调】" + tone_match.group(1).strip())

        return "\n\n".join(rules)

    def get_scene_choices(self, scene_id: str) -> list[dict]:
        """获取指定场景的关键选择项"""
        scene = self.scene_index.get(scene_id, {})
        choice_id = scene.get("critical_choice")
        if not choice_id:
            return []
        choice_data = self.choice_index.get(choice_id, {})
        options = choice_data.get("options", [])
        return [
            {
                "id": opt.get("id", ""),
                "text": opt.get("text", ""),
                "description": opt.get("description", ""),
                "consequences": opt.get("consequences", {}).get("immediate", ""),
            }
            for opt in options
        ]

    def print_script_summary(self):
        """打印剧本摘要信息"""
        title = self.metadata.get("title", "未知")
        genre = self.metadata.get("genre", "未知")
        duration = self.metadata.get("estimated_duration", "未知")
        replay = self.metadata.get("replay_value", "")

        print(f"{'=' * 60}")
        print(f"剧本: {title}")
        print(f"类型: {genre}")
        print(f"预计时长: {duration}")
        print(f"重玩价值: {replay}")
        print(f"{'=' * 60}")

        print(f"\n角色 ({len(self.char_index)}):")
        for cid, c in self.char_index.items():
            print(f"  - {c['name']} ({c.get('role', '')}) — {c.get('occupation', '')}")

        print(f"\n场景 ({len(self.scene_index)}):")
        for sid, s in self.scene_index.items():
            print(f"  - [{sid}] {s['name']} @ {s.get('location', '')}")

        print(f"\n世界线 ({len(self.worldline_index)}):")
        for wid, wl in self.worldline_index.items():
            path = self.build_worldline_string(wid)
            print(f"  - {wl['name']}: {path}")

        print(f"\n结局 ({len(self.ending_index)}):")
        for eid, e in self.ending_index.items():
            print(f"  - [{eid}] {e.get('title', '')} ({e.get('type', '')})")

        print(f"\n关键选择 ({len(self.choice_index)}):")
        for cid, c in self.choice_index.items():
            opts = [o['text'] for o in c.get('options', [])]
            print(f"  - [{cid}] {c.get('description', '')}: {opts}")


# ═══════════════════════════════════════════════════════
# CLI 入口：打印剧本摘要 + 生成测试输入
# ═══════════════════════════════════════════════════════

def main():
    import argparse
    import io
    import json
    import sys

    # Windows console UTF-8 fix (only in CLI entry point)
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="回响剧本适配器")
    parser.add_argument("script_dir", help="剧本目录路径")
    parser.add_argument("--scene", default="scene_01", help="要生成输入的场景ID")
    parser.add_argument("--worldline", default="main_line", help="世界线ID")
    parser.add_argument("--choice", default="", help="玩家选择文本")
    parser.add_argument("--summary", action="store_true", help="打印剧本摘要")
    parser.add_argument("--export", default="", help="导出 Harness 输入到 JSON 文件")
    args = parser.parse_args()

    adapter = EchoScriptAdapter(args.script_dir)

    if args.summary:
        adapter.print_script_summary()
        return

    # 生成 Harness 输入
    harness_input = adapter.build_harness_input(
        scene_id=args.scene,
        player_choice=args.choice,
        worldline_id=args.worldline,
    )

    # 清理内部字段（不传给 Harness）
    clean_input = {k: v for k, v in harness_input.items() if not k.startswith("_")}

    if args.export:
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(clean_input, f, ensure_ascii=False, indent=2)
        print(f"Harness 输入已导出到: {args.export}")
    else:
        print(json.dumps(clean_input, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
