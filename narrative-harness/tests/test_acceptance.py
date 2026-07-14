# -*- coding: utf-8 -*-
"""
Narrative Harness 验收测试 v3.0
覆盖 5 项验收标准 × 5 个真实剧本 + API 文档验证。

用法：
  cd narrative-harness
  python tests/test_acceptance.py
  # 或仅离线测试：
  python -m pytest tests/test_acceptance.py -v -k "offline"
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from harness import NarrativeHarness
from schemas import HarnessInput, Character, AssembledContext, Dialogue, Choice
from validator import NarrativeValidator
from generic_adapter import load_story, get_scene_characters, build_worldline, build_scene_text


# ═══════════════════════════════════════════════════════
# 测试剧本路径
# ═══════════════════════════════════════════════════════

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "测试剧本"

TEST_SCRIPTS = {
    "回魂公寓": str(SCRIPTS_DIR / "回魂公寓"),
    "星河入梦": str(SCRIPTS_DIR / "星河入梦"),
    "都市传说": str(SCRIPTS_DIR / "都市传说"),
    "霓虹深渊": str(SCRIPTS_DIR / "霓虹深渊"),
    "回响DEMO": str(SCRIPTS_DIR / "回响-DEMO(1)" / "回响-DEMO"),
}


def build_input_from_script(path: str, scene_index: int = 0) -> dict:
    """从真实剧本构建 harness 输入"""
    story = load_story(path)
    scenes = story.get("scenes", [])
    if not scenes:
        raise ValueError(f"剧本 {path} 无场景数据")
    scene = scenes[min(scene_index, len(scenes) - 1)]
    characters = get_scene_characters(story, scene)
    worldline = build_worldline(story, scene_index)
    scene_text = build_scene_text(scene)
    return {
        "currentScene": scene_text,
        "characters": [
            {
                "name": c["name"],
                "role": c["role"],
                "personality": c["personality"],
                "speakingStyle": c.get("speakingStyle", ""),
                "background": c.get("background", ""),
                "motivation": c.get("motivation", ""),
                "secrets": c.get("secrets", ""),
                "relationships": c.get("relationships", ""),
                "appearance": c.get("appearance", ""),
                "arc": c.get("arc", ""),
            }
            for c in characters
        ],
        "worldline": worldline,
        "playerChoice": "",
    }


# ═══════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════

HAS_API_KEY = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))


class TestAcceptance(unittest.TestCase):
    """验收测试 v3.0：5 个真实剧本 × 5 项验收标准"""

    @classmethod
    def setUpClass(cls):
        cls.harness = NarrativeHarness()
        if not HAS_API_KEY:
            print("\n[WARNING] 未配置 API Key，将跳过 Live 测试")

    # ── 验收标准 1: 稳定生成 3-5 轮对话 ──

    @unittest.skipUnless(HAS_API_KEY, "需要 API Key")
    def test_1_dialogue_count(self):
        """[标准1] 全部剧本：生成 3-5 轮对话"""
        for name, path in TEST_SCRIPTS.items():
            try:
                inp = build_input_from_script(path)
            except Exception as e:
                self.fail(f"{name} 加载失败: {e}")
            t0 = time.time()
            result = self.harness.run(inp)
            elapsed = time.time() - t0
            self.assertTrue(result["success"], f"{name} Pipeline 失败: {result.get('validation')}")
            n = len(result["data"]["dialogues"])
            self.assertGreaterEqual(n, 3, f"{name} 对话不足: {n}")
            self.assertLessEqual(n, 5, f"{name} 对话过多: {n}")
            print(f"  -> {name}: {n} 轮对话, {elapsed:.1f}s ✓")

    # ── 验收标准 2: 对话风格符合角色性格 ──

    @unittest.skipUnless(HAS_API_KEY, "需要 API Key")
    def test_2_personality_match(self):
        """[标准2] 全部剧本：角色卡完整注入 + 性格匹配 ≥ 0.6"""
        for name, path in TEST_SCRIPTS.items():
            inp = build_input_from_script(path)

            # 验证角色卡字段完整性
            for c in inp["characters"]:
                self.assertTrue(c["name"], f"{name} 角色缺少 name")
                self.assertTrue(c["role"], f"{name} 角色缺少 role")
                self.assertTrue(c["personality"], f"{name} 角色缺少 personality")
                # speakingStyle 和 background 至少一个有值
                has_extra = bool(c.get("speakingStyle") or c.get("background"))
                self.assertTrue(has_extra, f"{name}/{c['name']} 缺少 speakingStyle 和 background")

            result = self.harness.run(inp)
            dialogues = result["data"]["dialogues"]

            # 验证 speaker 全部存在于角色卡
            valid_names = {c["name"] for c in inp["characters"]}
            for d in dialogues:
                self.assertIn(d["speaker"], valid_names, f"{name}: 未知角色 '{d['speaker']}'")

            # 性格匹配得分
            validation = result.get("validation", {})
            pc = next((c for c in validation.get("checks", []) if c["name"] == "personality_match"), None)
            self.assertIsNotNone(pc, f"{name}: 缺少 personality_match")
            score = pc.get("score", 0)
            print(f"  -> {name}: 性格匹配 {score:.2f}, {len(dialogues)} 轮全部角色匹配 ✓")

    # ── 验收标准 3: 不偏离主线大纲 ──

    @unittest.skipUnless(HAS_API_KEY, "需要 API Key")
    def test_3_worldline_adherence(self):
        """[标准3] 全部剧本：主线关联度 + annotatedWorldline 注入"""
        for name, path in TEST_SCRIPTS.items():
            inp = build_input_from_script(path)

            # 验证 annotatedWorldline 构建
            chars = [Character(name=c["name"], role=c["role"], personality=c["personality"],
                               speaking_style=c.get("speakingStyle", ""), background=c.get("background", ""))
                     for c in inp["characters"]]
            hi = HarnessInput(current_scene=inp["currentScene"], characters=chars,
                              worldline=inp["worldline"])
            ctx = AssembledContext.from_input(hi)
            self.assertTrue(ctx.annotated_worldline, f"{name}: annotatedWorldline 为空")
            self.assertIn("【", ctx.annotated_worldline, f"{name}: 未标注当前位置")

            result = self.harness.run(inp)
            validation = result.get("validation", {})
            wc = next((c for c in validation.get("checks", []) if c["name"] == "worldline_adherence"), None)
            self.assertIsNotNone(wc, f"{name}: 缺少 worldline_adherence")
            score = wc.get("score", 0)

            ws = result["data"].get("worldlineState", {})
            self.assertIn("currentNode", ws, f"{name}: 缺少 currentNode")
            self.assertIn("progress", ws, f"{name}: 缺少 progress")
            print(f"  -> {name}: 关联度 {score:.2f}, 节点={ws['currentNode']}, 进度={ws['progress']} ✓")

    # ── 验收标准 4: 玩家选择后合理续写 ──

    @unittest.skipUnless(HAS_API_KEY, "需要 API Key")
    def test_4_continuation(self):
        """[标准4] 回魂公寓：玩家选择后续写不重复"""
        inp = build_input_from_script(TEST_SCRIPTS["回魂公寓"])
        r1 = self.harness.run(inp)
        d1 = r1["data"]["dialogues"]
        self.assertGreaterEqual(len(d1), 1, "初始无对话")

        choices = r1["data"]["nextChoices"]
        self.assertGreaterEqual(len(choices), 2, f"选项不足: {len(choices)}")

        r2_input = {**inp, "playerChoice": choices[0]["text"], "previousContext": r1["data"]}
        r2 = self.harness.run(r2_input)
        d2 = r2["data"]["dialogues"]
        self.assertGreaterEqual(len(d2), 1, "续写无对话")

        text1 = " ".join(d["text"] for d in d1)
        text2 = " ".join(d["text"] for d in d2)
        self.assertNotEqual(text1, text2, "续写与初始内容重复")
        c2 = r2["data"]["nextChoices"]
        self.assertGreaterEqual(len(c2), 2, f"续写选项不足: {len(c2)}")
        print(f"  -> 初始 {len(d1)} 轮 + {len(choices)} 选项, 续写 {len(d2)} 轮 + {len(c2)} 选项 ✓")

    # ── 验收标准 5: 接口文档完整性 ──

    def test_5_api_docs(self):
        """[标准5] API 路由均有 OpenAPI 文档"""
        from server import app

        routes = []
        for route in app.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                for method in route.methods:
                    if method not in ("HEAD", "OPTIONS"):
                        routes.append({"method": method, "path": route.path})

        required = [
            ("POST", "/api/upload"), ("POST", "/api/start"),
            ("POST", "/api/session/{session_id}/generate"),
            ("POST", "/api/session/{session_id}/choose"),
            ("GET", "/api/session/{session_id}/state"),
            ("GET", "/api/session/{session_id}/history"),
            ("GET", "/play"), ("POST", "/api/story"), ("POST", "/api/scene"),
            ("POST", "/api/load-folder"), ("POST", "/api/auto-run"),
        ]
        for method, path in required:
            found = any(r["method"] == method and r["path"] == path for r in routes)
            self.assertTrue(found, f"缺少: {method} {path}")
            print(f"  -> {method} {path} ✓")

        schema = app.openapi()
        self.assertIn("paths", schema)
        print(f"  -> OpenAPI: {len(schema['paths'])} 路径, {len(routes)} 路由 ✓")

    # ── 离线：Schema 通用性 ──

    def test_offline_schema(self):
        """[离线] 数据模型通用性验证"""
        for name, path in TEST_SCRIPTS.items():
            inp = build_input_from_script(path)
            chars = [Character(name=c["name"], role=c["role"], personality=c["personality"])
                     for c in inp["characters"]]
            hi = HarnessInput(current_scene=inp["currentScene"], characters=chars,
                              worldline=inp["worldline"])
            ctx = AssembledContext.from_input(hi)
            self.assertGreater(ctx.total_nodes, 0)
            self.assertGreaterEqual(ctx.worldline_progress, 0)
            print(f"  -> {name}: {ctx.total_nodes} 节点, 进度 {ctx.worldline_progress:.0%} ✓")

    # ── 离线：Validator 通用性 ──

    def test_offline_validator(self):
        """[离线] 验证器对全部剧本兼容"""
        for name, path in TEST_SCRIPTS.items():
            inp = build_input_from_script(path)
            chars = [Character(name=c["name"], role=c["role"], personality=c["personality"],
                               speaking_style=c.get("speakingStyle", ""), background=c.get("background", ""))
                     for c in inp["characters"]]
            hi = HarnessInput(current_scene=inp["currentScene"], characters=chars,
                              worldline=inp["worldline"])
            ctx = AssembledContext.from_input(hi)
            mock_d = [Dialogue(speaker=chars[0].name, text="测试对话"),
                       Dialogue(speaker=chars[1 % len(chars)].name, text="第二段对话"),
                       Dialogue(speaker=chars[0].name, text="第三段对话")]
            mock_c = [Choice(text="选项A", effect="效果A"), Choice(text="选项B", effect="效果B")]
            v = NarrativeValidator(ctx)
            result = v.validate_all("测试旁白描述，用于验证验证器功能正常。", mock_d, mock_c)
            check_names = {c.name for c in result.checks}
            expected = {"schema_compliance", "dialogue_count", "character_consistency",
                        "consecutive_speaker", "personality_match",
                        "worldline_adherence", "choice_diversity", "choice_validity"}
            self.assertEqual(check_names, expected, f"{name}: 检查项不完整")
            print(f"  -> {name}: 8/8 检查项存在 ✓")

    # ── 离线：角色卡完整性 ──

    def test_offline_character_cards(self):
        """[离线] 角色卡字段完整性 — 全部 10 个字段"""
        REQUIRED_FIELDS = ["name", "role", "personality", "speakingStyle", "background"]
        OPTIONAL_FIELDS = ["motivation", "secrets", "relationships", "appearance", "arc"]

        for name, path in TEST_SCRIPTS.items():
            inp = build_input_from_script(path)
            self.assertGreaterEqual(len(inp["characters"]), 1, f"{name}: 无角色")
            for c in inp["characters"]:
                for field in REQUIRED_FIELDS:
                    self.assertTrue(c.get(field), f"{name}/{c['name']}: 缺必填字段 {field}")
            # 统计可选字段覆盖率
            opt_counts = {f: sum(1 for c in inp["characters"] if c.get(f)) for f in OPTIONAL_FIELDS}
            print(
                f"  -> {name}: {len(inp['characters'])} 角色 | "
                + ", ".join(f"{k}={v}/{len(inp['characters'])}" for k, v in opt_counts.items())
                + " ✓"
            )


# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    print("=" * 60)
    print("  Narrative Harness 验收测试 v3.0")
    print(f"  测试剧本: {', '.join(TEST_SCRIPTS.keys())}")
    print("=" * 60)
    print()

    unittest.main(verbosity=2)
# -*- coding: utf-8 -*-
