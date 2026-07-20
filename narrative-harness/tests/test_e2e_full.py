# -*- coding: utf-8 -*-
"""
Narrative Harness 全链路端到端测试 v4.0
三层验证：数据识别层 → 数据注入层 → 生成效果层
覆盖 5 个剧本 × 三项验收

用法：
  cd narrative-harness
  python tests/test_e2e_full.py
"""

from __future__ import annotations
import io, json, os, sys, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")

from generic_adapter import load_story, get_scene_characters, build_worldline, build_scene_text
from harness import NarrativeHarness, render_template
from schemas import HarnessInput, Character, AssembledContext, Dialogue, Choice
from validator import NarrativeValidator

# 导入 server 的格式化函数
from server import (
    _extract_worldbook_rules, _format_relationships,
    _format_stages_overview, _format_events,
    _format_key_choices, _format_endings,
)

SCRIPTS_DIR = ROOT.parent / "测试剧本"
TEST_SCRIPTS = {
    "回魂公寓": str(SCRIPTS_DIR / "回魂公寓"),
    "星河入梦": str(SCRIPTS_DIR / "星河入梦"),
    "都市传说": str(SCRIPTS_DIR / "都市传说"),
    "夜班": str(SCRIPTS_DIR / "夜班"),
    "霓虹深渊": str(SCRIPTS_DIR / "霓虹深渊"),
    "回响DEMO": str(SCRIPTS_DIR / "回响-DEMO(1)" / "回响-DEMO"),
}

HAS_API_KEY = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))

# ── 结果收集 ──
RESULTS = []  # [(layer, script_name, check_name, pass/fail, detail)]


def record(layer: str, script: str, check: str, passed: bool, detail: str = ""):
    RESULTS.append((layer, script, check, passed, detail))


def build_harness_input(story: dict, scene_index: int = 0) -> dict:
    """从剧本构建完整的 harness_input（含 _ 前缀上下文变量）"""
    scenes = story.get("scenes", [])
    if not scenes:
        raise ValueError("无场景数据")
    scene = scenes[min(scene_index, len(scenes) - 1)]
    characters = get_scene_characters(story, scene)
    worldline = build_worldline(story, scene_index)
    scene_text = build_scene_text(scene)
    return {
        "currentScene": scene_text,
        "characters": [
            {
                "name": c["name"], "role": c["role"],
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
        "_worldbook": _extract_worldbook_rules(story.get("worldbook", "")),
        "_narrativeStyle": story.get("narrative_style", "") or "第二人称叙事",
        "_plotSummary": story.get("plot_summary", ""),
        "_coreConflict": story.get("core_conflict", ""),
        "_relationshipNetwork": _format_relationships(story.get("character_relationships", [])),
        "_stagesOverview": _format_stages_overview(story.get("stages", [])),
        "_events": _format_events(story.get("events", [])),
        "_keyChoices": _format_key_choices(story.get("key_choices", [])),
        "_endings": _format_endings(story.get("endings", [])),
    }


# ═══════════════════════════════════════════════════════
# 第一层：数据识别层
# ═══════════════════════════════════════════════════════

def test_data_recognition():
    print("\n" + "=" * 60)
    print("  第一层：数据识别层 — load_story 全字段验证")
    print("=" * 60)

    # 完整结构剧本（11文件）vs 简单结构（4文件）
    FULL_SCRIPTS = {"霓虹深渊", "回响DEMO"}
    SIMPLE_SCRIPTS = {"回魂公寓", "星河入梦", "都市传说", "夜班"}

    for name, path in TEST_SCRIPTS.items():
        is_full = name in FULL_SCRIPTS
        print(f"\n--- {name} ({'完整' if is_full else '简单'}结构) ---")
        try:
            story = load_story(path)
        except Exception as e:
            record("数据识别", name, "load_story", False, str(e)[:80])
            print(f"  [FAIL] load_story 失败: {e}")
            continue
        record("数据识别", name, "load_story", True)

        # 基本字段（所有剧本都有）
        checks = [
            ("title", story.get("title", ""), bool(story.get("title"))),
            ("characters", story.get("characters", []), len(story.get("characters", [])) >= 1),
            ("scenes", story.get("scenes", []), len(story.get("scenes", [])) >= 1),
            ("stages", story.get("stages", []), len(story.get("stages", [])) >= 1),
        ]
        for field, val, cond in checks:
            cnt = len(val) if isinstance(val, list) else "有值" if val else "空"
            record("数据识别", name, field, cond, f"{cnt}")
            print(f"  [{'PASS' if cond else 'FAIL'}] {field}: {cnt}")

        # 世界线（完整结构有，简单结构可能无）
        wl = story.get("world_lines", [])
        wl_ok = len(wl) >= 1
        record("数据识别", name, "world_lines", wl_ok if is_full else True,
               f"{len(wl)}" if wl_ok else "无(简单结构)")
        print(f"  [{'PASS' if (wl_ok or not is_full) else 'FAIL'}] world_lines: {len(wl)}")

        # 角色 10 字段
        required = ["name", "role", "personality", "speakingStyle", "background"]
        optional = ["motivation", "secrets", "relationships", "appearance", "arc"]
        chars = story.get("characters", [])
        for c in chars:
            for f in required:
                ok = bool(c.get(f))
                if not ok:
                    record("数据识别", name, f"角色必填.{c.get('name','?')}.{f}", False)
                    print(f"  [FAIL] {c.get('name','?')} 缺 {f}")

        # 统计可选字段覆盖
        opt_stats = {}
        for f in optional:
            cnt = sum(1 for c in chars if c.get(f))
            opt_stats[f] = cnt
        opt_str = ", ".join(f"{k}={v}/{len(chars)}" for k, v in opt_stats.items())
        if is_full:
            all_opt_pass = all(v > 0 for v in opt_stats.values())
            record("数据识别", name, "角色可选字段覆盖", all_opt_pass, opt_str)
            print(f"  [{'PASS' if all_opt_pass else 'FAIL'}] 角色可选字段: {opt_str}")
        else:
            # 简单结构剧本没有可选字段，只记录不标记失败
            record("数据识别", name, "角色可选字段覆盖", True, f"简单结构无:{opt_str}")
            print(f"  [PASS] 角色可选字段: {opt_str} (简单结构无扩展)")

        # 扩展数据（完整结构才有）
        ext_checks = [
            ("narrative_style", story.get("narrative_style", "")),
            ("genre", story.get("genre", "")),
            ("worldbook", story.get("worldbook", "")),
            ("plot_summary", story.get("plot_summary", "")),
            ("core_conflict", story.get("core_conflict", "")),
            ("character_relationships", story.get("character_relationships", [])),
            ("events", story.get("events", [])),
            ("endings", story.get("endings", [])),
        ]
        for field, val in ext_checks:
            cnt = len(val) if isinstance(val, list) else (len(val) if val else 0)
            has_data = cnt > 0
            if is_full:
                record("数据识别", name, field, has_data, str(cnt) if isinstance(cnt, int) else str(val)[:30])
                print(f"  [{'PASS' if has_data else 'FAIL'}] {field}: {cnt}")
            else:
                # 简单结构：有数据是加分项，没有不是错误
                if has_data:
                    record("数据识别", name, field, True, str(cnt))
                    print(f"  [PASS] {field}: {cnt} (简单结构有数据)")
                else:
                    print(f"  [SKIP] {field}: 无 (简单结构无此文件)")


# ═══════════════════════════════════════════════════════
# 第二层：数据注入层
# ═══════════════════════════════════════════════════════

def test_data_injection():
    print("\n" + "=" * 60)
    print("  第二层：数据注入层 — prompt 模板变量验证")
    print("=" * 60)

    # 读取 prompt 模板
    prompts_dir = ROOT / "prompts"
    narration_tpl = (prompts_dir / "narration_v2.md").read_text(encoding="utf-8")
    dialogue_tpl = (prompts_dir / "dialogue_v2.md").read_text(encoding="utf-8")
    choices_tpl = (prompts_dir / "choices_v2.md").read_text(encoding="utf-8")
    sys_prompt_tpl = (prompts_dir / "system_prompt_dynamic.md").read_text(encoding="utf-8")

    for name, path in TEST_SCRIPTS.items():
        print(f"\n--- {name} ---")
        story = load_story(path)
        try:
            inp = build_harness_input(story)
        except Exception as e:
            record("数据注入", name, "build_input", False, str(e)[:80])
            print(f"  [FAIL] build_input: {e}")
            continue

        extra = {k: v for k, v in inp.items() if k.startswith("_")}

        # 2.1 角色卡 10 字段在 dialogue 模板中
        chars = inp["characters"]
        dialogue_vars = {
            "narration": "测试旁白",
            "characters": chars,
            "annotatedWorldline": "● 节点1 → 【节点2】 → ○ 节点3",
            "currentNode": "节点2", "nextNode": "节点3",
            "worldlineProgress": 50,
            "playerDecision": "", "dialogueCount": 4,
            "minDialogues": 3, "maxDialogues": 5,
            "previousDialogues": [],
            "events": extra.get("_events", ""),
            "stagesOverview": extra.get("_stagesOverview", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
        }
        rendered_dlg = render_template(dialogue_tpl, dialogue_vars)

        # 验证每个角色字段出现在渲染结果中（只在有数据时检查）
        field_labels = {
            "personality": "性格", "speakingStyle": "说话风格",
            "background": "背景", "motivation": "动机",
            "relationships": "关系", "appearance": "外貌", "arc": "成长弧线",
        }
        char_fields_ok = 0
        char_fields_total = 0
        for f, label in field_labels.items():
            # 检查是否有角色拥有该字段数据
            has_data = any(c.get(f) for c in chars)
            if has_data:
                char_fields_total += 1
                if label in rendered_dlg:
                    char_fields_ok += 1
            else:
                # 无数据时标签不应出现（被空值清理正则删除）
                if label not in rendered_dlg:
                    pass  # 正确行为

        record("数据注入", name, "dialogue_角色字段",
               char_fields_ok >= max(1, char_fields_total - 1),
               f"{char_fields_ok}/{char_fields_total}")
        print(f"  [{'PASS' if char_fields_ok >= max(1, char_fields_total - 1) else 'FAIL'}] dialogue 角色字段: {char_fields_ok}/{char_fields_total} (有数据的字段)")

        # 2.2 narration 模板
        narr_vars = {
            "worldline": inp["worldline"],
            "annotatedWorldline": "● 节点1 → 【节点2】 → ○ 节点3",
            "currentNode": "节点2", "nodeIndex": 2, "totalNodes": 4,
            "previousNode": "节点1", "nextNode": "节点3",
            "worldlineProgress": 50,
            "sceneDescription": "测试场景描述",
            "characters": chars,
            "playerDecision": "",
            "previousNarration": "",
            "stagesOverview": extra.get("_stagesOverview", ""),
            "events": extra.get("_events", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "plotSummary": extra.get("_plotSummary", ""),
        }
        rendered_narr = render_template(narration_tpl, narr_vars)
        has_stages = "剧情阶段总览" in rendered_narr or not extra.get("_stagesOverview")
        has_events = "关键事件" in rendered_narr or not extra.get("_events")
        record("数据注入", name, "narration_阶段+事件", has_stages and has_events)
        print(f"  [{'PASS' if has_stages and has_events else 'FAIL'}] narration 阶段/事件注入")

        # 2.3 choices 模板
        choice_vars = {
            "narration": "测试旁白",
            "dialogues": [{"speaker": "A", "text": "测试"}],
            "worldline": inp["worldline"],
            "currentNode": "节点2", "nextNode": "节点3",
            "worldlineProgress": 50,
            "keyChoices": extra.get("_keyChoices", ""),
            "endings": extra.get("_endings", ""),
            "coreConflict": extra.get("_coreConflict", ""),
        }
        rendered_ch = render_template(choices_tpl, choice_vars)
        has_kc = "关键选择" in rendered_ch or not extra.get("_keyChoices")
        has_end = "结局系统" in rendered_ch or not extra.get("_endings")
        record("数据注入", name, "choices_选择+结局", has_kc and has_end)
        print(f"  [{'PASS' if has_kc and has_end else 'FAIL'}] choices 关键选择/结局注入")

        # 2.4 system_prompt_dynamic
        sys_vars = {
            "narrativeStyle": extra.get("_narrativeStyle", ""),
            "worldbookRules": extra.get("_worldbook", ""),
            "plotSummary": extra.get("_plotSummary", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
            "sceneAtmosphere": "", "narrativeNotes": "", "emotionalArc": "",
        }
        rendered_sys = render_template(sys_prompt_tpl, sys_vars)
        sys_checks = [
            ("narrativeStyle→叙事视角", "叙事视角" in rendered_sys),
            ("worldbookRules→世界书", "世界书" in rendered_sys or not extra.get("_worldbook")),
            ("plotSummary→故事概要", "故事概要" in rendered_sys or not extra.get("_plotSummary")),
            ("coreConflict→核心冲突", "核心冲突" in rendered_sys or not extra.get("_coreConflict")),
            ("relNet→角色关系", "角色关系" in rendered_sys or not extra.get("_relationshipNetwork")),
        ]
        all_sys_ok = all(ok for _, ok in sys_checks)
        record("数据注入", name, "system_prompt_全变量", all_sys_ok,
               ", ".join(f"{'✓' if ok else '✗'}{l}" for l, ok in sys_checks))
        print(f"  [{'PASS' if all_sys_ok else 'FAIL'}] system_prompt: {', '.join(l + ('✓' if ok else '✗') for l, ok in sys_checks)}")

        # 2.5 空值省略测试
        empty_vars = dict(dialogue_vars)
        empty_vars["events"] = ""
        empty_vars["stagesOverview"] = ""
        empty_vars["coreConflict"] = ""
        empty_vars["relationshipNetwork"] = ""
        rendered_empty = render_template(dialogue_tpl, empty_vars)
        no_empty_sections = ("关键事件" not in rendered_empty and
                             "剧情阶段总览" not in rendered_empty and
                             "核心冲突" not in rendered_empty)
        record("数据注入", name, "空值段落省略", no_empty_sections)
        print(f"  [{'PASS' if no_empty_sections else 'FAIL'}] 空值时不出现空段落")


# ═══════════════════════════════════════════════════════
# 第三层：生成效果层（需要 LLM API Key）
# ═══════════════════════════════════════════════════════

def test_generation_quality():
    print("\n" + "=" * 60)
    print("  第三层：生成效果层 — Pipeline 端到端 (LLM)")
    print("=" * 60)

    if not HAS_API_KEY:
        print("  [SKIP] 未配置 API Key，跳过生成测试")
        return

    harness = NarrativeHarness()

    for name, path in TEST_SCRIPTS.items():
        print(f"\n--- {name} ---")
        story = load_story(path)
        try:
            inp = build_harness_input(story)
        except Exception as e:
            record("生成效果", name, "build_input", False, str(e)[:80])
            print(f"  [FAIL] build: {e}")
            continue

        # 运行 Pipeline
        t0 = time.time()
        try:
            result = harness.run(inp)
        except Exception as e:
            record("生成效果", name, "Pipeline运行", False, str(e)[:80])
            print(f"  [FAIL] Pipeline: {e}")
            continue
        elapsed = time.time() - t0

        if not result.get("success"):
            record("生成效果", name, "Pipeline运行", False, "验证未通过")
            print(f"  [FAIL] Pipeline 未通过")
            continue
        record("生成效果", name, "Pipeline运行", True, f"{elapsed:.1f}s")

        data = result.get("data", {})
        dialogues = data.get("dialogues", [])
        choices = data.get("nextChoices", [])
        narration = data.get("narration", "")
        validation = result.get("validation", {})

        # ── 标准1: 对话稳定性（≥4轮） ──
        n_dlg = len(dialogues)
        ok1 = n_dlg >= 4
        record("生成效果", name, f"标准1_对话≥4轮", ok1, f"{n_dlg}轮 {elapsed:.1f}s")
        print(f"  [{'PASS' if ok1 else 'FAIL'}] 标准1 对话轮数: {n_dlg}轮 ({elapsed:.1f}s)")

        # ── 标准2: 角色一致性 ──
        valid_names = {c["name"] for c in inp["characters"]}
        speakers = [d.get("speaker", "") for d in dialogues]
        all_valid = all(s in valid_names for s in speakers)
        pc = next((c for c in validation.get("checks", []) if c["name"] == "personality_match"), None)
        p_score = pc.get("score", 0) if pc else 0
        ok2 = all_valid and p_score >= 0.6
        record("生成效果", name, "标准2_角色一致性", ok2,
               f"speaker全在卡中={all_valid}, personality={p_score:.2f}")
        print(f"  [{'PASS' if ok2 else 'FAIL'}] 标准2 speaker={set(speakers)}, personality={p_score:.2f}")

        # ── 标准3: 主线符合度 ──
        wc = next((c for c in validation.get("checks", []) if c["name"] == "worldline_adherence"), None)
        w_score = wc.get("score", 0) if wc else 0
        ws = data.get("worldlineState", {})
        ok3 = w_score > 0 and "currentNode" in ws
        record("生成效果", name, "标准3_主线符合度", ok3,
               f"adherence={w_score:.2f}, node={ws.get('currentNode','?')}, progress={ws.get('progress',0)}")
        print(f"  [{'PASS' if ok3 else 'FAIL'}] 标准3 adherence={w_score:.2f}, node={ws.get('currentNode','?')}")

        # ── 标准4: 选择续写能力 ──
        if n_dlg >= 1 and len(choices) >= 2:
            # 选择第一个选项续写
            choice_text = choices[0].get("text", "继续探索")
            r2_inp = {**inp, "playerChoice": choice_text, "previousContext": data}
            t1 = time.time()
            try:
                r2 = harness.run(r2_inp)
            except Exception as e:
                record("生成效果", name, "标准4_续写能力", False, f"续写失败: {e}")
                print(f"  [FAIL] 标准4 续写: {e}")
                continue
            elapsed2 = time.time() - t1

            if r2.get("success"):
                d2 = r2["data"].get("dialogues", [])
                c2 = r2["data"].get("nextChoices", [])
                text1 = " ".join(d.get("text", "") for d in dialogues)
                text2 = " ".join(d.get("text", "") for d in d2)
                not_repeat = text1 != text2
                ok4 = len(d2) >= 1 and len(c2) >= 2 and not_repeat
                record("生成效果", name, "标准4_续写能力", ok4,
                       f"续写{len(d2)}轮+{len(c2)}选项, 不重复={not_repeat}, {elapsed2:.1f}s")
                print(f"  [{'PASS' if ok4 else 'FAIL'}] 标准4 续写{len(d2)}轮+{len(c2)}选项 ({elapsed2:.1f}s)")
            else:
                record("生成效果", name, "标准4_续写能力", False, "续写Pipeline失败")
                print(f"  [FAIL] 标准4 续写Pipeline失败")
        else:
            ok4 = False
            record("生成效果", name, "标准4_续写能力", False, f"初始对话{len(dialogues)}轮/选项{len(choices)}个不足")
            print(f"  [FAIL] 标准4 初始数据不足: {len(dialogues)}轮/{len(choices)}选项")


# ═══════════════════════════════════════════════════════
# 汇总报告
# ═══════════════════════════════════════════════════════

def print_summary():
    print("\n\n" + "=" * 60)
    print("  全链路测试结果汇总")
    print("=" * 60)

    layers = ["数据识别", "数据注入", "生成效果"]
    total_pass = sum(1 for _, _, _, ok, _ in RESULTS if ok)
    total_fail = sum(1 for _, _, _, ok, _ in RESULTS if not ok)

    for layer in layers:
        layer_results = [(s, c, ok, d) for l, s, c, ok, d in RESULTS if l == layer]
        lp = sum(1 for _, _, ok, _ in layer_results if ok)
        lf = sum(1 for _, _, ok, _ in layer_results if not ok)
        print(f"\n  [{layer}] {lp} PASS / {lf} FAIL (共 {lp + lf})")
        # 列出失败项
        for s, c, ok, d in layer_results:
            if not ok:
                print(f"    [FAIL] {s} - {c}: {d}")

    print(f"\n  总计: {total_pass} PASS / {total_fail} FAIL")

    # 按剧本汇总
    print("\n  按剧本汇总:")
    for script_name in TEST_SCRIPTS:
        script_results = [(c, ok, d) for _, s, c, ok, d in RESULTS if s == script_name]
        sp = sum(1 for _, ok, _ in script_results if ok)
        sf = sum(1 for _, ok, _ in script_results if not ok)
        status = "PASS" if sf == 0 else "FAIL"
        print(f"    {script_name}: {sp}/{sp + sf} [{status}]")

    return total_fail == 0


# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  Narrative Harness 全链路端到端测试 v4.0")
    print(f"  测试剧本: {', '.join(TEST_SCRIPTS.keys())}")
    print(f"  API Key: {'已配置' if HAS_API_KEY else '未配置（跳过生成测试）'}")
    print("=" * 60)

    t_start = time.time()

    # 第一层
    test_data_recognition()

    # 第二层
    test_data_injection()

    # 第三层
    test_generation_quality()

    total_time = time.time() - t_start

    # 汇总
    all_pass = print_summary()
    print(f"\n  总耗时: {total_time:.1f}s")
    print(f"  结论: {'全部通过' if all_pass else '存在失败项'}")

    # 输出 JSON 结果（供报告生成）
    report_path = ROOT / "tests" / "_e2e_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_pass": sum(1 for _, _, _, ok, _ in RESULTS if ok),
            "total_fail": sum(1 for _, _, _, ok, _ in RESULTS if not ok),
            "total_time": round(total_time, 1),
            "results": [
                {"layer": l, "script": s, "check": c, "pass": ok, "detail": d}
                for l, s, c, ok, d in RESULTS
            ]
        }, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {report_path}")
