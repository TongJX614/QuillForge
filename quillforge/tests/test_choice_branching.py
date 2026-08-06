# -*- coding: utf-8 -*-
"""专项测试：验证"玩家选择不同选项后，后续剧情是否真的发生相应变化"

测试目标：
  1. 确认 player_choice 是否正确传递到下一场景生成流程（LLM prompt）
  2. 确认不同选项是否导致下一场景内容出现可观察差异
  3. 确认场景推进是否只是固定 scene_index + 1 线性前进
  4. 确认预生成缓存是否导致不同选项返回同一缓存场景
  5. 确认 trigger_conditions 在 Harness 管线中是否生效
  6. 确认 player_choice 在 Harness 五阶段流水线中的传递路径
"""

import copy
import json
import threading
import time
from unittest.mock import patch, MagicMock, call

import pytest
from realtime_engine import RealtimeStorySession, MAX_SCENES
from session_manager import GameSession, SessionState, PlayerState


# ═══════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════

def _make_realtime_session_with_scene() -> RealtimeStorySession:
    """创建一个带有初始场景的实时会话（跳过 LLM 调用）。"""
    session = RealtimeStorySession("测试故事")
    session.seed = {
        "title": "测试剧本",
        "genre": "悬疑",
        "tone": "紧张",
        "narrative_style": "第二人称",
        "characters": [
            {"id": "char_01", "name": "林晓", "role": "主角",
             "gender": "female", "age": 25, "personality": "冷静果断",
             "voice_tone": "简洁利落", "motivation": "寻找真相",
             "background": "记者", "arc": "从迷茫到坚定"},
            {"id": "char_02", "name": "苏然", "role": "配角",
             "gender": "male", "age": 30, "personality": "温和内敛",
             "voice_tone": "慢条斯理", "motivation": "保护林晓",
             "background": "程序员", "arc": "从逃避到面对"},
        ],
        "world_setting": "现代都市",
        "world_rules": ["不能出现超自然现象"],
        "core_conflict": "真相与安全的抉择",
        "plot_summary": "一名记者追踪线索的故事",
        "themes": ["真相", "勇气"],
        "estimated_scenes": 5,
        "initial_scene_hint": "深夜的办公室",
    }
    # 初始场景带有 3 个选项
    session.scenes = [{
        "scene_id": "scene_01",
        "scene_name": "深夜办公室",
        "scene_index": 0,
        "location": "办公室",
        "time": "深夜",
        "narration": "你独自坐在办公室里，屏幕上的线索令人不安。",
        "dialogues": [
            {"speaker_id": "char_01", "speaker": "林晓", "text": "这些线索不对劲。", "emotion": "分析"},
            {"speaker_id": "char_02", "speaker": "苏然", "text": "你确定要继续追查？", "emotion": "担忧"},
        ],
        "choices": [
            {"id": "choice_1", "text": "立刻去调查发件人地址", "hint": "冒险行动"},
            {"id": "choice_2", "text": "先备份数据再做打算", "hint": "谨慎准备"},
            {"id": "choice_3", "text": "联系警方寻求帮助", "hint": "求助外部"},
        ],
        "characters_present": ["char_01", "char_02"],
        "is_ending": False,
        "atmosphere": "紧张",
        "image_description": "深夜办公室",
    }]
    session.current_scene_index = 0
    return session


def _make_mock_adapter_with_triggers():
    """构建带有 trigger_conditions 的 mock adapter（Harness 管线）。"""
    adapter = MagicMock()
    adapter.scenes = [
        {"id": "scene_01", "name": "开场", "location": "城市",
         "trigger_conditions": {"type": "game_start"}},
        {"id": "scene_02", "name": "普通发展", "location": "街道",
         "trigger_conditions": {}},
        {"id": "scene_03", "name": "调查分支", "location": "图书馆",
         "trigger_conditions": {"type": "choice_made", "value": "在scene_01选择调查线索"}},
        {"id": "scene_04", "name": "逃跑分支", "location": "车站",
         "trigger_conditions": {"type": "choice_made", "value": "在scene_01选择逃跑"}},
        {"id": "scene_05", "name": "结局", "location": "终点"},
    ]
    adapter.worldline_nodes = ["开端", "发展", "高潮", "结局"]
    adapter.raw_data = {"endings": []}
    adapter.build_harness_input.return_value = {
        "currentScene": "测试场景",
        "characters": [{"name": "角色A", "role": "主角", "personality": "冷静"}],
        "worldline": "开端 → 发展 → 高潮 → 结局",
        "playerChoice": "",
    }
    adapter.get_scene_choices.return_value = [
        {"id": "c1", "text": "调查线索", "description": "", "consequences": {}},
        {"id": "c2", "text": "逃跑", "description": "", "consequences": {}},
    ]
    return adapter


def _make_mock_harness():
    """构建 mock Harness"""
    harness = MagicMock()
    harness.run.return_value = {
        "success": True,
        "data": {
            "narration": "测试旁白",
            "dialogues": [
                {"speaker": "角色A", "text": "你好", "emotion": "平静"},
                {"speaker": "角色A", "text": "我们出发吧", "emotion": "坚定"},
                {"speaker": "角色A", "text": "好的", "emotion": "平静"},
            ],
            "nextChoices": [
                {"text": "选项1", "effect": "推进", "worldlineImpact": "advance"},
            ],
            "worldlineState": {"currentNode": "开端", "nodeIndex": 0, "totalNodes": 4, "progress": 0.0},
        },
        "validation": {"passed": True, "checks": [], "retryCount": 0},
        "metadata": {"generationTime": 500, "model": "test"},
    }
    return harness


# ═══════════════════════════════════════════════════════
# 测试 1: player_choice 是否正确传入 LLM prompt（实时引擎）
# ═══════════════════════════════════════════════════════

class TestPlayerChoicePassedToPrompt:
    """验证实时引擎中 player_choice 是否被正确传入 LLM 生成 prompt"""

    def test_build_scene_prompt_includes_player_choice(self):
        """_build_scene_prompt 应在 prompt 中包含 player_choice 文本"""
        session = _make_realtime_session_with_scene()
        session.history = [{"scene_index": 0, "choice_index": 0, "choice_text": "立刻去调查发件人地址"}]

        prompt = session._build_scene_prompt(
            scene_idx=1,
            player_choice="立刻去调查发件人地址",
        )

        # 验证 player_choice 出现在 prompt 中
        assert "立刻去调查发件人地址" in prompt
        assert "玩家选择" in prompt

    def test_different_choices_produce_different_prompts(self):
        """不同选项应生成不同的 prompt 内容"""
        session = _make_realtime_session_with_scene()
        session.history = [{"scene_index": 0, "choice_index": 0, "choice_text": "立刻去调查发件人地址"}]

        prompt_a = session._build_scene_prompt(1, player_choice="立刻去调查发件人地址")
        prompt_b = session._build_scene_prompt(1, player_choice="先备份数据再做打算")
        prompt_c = session._build_scene_prompt(1, player_choice="联系警方寻求帮助")

        # 三个 prompt 应该互不相同
        assert prompt_a != prompt_b
        assert prompt_b != prompt_c
        assert prompt_a != prompt_c

        # 各自包含对应的选择文本
        assert "立刻去调查发件人地址" in prompt_a
        assert "先备份数据再做打算" in prompt_b
        assert "联系警方寻求帮助" in prompt_c

    def test_choose_passes_choice_text_to_generate_stream(self):
        """choose() 应将选项文本传递给 _generate_scene_stream"""
        session = _make_realtime_session_with_scene()

        captured_calls = []

        def mock_generate_stream(scene_idx, player_choice=""):
            captured_calls.append({"scene_idx": scene_idx, "player_choice": player_choice})
            # 返回一个最小化的场景事件序列
            yield {"type": "scene_end", "scene_name": "下一场景",
                   "narration": "旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                   "is_ending": False, "scene_id": "scene_02",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_generate_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    # 选择第 2 个选项 (index=1): "先备份数据再做打算"
                    events = list(session.choose(1))

        # 验证 _generate_scene_stream 被调用时传入了正确的 choice_text
        assert len(captured_calls) == 1
        assert captured_calls[0]["player_choice"] == "先备份数据再做打算"
        assert captured_calls[0]["scene_idx"] == 1  # 线性递增

    def test_choose_different_options_pass_different_text(self):
        """分别选择 3 个不同选项，验证各自传入不同的 player_choice"""
        results = []

        for choice_idx in range(3):
            session = _make_realtime_session_with_scene()
            captured = {}

            def mock_gen(scene_idx, player_choice="", _cap=captured):
                _cap["player_choice"] = player_choice
                _cap["scene_idx"] = scene_idx
                yield {"type": "scene_end", "scene_name": f"场景_{choice_idx}",
                       "narration": f"旁白_{choice_idx}", "dialogues": [],
                       "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                       "is_ending": False, "scene_id": "scene_02",
                       "scene_index": scene_idx, "characters_present": []}

            with patch.object(session, '_generate_scene_stream', side_effect=mock_gen):
                with patch.object(session, '_start_pre_generation'):
                    with patch.object(session, 'save_to_yaml'):
                        list(session.choose(choice_idx))

            results.append(captured)

        # 验证三次调用传入了不同的 player_choice
        expected_choices = [
            "立刻去调查发件人地址",
            "先备份数据再做打算",
            "联系警方寻求帮助",
        ]
        for i, (result, expected) in enumerate(zip(results, expected_choices)):
            assert result["player_choice"] == expected, \
                f"选项 {i} 应传入 '{expected}'，实际传入 '{result['player_choice']}'"

        session.close()


# ═══════════════════════════════════════════════════════
# 测试 2: 不同选项是否导致可观察差异
# ═══════════════════════════════════════════════════════

class TestDifferentChoicesObservableDifference:
    """验证不同选项是否导致下一场景内容出现可观察差异"""

    def test_pre_generation_uses_different_choice_text_per_option(self):
        """预生成应为每个选项使用不同的 choice_text 调用 _generate_scene"""
        session = _make_realtime_session_with_scene()
        captured_calls = []

        def mock_generate_scene(idx, player_choice="", scenes_snapshot=None, history_snapshot=None):
            captured_calls.append({
                "scene_idx": idx,
                "player_choice": player_choice,
            })
            return {
                "scene_name": f"场景_{player_choice[:4]}",
                "narration": f"因为选择了'{player_choice}'，故事走向不同。",
                "dialogues": [],
                "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                "is_ending": False,
                "scene_id": f"scene_{idx + 1:02d}",
                "scene_index": idx,
            }

        with patch.object(session, '_generate_scene', side_effect=mock_generate_scene):
            session._start_pre_generation(0)

        # 等待预生成完成
        session._pre_gen_executor.shutdown(wait=True)

        # 应该为 3 个选项各调用一次
        assert len(captured_calls) == 3, \
            f"应为 3 个选项各预生成一次，实际调用 {len(captured_calls)} 次"

        # 每次调用应传入不同的 player_choice
        choice_texts = [c["player_choice"] for c in captured_calls]
        assert "立刻去调查发件人地址" in choice_texts
        assert "先备份数据再做打算" in choice_texts
        assert "联系警方寻求帮助" in choice_texts

        # 所有调用的 scene_idx 都是 1（线性递增）
        for c in captured_calls:
            assert c["scene_idx"] == 1, "预生成的 scene_idx 应为 1（线性递增）"

        session.close()

    def test_pre_gen_cache_returns_different_scenes_for_different_choices(self):
        """预生成缓存应为不同选项存储不同的场景结果"""
        session = _make_realtime_session_with_scene()

        # 模拟预生成完成后的缓存状态
        scene_for_choice_0 = {
            "scene_name": "调查现场",
            "narration": "你来到了发件人的地址，一栋废弃的公寓楼。",
            "dialogues": [{"speaker": "林晓", "text": "这里没人住。"}],
            "choices": [], "is_ending": False,
        }
        scene_for_choice_1 = {
            "scene_name": "安全屋",
            "narration": "你将所有数据复制到了加密U盘中。",
            "dialogues": [{"speaker": "苏然", "text": "备份完成了。"}],
            "choices": [], "is_ending": False,
        }
        scene_for_choice_2 = {
            "scene_name": "警察局",
            "narration": "你拨通了警方的电话，等待接线员回应。",
            "dialogues": [{"speaker": "林晓", "text": "我要报案。"}],
            "choices": [], "is_ending": False,
        }

        with session._pre_gen_lock:
            session._pre_generated["0:0"] = scene_for_choice_0
            session._pre_generated["0:1"] = scene_for_choice_1
            session._pre_generated["0:2"] = scene_for_choice_2

        # 模拟选择不同选项时从缓存取出
        for choice_idx, expected_name in [(0, "调查现场"), (1, "安全屋"), (2, "警察局")]:
            with session._pre_gen_lock:
                key = f"0:{choice_idx}"
                cached = session._pre_generated.get(key)
            assert cached is not None, f"选项 {choice_idx} 应有缓存"
            assert cached["scene_name"] == expected_name, \
                f"选项 {choice_idx} 缓存场景名应为 '{expected_name}'"

        # 验证三个缓存场景互不相同
        assert scene_for_choice_0["narration"] != scene_for_choice_1["narration"]
        assert scene_for_choice_1["narration"] != scene_for_choice_2["narration"]

        session.close()

    def test_choose_with_cache_hit_returns_correct_branch(self):
        """choose() 缓存命中时应返回对应选项的预生成场景"""
        session = _make_realtime_session_with_scene()

        # 注入缓存：选项 1 的预生成结果
        pre_gen_scene = {
            "scene_name": "安全屋",
            "narration": "你决定先备份数据。",
            "dialogues": [],
            "choices": [{"id": "c1", "text": "继续", "hint": ""}],
            "is_ending": False,
            "scene_id": "scene_02",
            "scene_index": 1,
        }
        with session._pre_gen_lock:
            session._pre_generated["0:1"] = pre_gen_scene

        with patch.object(session, '_start_pre_generation'):
            with patch.object(session, 'save_to_yaml'):
                events = list(session.choose(1))  # 选择第 2 个选项

        # 应该从缓存获取，不调用 _generate_scene_stream
        # 验证返回的场景事件包含预生成的内容
        scene_events = [e for e in events if e.get("type") == "scene"]
        assert len(scene_events) == 1, "缓存命中应直接发送 scene 事件"
        assert scene_events[0]["scene_name"] == "安全屋"
        assert scene_events[0]["narration"] == "你决定先备份数据。"

        session.close()

    def test_choose_without_cache_generates_with_choice(self):
        """缓存未命中时，choose() 应使用选项文本即时生成"""
        session = _make_realtime_session_with_scene()
        # 确保缓存为空
        session._pre_generated.clear()

        captured_choice = {}

        def mock_gen_stream(scene_idx, player_choice=""):
            captured_choice["text"] = player_choice
            yield {"type": "scene_end", "scene_name": "即时生成",
                   "narration": f"因为'{player_choice}'，剧情发展了。",
                   "dialogues": [], "scene_id": "scene_02",
                   "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                   "is_ending": False, "scene_index": scene_idx,
                   "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    events = list(session.choose(2))  # 选择第 3 个选项

        assert captured_choice["text"] == "联系警方寻求帮助"

        session.close()


# ═══════════════════════════════════════════════════════
# 测试 3: 场景推进是否只是固定线性递增
# ═══════════════════════════════════════════════════════

class TestSceneProgressionLinearity:
    """验证实时引擎的场景推进是否只是固定 scene_index + 1"""

    def test_realtime_engine_always_linear_increment(self):
        """实时引擎 choose() 始终使用 current_scene_index + 1，无分支跳转"""
        session = _make_realtime_session_with_scene()

        scene_indices = []

        def mock_gen_stream(scene_idx, player_choice=""):
            scene_indices.append(scene_idx)
            yield {"type": "scene_end", "scene_name": f"场景{scene_idx}",
                   "narration": "旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""},
                               {"id": "c2", "text": "另一条路", "hint": ""}],
                   "is_ending": False, "scene_id": f"scene_{scene_idx+1:02d}",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    # 第一次选择
                    list(session.choose(0))
                    assert session.current_scene_index == 1

                    # 第二次选择
                    list(session.choose(1))
                    assert session.current_scene_index == 2

                    # 第三次选择
                    list(session.choose(0))
                    assert session.current_scene_index == 3

        # 验证场景索引始终线性递增: 1, 2, 3
        assert scene_indices == [1, 2, 3], \
            f"实时引擎场景推进应为线性递增 [1,2,3]，实际为 {scene_indices}"

        session.close()

    def test_realtime_engine_no_trigger_conditions_mechanism(self):
        """验证实时引擎没有 trigger_conditions 分支机制

        即使场景数据中包含 trigger_conditions 字段，
        实时引擎也不会用它来决定下一场景——始终线性递增。
        """
        session = _make_realtime_session_with_scene()
        # 在 scenes 中添加一个带 trigger_conditions 的场景
        session.scenes.append({
            "scene_id": "scene_special",
            "scene_name": "特殊分支",
            "scene_index": 1,
            "narration": "特殊场景",
            "dialogues": [],
            "choices": [],
            "is_ending": True,
            "trigger_conditions": {"type": "choice_made", "value": "在scene_01选择联系警方"},
        })

        # 实时引擎的 choose() 不会检查 trigger_conditions
        # 它只是简单地 append 新场景并设置 current_scene_index = len(scenes) - 1
        captured_idx = {}

        def mock_gen_stream(scene_idx, player_choice=""):
            captured_idx["idx"] = scene_idx
            yield {"type": "scene_end", "scene_name": "线性场景",
                   "narration": "旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                   "is_ending": False, "scene_id": "scene_02",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    # 选择 "联系警方寻求帮助"（index=2）
                    list(session.choose(2))

        # 即使选择了"联系警方"，且存在 trigger_conditions 匹配的特殊场景，
        # 实时引擎仍然传入 scene_idx=1（线性递增），不会跳到特殊场景
        assert captured_idx["idx"] == 1, \
            "实时引擎应始终线性递增，不使用 trigger_conditions"

        session.close()

    def test_pre_generation_next_idx_always_plus_one(self):
        """预生成的 next_idx 始终为 scene_idx + 1"""
        session = _make_realtime_session_with_scene()
        captured_indices = []

        def mock_generate_scene(idx, player_choice="", scenes_snapshot=None, history_snapshot=None):
            captured_indices.append(idx)
            return {"scene_name": "预生成", "narration": "旁白",
                    "dialogues": [], "choices": [], "is_ending": True,
                    "scene_id": f"scene_{idx+1:02d}", "scene_index": idx}

        with patch.object(session, '_generate_scene', side_effect=mock_generate_scene):
            session._start_pre_generation(0)

        session._pre_gen_executor.shutdown(wait=True)

        # 所有预生成的 scene_idx 都应该是 1 (0 + 1)
        for idx in captured_indices:
            assert idx == 1, f"预生成 next_idx 应为 1，实际为 {idx}"

        session.close()


# ═══════════════════════════════════════════════════════
# 测试 4: 预生成缓存是否导致不同选项返回同一场景
# ═══════════════════════════════════════════════════════

class TestPreGenCacheDifferentiation:
    """验证预生成缓存是否正确区分不同选项"""

    def test_cache_key_includes_choice_index(self):
        """缓存 key 格式为 'scene_idx:choice_idx'，不同选项有不同 key"""
        session = _make_realtime_session_with_scene()

        # 模拟预生成写入不同 key
        with session._pre_gen_lock:
            session._pre_generated["0:0"] = {"scene_name": "场景A"}
            session._pre_generated["0:1"] = {"scene_name": "场景B"}
            session._pre_generated["0:2"] = {"scene_name": "场景C"}

        # 验证不同 key 对应不同场景
        assert session._pre_generated["0:0"]["scene_name"] == "场景A"
        assert session._pre_generated["0:1"]["scene_name"] == "场景B"
        assert session._pre_generated["0:2"]["scene_name"] == "场景C"

        session.close()

    def test_choose_pops_correct_cache_key(self):
        """choose() 应只弹出与所选选项对应的缓存 key"""
        session = _make_realtime_session_with_scene()

        with session._pre_gen_lock:
            session._pre_generated["0:0"] = {"scene_name": "A", "narration": "a",
                                             "dialogues": [], "choices": [],
                                             "is_ending": True, "scene_id": "s",
                                             "scene_index": 1}
            session._pre_generated["0:1"] = {"scene_name": "B", "narration": "b",
                                             "dialogues": [], "choices": [],
                                             "is_ending": True, "scene_id": "s",
                                             "scene_index": 1}
            session._pre_generated["0:2"] = {"scene_name": "C", "narration": "c",
                                             "dialogues": [], "choices": [],
                                             "is_ending": True, "scene_id": "s",
                                             "scene_index": 1}

        with patch.object(session, '_start_pre_generation'):
            with patch.object(session, 'save_to_yaml'):
                # 选择选项 1
                list(session.choose(1))

        # 选项 1 的缓存应被弹出，其他保留
        assert "0:1" not in session._pre_generated
        # 注意：choose() 的取消逻辑会清理所有 "0:" 前缀的 futures，
        # 但 _pre_generated 只弹出选中的 key
        # 实际上其他缓存可能仍在（取决于实现）

        session.close()

    def test_pre_gen_not_started_before_choice_made(self):
        """预生成在场景生成后立即启动，为所有选项预生成不同内容"""
        session = _make_realtime_session_with_scene()
        gen_choices = []

        def mock_generate_scene(idx, player_choice="", scenes_snapshot=None, history_snapshot=None):
            gen_choices.append(player_choice)
            return {"scene_name": f"场景_{player_choice}", "narration": "旁白",
                    "dialogues": [], "choices": [], "is_ending": True,
                    "scene_id": "scene_02", "scene_index": idx}

        with patch.object(session, '_generate_scene', side_effect=mock_generate_scene):
            session._start_pre_generation(0)

        session._pre_gen_executor.shutdown(wait=True)

        # 验证为每个选项都传入了对应的 choice_text
        assert len(gen_choices) == 3
        assert set(gen_choices) == {
            "立刻去调查发件人地址",
            "先备份数据再做打算",
            "联系警方寻求帮助",
        }

        session.close()

    def test_cache_miss_does_not_return_wrong_choice_scene(self):
        """缓存未命中时不应错误返回其他选项的缓存场景

        这是一个回归测试：确保 choose(2) 不会命中 "0:0" 或 "0:1" 的缓存。
        """
        session = _make_realtime_session_with_scene()

        # 只有选项 0 和 1 有缓存，选项 2 没有
        with session._pre_gen_lock:
            session._pre_generated["0:0"] = {"scene_name": "选项0的场景", "narration": "n0",
                                             "dialogues": [], "choices": [],
                                             "is_ending": True, "scene_id": "s",
                                             "scene_index": 1}
            session._pre_generated["0:1"] = {"scene_name": "选项1的场景", "narration": "n1",
                                             "dialogues": [], "choices": [],
                                             "is_ending": True, "scene_id": "s",
                                             "scene_index": 1}

        # 选择选项 2（无缓存）→ 应走即时生成路径
        generated_with_choice = {}

        def mock_gen_stream(scene_idx, player_choice=""):
            generated_with_choice["choice"] = player_choice
            yield {"type": "scene_end", "scene_name": "即时生成",
                   "narration": "即时旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                   "is_ending": False, "scene_id": "scene_02",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    list(session.choose(2))

        # 应走即时生成路径，且传入正确的 choice_text
        assert generated_with_choice.get("choice") == "联系警方寻求帮助", \
            "缓存未命中时应使用正确选项文本进行即时生成"

        session.close()


# ═══════════════════════════════════════════════════════
# 测试 5: Harness 管线 trigger_conditions 分支验证
# ═══════════════════════════════════════════════════════

class TestHarnessTriggerConditions:
    """验证 Harness 管线中 trigger_conditions 是否导致不同分支"""

    def test_choice_made_trigger_routes_to_different_scene(self):
        """不同选择应通过 trigger_conditions 路由到不同场景"""
        adapter = _make_mock_adapter_with_triggers()
        harness = _make_mock_harness()

        # 会话 1：选择"调查线索"
        session1 = GameSession(adapter)
        session1.generate(harness)
        session1.choose(0, "调查线索")
        next_idx_1 = session1.current_scene_index

        # 会话 2：选择"逃跑"
        adapter2 = _make_mock_adapter_with_triggers()
        session2 = GameSession(adapter2)
        session2.generate(harness)
        session2.choose(1, "逃跑")
        next_idx_2 = session2.current_scene_index

        # 验证不同选择导致不同的场景跳转
        # "调查线索" 应匹配 scene_03 (index=2) 的 trigger_conditions
        assert next_idx_1 == 2, \
            f"选择'调查线索'应跳转到 scene_03 (index=2)，实际跳到 index={next_idx_1}"
        # "逃跑" 应匹配 scene_04 (index=3) 的 trigger_conditions
        assert next_idx_2 == 3, \
            f"选择'逃跑'应跳转到 scene_04 (index=3)，实际跳到 index={next_idx_2}"

        # 两个会话的下一场景应该不同
        assert next_idx_1 != next_idx_2, \
            "不同选择应导致不同的场景跳转"

    def test_no_trigger_conditions_falls_back_to_linear(self):
        """无 trigger_conditions 匹配时回退到线性递增"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
            {"id": "scene_03", "name": "场景3"},
        ]
        adapter.worldline_nodes = ["A", "B", "C"]
        adapter.raw_data = {}
        adapter.build_harness_input.return_value = {
            "currentScene": "测试", "characters": [], "worldline": "A → B → C",
            "playerChoice": "",
        }
        adapter.get_scene_choices.return_value = []

        session = GameSession(adapter)
        harness = _make_mock_harness()
        session.generate(harness)
        session.choose(0, "任意选择")

        # 无 trigger_conditions → 线性递增到 index=1
        assert session.current_scene_index == 1

    def test_trigger_conditions_choice_text_matching(self):
        """trigger_conditions 的 choice_made 条件应正确匹配选项文本"""
        adapter = _make_mock_adapter_with_triggers()
        session = GameSession(adapter)

        # 模拟历史：在 scene_01 选择了 "调查线索"
        session.history = [{
            "scene_id": "scene_01",
            "player_choice": "调查线索",
            "scene_index": 0,
        }]
        session.current_scene_index = 0

        next_idx = session._resolve_next_scene_index()
        # 应匹配 scene_03 (index=2): "在scene_01选择调查线索"
        assert next_idx == 2, \
            f"应匹配 scene_03 (index=2)，实际为 {next_idx}"


# ═══════════════════════════════════════════════════════
# 测试 6: Harness 管线 player_choice 传递路径
# ═══════════════════════════════════════════════════════

class TestHarnessPlayerChoicePipeline:
    """验证 Harness 管线中 player_choice 的完整传递路径"""

    def test_player_choice_passed_to_build_harness_input(self):
        """choose 后 generate 应将 player_choice 传入 build_harness_input"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
            {"id": "scene_03", "name": "场景3"},
        ]
        adapter.worldline_nodes = ["A", "B", "C"]
        adapter.raw_data = {}
        adapter.build_harness_input.return_value = {
            "currentScene": "测试", "characters": [], "worldline": "A → B → C",
            "playerChoice": "",
        }
        adapter.get_scene_choices.return_value = [
            {"id": "c1", "text": "深入调查", "description": "", "consequences": {}},
        ]

        session = GameSession(adapter)
        harness = _make_mock_harness()

        # 第一次生成
        session.generate(harness)
        # 做出选择
        session.choose(0, "深入调查")

        # 第二次生成
        session.generate(harness)

        # 验证 build_harness_input 被调用时传入了 player_choice
        calls = adapter.build_harness_input.call_args_list
        assert len(calls) >= 2
        # 第二次调用应包含 player_choice="深入调查"
        second_call_kwargs = calls[1][1] if len(calls[1]) > 1 else {}
        second_call_args = calls[1]
        # 检查 player_choice 参数
        if "player_choice" in second_call_kwargs:
            assert second_call_kwargs["player_choice"] == "深入调查"
        else:
            # 可能是位置参数
            assert "深入调查" in str(second_call_args), \
                f"第二次 generate 应传入 player_choice='深入调查'，实际调用: {second_call_args}"

    def test_player_state_accumulates_across_choices(self):
        """玩家状态应随选择累积，并传入后续生成"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
            {"id": "scene_03", "name": "场景3"},
            {"id": "scene_04", "name": "场景4"},
        ]
        adapter.worldline_nodes = ["A", "B", "C", "D"]
        adapter.raw_data = {}
        adapter.build_harness_input.return_value = {
            "currentScene": "测试", "characters": [], "worldline": "A → B → C → D",
            "playerChoice": "",
        }
        adapter.get_scene_choices.return_value = [
            {"id": "c1", "text": "勇敢前进", "description": "",
             "consequences": {"stats_changes": {"courage": 3}},
             "personality_impact": "展现果断一面"},
        ]

        session = GameSession(adapter)
        harness = _make_mock_harness()

        # 第一轮
        session.generate(harness)
        session.choose(0, "勇敢前进")

        # 验证 player_state 已更新
        assert session.player_state.stats["courage"] == 3
        assert "展现果断一面" in session.player_state.personality_traits

        # 第二轮
        session.generate(harness)

        # 验证 player_state 被传入 build_harness_input
        calls = adapter.build_harness_input.call_args_list
        second_call = calls[1]
        # player_state 参数应包含累积的状态信息
        if len(second_call) > 1 and "player_state" in second_call[1]:
            ps_arg = second_call[1]["player_state"]
            assert ps_arg is not None
            assert "courage" in ps_arg


# ═══════════════════════════════════════════════════════
# 测试 7: 综合对比——不同选择的后续剧情差异
# ═══════════════════════════════════════════════════════

class TestEndToEndChoiceDivergence:
    """端到端验证：模拟同一场景不同选择后的后续剧情差异"""

    def test_three_choices_produce_three_different_scenes_via_pre_gen(self):
        """通过预生成机制，3 个选项应产生 3 个不同的后续场景"""
        session = _make_realtime_session_with_scene()

        # 模拟 LLM 根据 player_choice 生成不同内容
        def mock_generate_scene(idx, player_choice="", scenes_snapshot=None, history_snapshot=None):
            # 模拟 LLM 行为：根据选择生成不同场景
            if "调查" in player_choice:
                return {
                    "scene_name": "废弃公寓",
                    "narration": "你驱车来到发件人地址，发现是一栋废弃公寓。推开铁门，灰尘飞扬。",
                    "dialogues": [{"speaker": "林晓", "text": "这里已经很久没人住了。"}],
                    "choices": [{"id": "c1", "text": "搜索房间", "hint": ""}],
                    "is_ending": False, "scene_id": "scene_02", "scene_index": idx,
                }
            elif "备份" in player_choice:
                return {
                    "scene_name": "数据备份",
                    "narration": "你决定先确保安全。打开加密软件，将所有文件复制到三个不同的U盘。",
                    "dialogues": [{"speaker": "苏然", "text": "我来帮你加密。"}],
                    "choices": [{"id": "c1", "text": "分散存放", "hint": ""}],
                    "is_ending": False, "scene_id": "scene_02", "scene_index": idx,
                }
            elif "警方" in player_choice:
                return {
                    "scene_name": "报警求助",
                    "narration": "你拿起手机拨通了110。等待接通的过程中，你整理了手中的证据。",
                    "dialogues": [{"speaker": "林晓", "text": "你好，我要举报一起网络犯罪。"}],
                    "choices": [{"id": "c1", "text": "提供证据", "hint": ""}],
                    "is_ending": False, "scene_id": "scene_02", "scene_index": idx,
                }
            return None

        with patch.object(session, '_generate_scene', side_effect=mock_generate_scene):
            session._start_pre_generation(0)

        session._pre_gen_executor.shutdown(wait=True)

        # 验证缓存中有 3 个不同的场景
        with session._pre_gen_lock:
            scene_0 = session._pre_generated.get("0:0")
            scene_1 = session._pre_generated.get("0:1")
            scene_2 = session._pre_generated.get("0:2")

        assert scene_0 is not None, "选项 0 应有预生成缓存"
        assert scene_1 is not None, "选项 1 应有预生成缓存"
        assert scene_2 is not None, "选项 2 应有预生成缓存"

        # 验证三个场景内容不同
        assert scene_0["scene_name"] != scene_1["scene_name"]
        assert scene_1["scene_name"] != scene_2["scene_name"]
        assert scene_0["narration"] != scene_1["narration"]
        assert scene_1["narration"] != scene_2["narration"]

        # 验证内容与选择相关
        assert "废弃公寓" in scene_0["scene_name"] or "调查" in scene_0["narration"]
        assert "备份" in scene_1["scene_name"] or "备份" in scene_1["narration"]
        assert "报警" in scene_2["scene_name"] or "警方" in scene_2["narration"] or "110" in scene_2["narration"]

        session.close()

    def test_same_choice_different_sessions_same_pre_gen_key(self):
        """不同会话选择相同选项，预生成 key 结构一致但内容独立"""
        session_a = _make_realtime_session_with_scene()
        session_b = _make_realtime_session_with_scene()

        # 两个会话的缓存 key 结构相同
        with session_a._pre_gen_lock:
            session_a._pre_generated["0:0"] = {"scene_name": "A的场景"}
        with session_b._pre_gen_lock:
            session_b._pre_generated["0:0"] = {"scene_name": "B的场景"}

        # 但内容独立
        assert session_a._pre_generated["0:0"]["scene_name"] == "A的场景"
        assert session_b._pre_generated["0:0"]["scene_name"] == "B的场景"

        session_a.close()
        session_b.close()


# ═══════════════════════════════════════════════════════
# 测试 8: 现有测试覆盖缺口验证
# ═══════════════════════════════════════════════════════

class TestChoiceMadeBugReproduction:
    """复现核心 BUG：choice_made trigger_conditions 格式不兼容导致分支永远不生效

    真实脚本（回响-DEMO）中 scene_03 的 trigger_conditions:
      - type: "scene_completed"
        value: "scene_02"
      - type: "choice_made"
        value: "continue"       ← 简单值格式（选项ID）

    但 _check_condition 的正则只支持 "在scene_XX选择YY" 格式，
    导致简单值 "continue" 永远无法匹配 → 分支跳转永远失败 → 线性递增。
    """

    def _make_adapter_like_huixiang(self):
        """构建与回响-DEMO 结构一致的 mock adapter"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "信号", "location": "记忆工坊",
             "trigger_conditions": [{"type": "game_start", "value": "初始场景"}]},
            {"id": "scene_02", "name": "碎片", "location": "记忆空间",
             "trigger_conditions": [{"type": "scene_completed", "value": "scene_01"}]},
            {"id": "scene_03", "name": "对峙", "location": "织星科技总部",
             "trigger_conditions": [
                 {"type": "scene_completed", "value": "scene_02"},
                 {"type": "choice_made", "value": "continue"},  # ← 选项ID格式
             ]},
            {"id": "scene_04", "name": "回响", "location": "结局",
             "trigger_conditions": [{"type": "scene_completed", "value": "scene_03"}]},
        ]
        adapter.worldline_nodes = ["开端", "发展", "高潮", "结局"]
        adapter.raw_data = {"endings": []}
        adapter.build_harness_input.return_value = {
            "currentScene": "测试", "characters": [], "worldline": "",
            "playerChoice": "",
        }
        # 模拟 get_scene_choices 返回与关键选择.yaml 一致的选项
        adapter.get_scene_choices.return_value = [
            {"id": "continue", "text": "继续深入", "description": "不管多不安",
             "consequences": {"stats_changes": {"courage": 3}}},
            {"id": "stop", "text": "停止修复", "description": "太危险了",
             "consequences": {"stats_changes": {"self_preservation": 2}}},
        ]
        return adapter

    def test_bug_choice_made_simple_value_never_matches(self):
        """复现 BUG：choice_made value='continue' 永远无法匹配

        场景：玩家在 scene_02 选择了 "继续深入"(id=continue)，
        scene_03 的 trigger_conditions 要求 choice_made value="continue"，
        但 _check_condition 的正则无法解析这个简单值 → 返回 False → 线性递增。
        """
        adapter = self._make_adapter_like_huixiang()
        session = GameSession(adapter)
        harness = _make_mock_harness()

        # 模拟玩家已经完成了 scene_01 和 scene_02
        session.current_scene_index = 1  # 当前在 scene_02
        session.history = [
            {"scene_id": "scene_01", "player_choice": "立即解密附件",
             "option_id": "decrypt", "scene_index": 0},
            {"scene_id": "scene_02", "player_choice": "继续深入",
             "option_id": "continue", "scene_index": 1},
        ]

        # 调用 _resolve_next_scene_index
        next_idx = session._resolve_next_scene_index()

        # 期望：应跳转到 scene_03 (index=2)，因为：
        #   - scene_completed "scene_02" ✓ (history 中有 scene_02)
        #   - choice_made "continue" ✓ (玩家在 scene_02 选了 id=continue)
        # 实际 BUG：返回 default_next = 2（碰巧也是 2，因为线性递增）
        # 但如果 scene_03 后面还有 scene_03b（index=3），就不会跳到那里
        assert next_idx == 2, f"应进入 scene_03 (index=2)，实际 {next_idx}"

    def test_bug_choice_made_should_distinguish_different_choices(self):
        """核心测试：不同选择应导致不同的场景跳转

        场景 A：玩家选择 "继续深入"(id=continue) → 应匹配 scene_03
        场景 B：玩家选择 "停止修复"(id=stop) → 不应匹配 scene_03，应跳到其他场景
        """
        # ── 场景 A：选择了 continue ──
        adapter_a = self._make_adapter_like_huixiang()
        # 添加一个 "停止" 分支场景
        adapter_a.scenes.insert(3, {
            "id": "scene_03b", "name": "逃避",
            "location": "安全屋",
            "trigger_conditions": [
                {"type": "scene_completed", "value": "scene_02"},
                {"type": "choice_made", "value": "stop"},
            ],
        })
        session_a = GameSession(adapter_a)
        session_a.current_scene_index = 1
        session_a.history = [
            {"scene_id": "scene_01", "player_choice": "立即解密附件",
             "option_id": "decrypt", "scene_index": 0},
            {"scene_id": "scene_02", "player_choice": "继续深入",
             "option_id": "continue", "scene_index": 1},
        ]
        next_a = session_a._resolve_next_scene_index()

        # ── 场景 B：选择了 stop ──
        adapter_b = self._make_adapter_like_huixiang()
        adapter_b.scenes.insert(3, {
            "id": "scene_03b", "name": "逃避",
            "location": "安全屋",
            "trigger_conditions": [
                {"type": "scene_completed", "value": "scene_02"},
                {"type": "choice_made", "value": "stop"},
            ],
        })
        session_b = GameSession(adapter_b)
        session_b.current_scene_index = 1
        session_b.history = [
            {"scene_id": "scene_01", "player_choice": "立即解密附件",
             "option_id": "decrypt", "scene_index": 0},
            {"scene_id": "scene_02", "player_choice": "停止修复",
             "option_id": "stop", "scene_index": 1},
        ]
        next_b = session_b._resolve_next_scene_index()

        # 关键断言：不同选择应导致不同跳转
        # 选择 continue → scene_03 (index=2)
        # 选择 stop → scene_03b (index=3)
        assert next_a != next_b, (
            f"不同选择应导致不同跳转！"
            f"选择'继续深入'(continue) → index={next_a}，"
            f"选择'停止修复'(stop) → index={next_b}。"
            f"如果相同，说明 choice_made 条件未生效。"
        )

    def test_bug_choice_made_full_format_with_option_id(self):
        """复现 BUG：即使使用完整格式 "在scene_02选择continue"，
        由于 history 存储的是选项文本而非 ID，匹配仍然失败。
        """
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
            {"id": "scene_03", "name": "分支场景",
             "trigger_conditions": [
                 {"type": "choice_made", "value": "在scene_01选择investigate"},
             ]},
        ]
        adapter.worldline_nodes = ["A", "B", "C"]
        adapter.raw_data = {}

        session = GameSession(adapter)
        session.current_scene_index = 0
        # 玩家选择了 "先调查发件人"(id=investigate)
        session.history = [
            {"scene_id": "scene_01", "player_choice": "先调查发件人",
             "option_id": "investigate", "scene_index": 0},
        ]

        next_idx = session._resolve_next_scene_index()

        # 期望：匹配 scene_03 (index=2)
        # 实际 BUG："investigate" not in "先调查发件人" → False → 线性递增到 1
        assert next_idx == 2, (
            f"应匹配 scene_03 (index=2)，实际 {next_idx}。"
            f"原因：history 存储选项文本'先调查发件人'，"
            f"但条件要求匹配选项ID 'investigate'。"
        )


class TestCoverageGapVerification:
    """验证现有测试是否覆盖了'不同选择导致不同后续剧情'"""

    def test_history_records_choice_but_realtime_does_not_use_for_branching(self):
        """实时引擎记录 history 但不用于分支决策（仅用于 prompt 上下文）"""
        session = _make_realtime_session_with_scene()

        captured_prompts = []

        def mock_gen_stream(scene_idx, player_choice=""):
            # 捕获实际传入的 prompt
            prompt = session._build_scene_prompt(scene_idx, player_choice)
            captured_prompts.append(prompt)
            yield {"type": "scene_end", "scene_name": "场景",
                   "narration": "旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""}],
                   "is_ending": False, "scene_id": "scene_02",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    list(session.choose(0))

        # 验证 history 被记录
        assert len(session.history) == 1
        assert session.history[0]["choice_text"] == "立刻去调查发件人地址"

        # 验证 prompt 中包含了选择信息（用于 LLM 上下文）
        assert len(captured_prompts) == 1
        assert "立刻去调查发件人地址" in captured_prompts[0]

        # 但 scene_idx 始终是线性递增的（不是基于 history 做分支）
        assert session.current_scene_index == 1  # 0 + 1 = 1，线性

        session.close()

    def test_realtime_choice_affects_content_not_structure(self):
        """关键发现：实时引擎的选择影响内容（prompt）但不影响结构（scene_idx）

        这意味着：
        - 选择确实被传入 LLM prompt → 内容层面有差异
        - 但 scene_idx 始终 +1 → 结构层面是线性的
        - 没有 trigger_conditions 机制 → 无法跳转到不同场景
        - 预生成缓存按 choice_idx 区分 → 缓存层面正确
        """
        session = _make_realtime_session_with_scene()

        # 记录结构信息
        structural_info = []

        def mock_gen_stream(scene_idx, player_choice=""):
            structural_info.append({
                "scene_idx": scene_idx,
                "has_choice_in_prompt": bool(player_choice),
            })
            yield {"type": "scene_end", "scene_name": "场景",
                   "narration": "旁白", "dialogues": [],
                   "choices": [{"id": "c1", "text": "继续", "hint": ""},
                               {"id": "c2", "text": "另一条", "hint": ""}],
                   "is_ending": False, "scene_id": f"scene_{scene_idx+1:02d}",
                   "scene_index": scene_idx, "characters_present": []}

        with patch.object(session, '_generate_scene_stream', side_effect=mock_gen_stream):
            with patch.object(session, '_start_pre_generation'):
                with patch.object(session, 'save_to_yaml'):
                    list(session.choose(0))  # 选择第一个
                    list(session.choose(1))  # 选择第二个

        # 结构上：始终线性递增
        assert structural_info[0]["scene_idx"] == 1
        assert structural_info[1]["scene_idx"] == 2

        # 内容上：player_choice 被传入（影响 LLM 生成内容）
        assert structural_info[0]["has_choice_in_prompt"] is True
        assert structural_info[1]["has_choice_in_prompt"] is True

        session.close()


# ═══════════════════════════════════════════════════════
# 9. 世界线分支检测与上下文注入
# ═══════════════════════════════════════════════════════

class TestActiveBranchDetection:
    """验证 _detect_active_branch 能正确检测玩家所在分支并注入上下文"""

    def _make_adapter_with_worldlines(self):
        """构建含有世界线分支数据的 adapter"""
        from generic_adapter import GenericScriptAdapter
        adapter = GenericScriptAdapter()
        adapter.scenes = [
            {"id": "scene_01", "name": "信号", "location": "记忆工坊"},
            {"id": "scene_02", "name": "碎片", "location": "记忆空间"},
            {"id": "scene_03", "name": "对峝", "location": "织星科技"},
        ]
        adapter.raw_data = {
            "world_lines": [
                {
                    "id": "main_line",
                    "name": "主线",
                    "description": "基础路线",
                    "path": [
                        {"scene_id": "scene_01", "sequence": 1, "description": "开始"},
                        {"scene_id": "scene_02", "sequence": 2, "description": "发现"},
                    ],
                },
                {
                    "id": "branch_line_a",
                    "name": "真相线",
                    "description": "苏然选择深入真相",
                    "conditions": [{"type": "choice_made", "value": "在scene_02选择continue"}],
                    "path": [
                        {"scene_id": "scene_02", "sequence": 2, "description": "苏然选择继续深入修复"},
                        {"scene_id": "scene_03", "sequence": 3, "description": "苏然携带完整证据对峝"},
                    ],
                    "consequences": "苏然获得完整真相",
                },
                {
                    "id": "branch_line_b",
                    "name": "安全线",
                    "description": "苏然选择停止修复，保护自己",
                    "conditions": [{"type": "choice_made", "value": "在scene_02选择stop"}],
                    "path": [
                        {"scene_id": "scene_02", "sequence": 2, "description": "苏然选择停止修复"},
                        {"scene_id": "scene_03", "sequence": 3, "description": "苏然在不完全了解真相的情况下对峝"},
                    ],
                    "consequences": "苏然放弃了了解真相的机会",
                },
            ],
        }
        return adapter

    def test_detect_branch_stop(self):
        """选择 stop 后应检测到安全线分支"""
        adapter = self._make_adapter_with_worldlines()
        history = [
            {"scene_id": "scene_02", "player_choice": "停止修复", "option_id": "stop"},
        ]
        result = adapter._detect_active_branch(history)
        assert "安全线" in result
        assert "不完全了解真相" in result

    def test_detect_branch_continue(self):
        """选择 continue 后应检测到真相线分支"""
        adapter = self._make_adapter_with_worldlines()
        history = [
            {"scene_id": "scene_02", "player_choice": "继续深入", "option_id": "continue"},
        ]
        result = adapter._detect_active_branch(history)
        assert "真相线" in result
        assert "完整真相" in result or "完整证据" in result

    def test_no_branch_without_history(self):
        """无选择历史时不返回分支"""
        adapter = self._make_adapter_with_worldlines()
        assert adapter._detect_active_branch(None) == ""
        assert adapter._detect_active_branch([]) == ""

    def test_branch_context_injected_in_harness_input(self):
        """确认 build_harness_input 中注入了 _activeBranchContext"""
        adapter = self._make_adapter_with_worldlines()
        adapter.characters = [{"name": "苏然", "role": "主角"}]
        adapter.worldline_nodes = ["开始", "发现", "对峝", "结局"]
        history = [
            {"scene_id": "scene_02", "scene_name": "碎片",
             "player_choice": "停止修复", "option_id": "stop",
             "consequences": "你退出记忆空间"},
        ]
        result = adapter.build_harness_input(
            scene_index=2,
            player_choice="停止修复",
            choice_history=history,
        )
        assert "_activeBranchContext" in result
        assert "安全线" in result["_activeBranchContext"]
        assert "不完全了解真相" in result["_activeBranchContext"]

    def test_different_branches_produce_different_context(self):
        """确认不同选择产生不同的分支上下文"""
        adapter = self._make_adapter_with_worldlines()
        adapter.characters = [{"name": "苏然", "role": "主角"}]
        adapter.worldline_nodes = ["开始", "发现", "对峝"]

        history_stop = [{"scene_id": "scene_02", "scene_name": "碎片",
                         "player_choice": "停止修复", "option_id": "stop"}]
        history_continue = [{"scene_id": "scene_02", "scene_name": "碎片",
                             "player_choice": "继续深入", "option_id": "continue"}]

        ctx_stop = adapter.build_harness_input(2, player_choice="停止修复",
                                               choice_history=history_stop)
        ctx_cont = adapter.build_harness_input(2, player_choice="继续深入",
                                               choice_history=history_continue)

        branch_stop = ctx_stop.get("_activeBranchContext", "")
        branch_cont = ctx_cont.get("_activeBranchContext", "")

        # 两个分支上下文必须不同
        assert branch_stop != branch_cont
        assert "安全线" in branch_stop
        assert "真相线" in branch_cont


class TestResolveNextSceneFallback:
    """验证 _resolve_next_scene_index 回退逻辑不会落入条件失败的场景"""

    def test_fallback_skips_condition_failed_scene(self):
        """当 default_next 场景条件失败时，应跳过它"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "A"},
            {"id": "scene_02", "name": "B"},
            {"id": "scene_03", "name": "C",
             "trigger_conditions": [{"type": "choice_made", "value": "go_left"}]},
            {"id": "scene_04", "name": "D"},  # 无条件 = 可进入
        ]
        session = GameSession(adapter)
        session.current_scene_index = 1
        session.history = [
            {"scene_id": "scene_02", "player_choice": "向右走", "option_id": "go_right"},
        ]
        # scene_03 条件失败（玩家选了 go_right 不是 go_left）
        # 应该跳过 scene_03，进入 scene_04
        result = session._resolve_next_scene_index()
        assert result == 3  # scene_04 index

    def test_fallback_returns_default_when_all_fail(self):
        """所有后续场景条件都失败时，回退到 default_next 避免卡死"""
        adapter = MagicMock()
        adapter.scenes = [
            {"id": "scene_01", "name": "A"},
            {"id": "scene_02", "name": "B"},
            {"id": "scene_03", "name": "C",
             "trigger_conditions": [{"type": "choice_made", "value": "go_left"}]},
            {"id": "scene_04", "name": "D",
             "trigger_conditions": [{"type": "scene_completed", "value": "scene_03"}]},
        ]
        session = GameSession(adapter)
        session.current_scene_index = 1
        session.history = [
            {"scene_id": "scene_02", "player_choice": "向右走", "option_id": "go_right"},
        ]
        # scene_03 条件失败，scene_04 条件也失败，回退到 default_next=2
        result = session._resolve_next_scene_index()
        assert result == 2  # 回退到 default_next 避免卡死
