# -*- coding: utf-8 -*-
"""
回响-DEMO 端到端数据使用验证
覆盖：数据读取完整性 + 数据注入运用 + 模板变量验证

用法: cd narrative-harness && python -m pytest tests/test_echo_e2e.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from generic_adapter import (
    GenericScriptAdapter,
    load_story, get_scene_characters, build_worldline, build_scene_text,
)

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "测试剧本", "回响-DEMO(1)", "回响-DEMO",
)


# ═══════════════════════════════════════════════════════
# Part 1: 数据读取完整性验证
# ═══════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def story():
    """加载回响-DEMO 剧本"""
    assert os.path.isdir(SCRIPT_PATH), f"剧本目录不存在: {SCRIPT_PATH}"
    return load_story(SCRIPT_PATH)


class TestDataLoading:
    """验证 load_story 对所有 11 个文件的加载完整性"""

    # -- 元数据 --
    def test_title(self, story):
        assert story["title"] == "回响", f"title={story['title']!r}"

    def test_narrative_style(self, story):
        assert story["narrative_style"], "narrative_style 为空"
        assert "第二人称" in story["narrative_style"]

    def test_genre(self, story):
        assert story["genre"], "genre 为空"

    # -- 角色 --
    def test_characters_count(self, story):
        assert len(story["characters"]) == 3, f"角色数={len(story['characters'])}"

    @pytest.mark.parametrize("field", [
        "id", "name", "role", "personality", "speakingStyle",
        "background", "motivation", "secrets", "relationships",
        "appearance", "arc",
    ])
    def test_character_fields(self, story, field):
        """每个角色的 10+ 个字段均非空"""
        for c in story["characters"]:
            val = c.get(field, "")
            assert val, f"角色 {c.get('name','?')} 的 {field} 为空"

    def test_character_names(self, story):
        names = {c["name"] for c in story["characters"]}
        assert names == {"苏然", "林晓", "杜维明"}, f"角色名={names}"

    # -- 场景 --
    def test_scenes_count(self, story):
        assert len(story["scenes"]) == 4, f"场景数={len(story['scenes'])}"

    def test_scene_ids(self, story):
        ids = [s.get("id", "") for s in story["scenes"]]
        assert ids == ["scene_01", "scene_02", "scene_03", "scene_04"]

    # -- 主线阶段 --
    def test_stages_count(self, story):
        assert len(story["stages"]) == 4, f"阶段数={len(story['stages'])}"

    def test_stage_names(self, story):
        names = [s.get("name", "") for s in story["stages"]]
        assert names == ["开端", "发现", "对峙", "结局"], f"阶段名={names}"

    # -- 世界线 --
    def test_world_lines_count(self, story):
        assert len(story["world_lines"]) == 3, f"世界线数={len(story['world_lines'])}"

    def test_convergence_points(self, story):
        assert len(story["convergence_points"]) == 2

    def test_worldline_rules(self, story):
        assert len(story["worldline_rules"]) == 3

    # -- 关键选择 --
    def test_key_choices_count(self, story):
        assert len(story["key_choices"]) == 3, f"关键选择数={len(story['key_choices'])}"

    def test_key_choices_have_options(self, story):
        for kc in story["key_choices"]:
            opts = kc.get("options", [])
            assert len(opts) >= 2, f"选择 {kc.get('id')} 选项不足: {len(opts)}"

    # -- 角色关系 --
    def test_character_relationships(self, story):
        assert len(story["character_relationships"]) == 3

    # -- 事件 --
    def test_events_count(self, story):
        assert len(story["events"]) == 4, f"事件数={len(story['events'])}"

    # -- 结局 --
    def test_endings_count(self, story):
        assert len(story["endings"]) == 3, f"结局数={len(story['endings'])}"

    def test_ending_types(self, story):
        types = {e.get("type", "") for e in story["endings"]}
        assert types == {"bad", "normal", "good"}, f"结局类型={types}"

    # -- 世界书 --
    def test_worldbook(self, story):
        assert len(story["worldbook"]) > 100, f"世界书过短: {len(story['worldbook'])} 字符"
        assert "记忆" in story["worldbook"]

    # -- 主线扩展字段 --
    def test_plot_summary(self, story):
        assert len(story["plot_summary"]) > 50

    def test_core_conflict(self, story):
        assert "真相" in story["core_conflict"] or "安全" in story["core_conflict"]

    def test_themes(self, story):
        assert len(story["themes"]) == 3

    def test_beats(self, story):
        assert len(story["beats"]) == 7, f"节拍数={len(story['beats'])}"

    def test_hooks(self, story):
        assert len(story["hooks"]) == 4, f"钩子数={len(story['hooks'])}"


# ═══════════════════════════════════════════════════════
# Part 2: 数据注入验证
# ═══════════════════════════════════════════════════════

# 从 server.py 复制格式化函数（避免导入 FastAPI 依赖）
# 这里直接 import server.py 的函数
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _import_server_formatters():
    """延迟导入 server.py 的格式化函数"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "server_fmt",
        os.path.join(os.path.dirname(__file__), "..", "src", "server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # 防止 server.py 启动 FastAPI app
    sys.modules["server_fmt"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass
    return mod


class TestDataInjection:
    """验证 harness_input 中的 _ 前缀字段是否正确注入"""

    @pytest.fixture(scope="class")
    def fmt(self):
        return _import_server_formatters()

    @pytest.fixture(scope="class")
    def story_data(self):
        return load_story(SCRIPT_PATH)

    def _build_harness_input(self, story, scene_idx, fmt):
        """模拟 auto_run 的 harness_input 构建"""
        scenes = story.get("scenes", [])
        scene = scenes[scene_idx]
        scene_id = scene.get("id", scene.get("scene_id", ""))
        characters = get_scene_characters(story, scene)
        worldline = build_worldline(story, scene_idx)
        scene_text = build_scene_text(scene)

        current_stage = fmt._get_current_stage(story.get("stages", []), scene_id)

        return {
            "currentScene": scene_text,
            "characters": characters,
            "worldline": worldline,
            "_worldbook": fmt._extract_worldbook_rules(story.get("worldbook", "")),
            "_narrativeStyle": story.get("narrative_style", "") or "第二人称叙事",
            "_plotSummary": story.get("plot_summary", ""),
            "_coreConflict": story.get("core_conflict", ""),
            "_themes": fmt._format_themes(story.get("themes", [])),
            "_relationshipNetwork": fmt._format_relationships(story.get("character_relationships", [])),
            "_stagesOverview": fmt._format_stages_overview(story.get("stages", [])),
            "_events": fmt._format_events(story.get("events", [])),
            "_keyChoices": fmt._format_key_choices(story.get("key_choices", [])),
            "_endings": fmt._format_endings(story.get("endings", [])),
            "_sceneDetails": fmt._format_scene_details(scene, story.get("key_choices", [])),
            "_worldlines": fmt._format_worldlines(
                story.get("world_lines", []),
                story.get("convergence_points", []),
                story.get("worldline_rules", []),
            ),
            "_currentStageContext": fmt._format_current_stage_context(current_stage),
            "_sceneBeats": fmt._format_beats_for_scene(story.get("beats", []), scene_id),
            "_sceneHooks": fmt._format_hooks_for_scene(story.get("hooks", []), scene_id),
        }

    # -- 全局字段非空验证 --
    @pytest.mark.parametrize("field", [
        "_worldbook", "_narrativeStyle", "_plotSummary", "_coreConflict",
        "_themes", "_relationshipNetwork", "_stagesOverview",
        "_events", "_keyChoices", "_endings", "_worldlines",
    ])
    def test_global_fields_nonempty(self, story_data, fmt, field):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert hi[field], f"{field} 为空"

    # -- 场景专属字段验证 --
    def test_scene01_stage_context(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert "开端" in hi["_currentStageContext"]

    def test_scene02_stage_context(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 1, fmt)
        assert "发现" in hi["_currentStageContext"]

    def test_scene03_stage_context(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 2, fmt)
        assert "对峙" in hi["_currentStageContext"]

    def test_scene04_stage_context(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 3, fmt)
        assert "结局" in hi["_currentStageContext"]

    def test_scene01_beats(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert hi["_sceneBeats"], "scene_01 应有节拍"
        # scene_01 有 beat_01, beat_02
        assert "神秘委托" in hi["_sceneBeats"] or "第一次接触" in hi["_sceneBeats"]

    def test_scene02_beats(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 1, fmt)
        assert hi["_sceneBeats"]
        assert "真相时刻" in hi["_sceneBeats"] or "灾难重现" in hi["_sceneBeats"]

    def test_scene03_beats(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 2, fmt)
        assert hi["_sceneBeats"]
        assert "正面对峙" in hi["_sceneBeats"] or "最终选择" in hi["_sceneBeats"]

    def test_scene04_beats(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 3, fmt)
        assert hi["_sceneBeats"]
        assert "回响" in hi["_sceneBeats"]

    def test_scene01_hooks(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert hi["_sceneHooks"]

    def test_scene01_details_atmosphere(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert "压抑" in hi["_sceneDetails"] or "不安" in hi["_sceneDetails"]

    def test_scene01_details_narrative_notes(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        assert "决策点" in hi["_sceneDetails"] or "分支" in hi["_sceneDetails"] or "选择" in hi["_sceneDetails"]

    def test_scene01_details_interactions(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 0, fmt)
        # 应包含互动选项
        assert "解密" in hi["_sceneDetails"] or "调查" in hi["_sceneDetails"]

    def test_scene03_details_branching(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 2, fmt)
        assert "分支" in hi["_sceneDetails"] or "决策" in hi["_sceneDetails"]

    def test_scene04_no_critical_choice(self, story_data, fmt):
        hi = self._build_harness_input(story_data, 3, fmt)
        # scene_04 没有 critical_choice，_sceneDetails 不应包含 "关键抉择"
        assert "关键抉择" not in hi["_sceneDetails"]

    # -- 角色在场验证 --
    def test_scene01_characters(self, story_data):
        chars = get_scene_characters(story_data, story_data["scenes"][0])
        names = {c["name"] for c in chars}
        assert names == {"苏然", "林晓"}, f"scene_01 在场角色={names}"

    def test_scene03_characters(self, story_data):
        chars = get_scene_characters(story_data, story_data["scenes"][2])
        names = {c["name"] for c in chars}
        assert "杜维明" in names, f"scene_03 应包含杜维明: {names}"

    # -- 剧本选项提取验证 --
    def test_scene01_script_choices(self, story_data, fmt):
        scene = story_data["scenes"][0]
        choices = fmt._extract_script_choices(scene, story_data.get("key_choices", []))
        texts = [c["text"] for c in choices]
        assert len(texts) == 3, f"scene_01 应有3个选项: {texts}"
        assert any("解密" in t for t in texts), f"缺少'解密'选项: {texts}"
        assert any("调查" in t for t in texts), f"缺少'调查'选项: {texts}"

    def test_scene02_script_choices(self, story_data, fmt):
        scene = story_data["scenes"][1]
        choices = fmt._extract_script_choices(scene, story_data.get("key_choices", []))
        texts = [c["text"] for c in choices]
        assert len(texts) == 2, f"scene_02 应有2个选项: {texts}"
        assert any("继续" in t for t in texts)
        assert any("停止" in t for t in texts)

    def test_scene03_script_choices(self, story_data, fmt):
        scene = story_data["scenes"][2]
        choices = fmt._extract_script_choices(scene, story_data.get("key_choices", []))
        texts = [c["text"] for c in choices]
        assert len(texts) == 3, f"scene_03 应有3个选项: {texts}"
        assert any("真相" in t or "公开" in t for t in texts)
        assert any("协议" in t or "达成" in t for t in texts)
        assert any("伏笔" in t or "暗中" in t for t in texts)

    def test_scene04_no_script_choices(self, story_data, fmt):
        scene = story_data["scenes"][3]
        choices = fmt._extract_script_choices(scene, story_data.get("key_choices", []))
        assert len(choices) == 0, f"scene_04 不应有选项: {choices}"


# ═══════════════════════════════════════════════════════
# Part 3: 汇总验证报告（运行 pytest -s 时打印）
# ═══════════════════════════════════════════════════════

def test_echo_verification_report():
    """汇总报告：打印所有字段的 PASS/FAIL 状态"""
    story = load_story(SCRIPT_PATH)

    print("\n" + "=" * 60)
    print("  Echo-DEMO End-to-End Verification Report")
    print("=" * 60)

    # data loading
    print("\n[DATA LOADING]")
    checks = [
        ("title",            story.get("title") == "回响"),
        ("narrative_style",  bool(story.get("narrative_style"))),
        ("genre",            bool(story.get("genre"))),
        ("characters (3)",   len(story.get("characters", [])) == 3),
        ("scenes (4)",       len(story.get("scenes", [])) == 4),
        ("stages (4)",       len(story.get("stages", [])) == 4),
        ("world_lines (3)",  len(story.get("world_lines", [])) == 3),
        ("convergence_pts",  len(story.get("convergence_points", [])) == 2),
        ("worldline_rules",  len(story.get("worldline_rules", [])) == 3),
        ("key_choices (3)",  len(story.get("key_choices", [])) == 3),
        ("relationships (3)",len(story.get("character_relationships", [])) == 3),
        ("events (4)",       len(story.get("events", [])) == 4),
        ("endings (3)",      len(story.get("endings", [])) == 3),
        ("worldbook",        len(story.get("worldbook", "")) > 100),
        ("plot_summary",     len(story.get("plot_summary", "")) > 50),
        ("core_conflict",    bool(story.get("core_conflict"))),
        ("themes (3)",       len(story.get("themes", [])) == 3),
        ("beats (7)",        len(story.get("beats", [])) == 7),
        ("hooks (4)",        len(story.get("hooks", [])) == 4),
    ]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"  通过: {sum(1 for _, ok in checks if ok)}/{len(checks)}")

    # character fields
    print("\n[CHARACTER FIELDS]")
    fields = ["id","name","role","personality","speakingStyle",
              "background","motivation","secrets","relationships","appearance","arc"]
    for c in story.get("characters", []):
        missing = [f for f in fields if not c.get(f)]
        status = "PASS" if not missing else f"FAIL ({missing})"
        print(f"  {status}  {c.get('name','?')}")

    # injection
    fmt = _import_server_formatters()
    print("\n[DATA INJECTION]")
    scene = story["scenes"][0]
    scene_id = scene.get("id", "")
    current_stage = fmt._get_current_stage(story.get("stages", []), scene_id)
    inj_checks = [
        ("_worldbook",          bool(fmt._extract_worldbook_rules(story.get("worldbook", "")))),
        ("_narrativeStyle",     bool(story.get("narrative_style"))),
        ("_plotSummary",        bool(story.get("plot_summary"))),
        ("_coreConflict",       bool(story.get("core_conflict"))),
        ("_themes",             bool(fmt._format_themes(story.get("themes", [])))),
        ("_relationshipNetwork",bool(fmt._format_relationships(story.get("character_relationships", [])))),
        ("_stagesOverview",     bool(fmt._format_stages_overview(story.get("stages", [])))),
        ("_events",             bool(fmt._format_events(story.get("events", [])))),
        ("_keyChoices",         bool(fmt._format_key_choices(story.get("key_choices", [])))),
        ("_endings",            bool(fmt._format_endings(story.get("endings", [])))),
        ("_worldlines",         bool(fmt._format_worldlines(
            story.get("world_lines", []),
            story.get("convergence_points", []),
            story.get("worldline_rules", [])))),
        ("_sceneDetails",       bool(fmt._format_scene_details(scene, story.get("key_choices", [])))),
        ("_currentStageContext",bool(fmt._format_current_stage_context(current_stage))),
        ("_sceneBeats",         bool(fmt._format_beats_for_scene(story.get("beats", []), scene_id))),
        ("_sceneHooks",         bool(fmt._format_hooks_for_scene(story.get("hooks", []), scene_id))),
    ]
    for name, ok in inj_checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"  通过: {sum(1 for _, ok in inj_checks if ok)}/{len(inj_checks)}")

    # scene-specific
    print("\n[SCENE DATA]")
    stage_names = ["开端", "发现", "对峙", "结局"]
    expected_beats = [2, 2, 2, 1]
    for i, (sname, exp_beats) in enumerate(zip(stage_names, expected_beats)):
        sc = story["scenes"][i]
        sid = sc.get("id", "")
        stage = fmt._get_current_stage(story.get("stages", []), sid)
        stage_ctx = fmt._format_current_stage_context(stage)
        beats = fmt._format_beats_for_scene(story.get("beats", []), sid)
        hooks = fmt._format_hooks_for_scene(story.get("hooks", []), sid)
        sc_choices = fmt._extract_script_choices(sc, story.get("key_choices", []))
        print(f"  Scene {i+1} ({sc.get('name','?')}):")
        print(f"    阶段: {'PASS' if sname in stage_ctx else 'FAIL'} ({sname})")
        print(f"    节拍: {'PASS' if beats else 'FAIL'} (预期 {exp_beats} 个)")
        print(f"    钩子: {'PASS' if hooks else 'WARN(无)'}")
        print(f"    选项: {len(sc_choices)} 个 {[c['text'][:10] for c in sc_choices]}")

    print("\n" + "=" * 60)
    all_pass = all(ok for _, ok in checks) and all(ok for _, ok in inj_checks)
    print(f"  总体结果: {'PASS' if all_pass else 'FAIL'}")
    print("=" * 60)
    assert all_pass, "存在失败项，请查看上方报告"
