# -*- coding: utf-8 -*-
"""GameSession 单元测试

测试覆盖：
  1. 会话初始化状态 (IDLE)
  2. 进度计算 (progress, total_scenes)
  3. 状态迁移: IDLE → GENERATED (generate)
  4. 状态迁移: GENERATED → CHOSEN (choose)
  5. 状态迁移: CHOSEN → GENERATED (generate again)
  6. 状态迁移: 最后场景 choose → FINISHED
  7. 非法状态操作防护
  8. 场景跳转 (_resolve_next_scene_index)
  9. 结局匹配 (_match_ending)
  10. 状态查询 (get_state)
  11. PlayerState 属性/关系/性格追踪
"""

from unittest.mock import MagicMock, patch, PropertyMock
import pytest
from fastapi import HTTPException

from session_manager import (
    GameSession, SessionState, PlayerState, ChoiceRequest,
)
from schemas import Character, Dialogue, Choice


# ═══════════════════════════════════════════════════════
# 测试辅助 fixtures
# ═══════════════════════════════════════════════════════

def make_mock_adapter(
    scenes: list[dict] = None,
    worldline_nodes: list[str] = None,
    raw_data: dict = None,
):
    """构建 mock GenericScriptAdapter"""
    adapter = MagicMock()
    # 注意：不能用 `scenes or [...]`，否则显式传入空列表（空剧本场景）会被默认值覆盖
    adapter.scenes = [
        {"id": "scene_01", "name": "场景1", "location": "地点1", "description": "描述1"},
        {"id": "scene_02", "name": "场景2", "location": "地点2", "description": "描述2"},
        {"id": "scene_03", "name": "场景3", "location": "地点3", "description": "描述3"},
    ] if scenes is None else scenes
    adapter.worldline_nodes = worldline_nodes or ["开端", "发展", "结局"]
    adapter.raw_data = raw_data or {}

    # build_harness_input 返回标准 dict
    adapter.build_harness_input.return_value = {
        "currentScene": "测试场景",
        "characters": [
            {"name": "角色A", "role": "主角", "personality": "冷静"},
            {"name": "角色B", "role": "配角", "personality": "热血"},
        ],
        "worldline": "开端 → 发展 → 结局",
        "playerChoice": "",
    }

    # get_scene_choices 返回空列表（无预设选项）
    adapter.get_scene_choices.return_value = []

    return adapter


def make_mock_harness():
    """构建 mock Harness"""
    harness = MagicMock()
    harness.run.return_value = {
        "success": True,
        "data": {
            "narration": "测试旁白文本",
            "dialogues": [
                {"speaker": "角色A", "text": "你好", "emotion": "平静"},
                {"speaker": "角色B", "text": "你好", "emotion": "平静"},
                {"speaker": "角色A", "text": "我们开始吧", "emotion": "坚定"},
            ],
            "nextChoices": [
                {"text": "选项1", "effect": "推进主线", "worldlineImpact": "advance"},
                {"text": "选项2", "effect": "探索支线", "worldlineImpact": "side_branch"},
            ],
            "worldlineState": {
                "currentNode": "开端",
                "nodeIndex": 0,
                "totalNodes": 3,
                "progress": 0.0,
            },
        },
        "validation": {"passed": True, "checks": [], "retryCount": 0},
        "metadata": {"generationTime": 1000, "model": "test-model"},
    }
    return harness


# ═══════════════════════════════════════════════════════
# 会话初始化测试
# ═══════════════════════════════════════════════════════

class TestGameSessionInit:
    """会话初始化"""

    def test_initial_state(self):
        session = GameSession(make_mock_adapter())
        assert session.state == SessionState.IDLE
        assert session.current_scene_index == 0
        assert session.history == []
        assert session.previous_result is None
        assert session.current_result is None
        assert len(session.session_id) == 8

    def test_session_id_unique(self):
        """不同会话应有不同 session_id"""
        s1 = GameSession(make_mock_adapter())
        s2 = GameSession(make_mock_adapter())
        assert s1.session_id != s2.session_id

    def test_total_scenes_from_scenes(self):
        """total_scenes 优先从 scenes 获取"""
        adapter = make_mock_adapter(scenes=[
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"}, {"id": "s4"},
        ])
        session = GameSession(adapter)
        assert session.total_scenes == 4

    def test_total_scenes_fallback_to_nodes(self):
        """scenes 为空时 total_scenes 应为 0（不回退到 worldline_nodes，避免越界生成 500）"""
        adapter = make_mock_adapter(scenes=[], worldline_nodes=["A", "B", "C"])
        session = GameSession(adapter)
        assert session.total_scenes == 0

    def test_progress_zero(self):
        session = GameSession(make_mock_adapter(scenes=[
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"},
        ]))
        assert session.progress == 0.0

    def test_player_state_initial(self):
        session = GameSession(make_mock_adapter())
        assert session.player_state.stats == {}
        assert session.player_state.relationships == {}
        assert session.player_state.personality_traits == []


# ═══════════════════════════════════════════════════════
# 状态迁移: generate (IDLE → GENERATED)
# ═══════════════════════════════════════════════════════

class TestGenerate:
    """generate 状态迁移"""

    def test_idle_to_generated(self):
        """IDLE 状态执行 generate 后应变为 GENERATED"""
        session = GameSession(make_mock_adapter())
        harness = make_mock_harness()

        result = session.generate(harness)

        assert session.state == SessionState.GENERATED
        assert session.current_result is not None
        assert result["session_state"] == "generated"
        assert "narration" in result["data"]
        assert "dialogues" in result["data"]
        assert "scene_meta" in result

    def test_already_generated_raises(self):
        """GENERATED 状态再次 generate 应抛出异常"""
        session = GameSession(make_mock_adapter())
        harness = make_mock_harness()
        session.state = SessionState.GENERATED

        with pytest.raises(HTTPException) as exc_info:
            session.generate(harness)
        assert exc_info.value.status_code == 400
        assert "已生成" in exc_info.value.detail

    def test_finished_raises_on_generate(self):
        """FINISHED 状态 generate 应抛出异常"""
        session = GameSession(make_mock_adapter())
        harness = make_mock_harness()
        session.state = SessionState.FINISHED

        with pytest.raises(HTTPException) as exc_info:
            session.generate(harness)
        assert "已结束" in exc_info.value.detail

    def test_no_more_scenes_raises(self):
        """场景索引超出时应抛出异常"""
        adapter = make_mock_adapter(scenes=[{"id": "s1"}])
        session = GameSession(adapter)
        session.current_scene_index = 1  # >= total_scenes
        harness = make_mock_harness()

        with pytest.raises(HTTPException) as exc_info:
            session.generate(harness)
        assert "没有更多场景" in exc_info.value.detail


# ═══════════════════════════════════════════════════════
# 状态迁移: choose (GENERATED → CHOSEN / FINISHED)
# ═══════════════════════════════════════════════════════

class TestChoose:
    """choose 状态迁移"""

    def test_generated_to_chosen(self):
        """GENERATED 状态选择非最后场景 → CHOSEN"""
        adapter = make_mock_adapter(scenes=[
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
            {"id": "scene_03", "name": "场景3"},
        ])
        session = GameSession(adapter)
        harness = make_mock_harness()
        session.generate(harness)

        result = session.choose(choice_index=0, choice_text="选项1")

        assert session.state == SessionState.CHOSEN
        assert result["session_state"] == "chosen"
        assert result["message"] == "选择已确认"
        assert len(session.history) == 1
        assert session.current_result is None

    def test_not_generated_raises(self):
        """非 GENERATED 状态 choose 应抛出异常"""
        session = GameSession(make_mock_adapter())

        with pytest.raises(HTTPException) as exc_info:
            session.choose(0, "选项1")
        assert "无法做出选择" in exc_info.value.detail

    def test_last_scene_to_finished(self):
        """选择最后一个场景 → FINISHED"""
        adapter = make_mock_adapter(scenes=[
            {"id": "scene_01", "name": "最终场景"},
        ])
        adapter.get_scene_choices.return_value = [
            {"id": "choice_1", "text": "结束", "description": "结束故事"},
        ]
        session = GameSession(adapter)
        harness = make_mock_harness()
        session.generate(harness)

        result = session.choose(0, "结束")

        assert session.state == SessionState.FINISHED
        assert result["session_state"] == "finished"
        assert "故事已结束" in result["message"]
        assert "ending" in result

    def test_choose_triggers_scene_advance(self):
        """选择后 scene_index 应推进"""
        adapter = make_mock_adapter(scenes=[
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"},
        ])
        session = GameSession(adapter)
        harness = make_mock_harness()
        session.generate(harness)

        assert session.current_scene_index == 0
        session.choose(0, "选项1")
        # 无 trigger_conditions → 线性递增
        assert session.current_scene_index == 1

    def test_choose_stores_history(self):
        """选择应存储到 history"""
        adapter = make_mock_adapter(scenes=[
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "场景2"},
        ])
        session = GameSession(adapter)
        harness = make_mock_harness()
        session.generate(harness)
        session.choose(0, "选项1")

        assert len(session.history) == 1
        assert session.history[0]["scene_id"] == "scene_01"
        assert session.history[0]["player_choice"] == "选项1"


# ═══════════════════════════════════════════════════════
# 场景跳转测试
# ═══════════════════════════════════════════════════════

class TestResolveNextSceneIndex:
    """场景跳转逻辑"""

    def test_default_linear_advance(self):
        """无 trigger_conditions 时线性递增"""
        adapter = make_mock_adapter(scenes=[
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"},
        ])
        session = GameSession(adapter)
        # 无历史，无 trigger_conditions → 默认 linear
        next_idx = session._resolve_next_scene_index()
        assert next_idx == 1

    def test_scene_completed_trigger(self):
        """scene_completed 条件匹配时应跳转到对应场景"""
        adapter = make_mock_adapter(scenes=[
            {"id": "scene_01", "name": "场景1"},
            {"id": "scene_02", "name": "普通场景"},
            {"id": "scene_special", "name": "特殊场景",
             "trigger_conditions": {"type": "scene_completed", "value": "scene_01"}},
            {"id": "scene_03", "name": "场景3"},
        ])
        session = GameSession(adapter)
        # 模拟已访问过 scene_01
        session.history = [{"scene_id": "scene_01", "player_choice": "选项1"}]

        next_idx = session._resolve_next_scene_index()
        # 应跳过 scene_02，直接到 scene_special (index 2)
        assert next_idx == 2

    def test_empty_trigger_conditions_skip(self):
        """空 trigger_conditions 的场景应被跳过"""
        adapter = make_mock_adapter(scenes=[
            {"id": "s1"},  # 0
            {"id": "s2", "trigger_conditions": {}},  # 1 - 空条件，跳过
            {"id": "s3"},  # 2
        ])
        session = GameSession(adapter)
        next_idx = session._resolve_next_scene_index()
        # 跳过空条件场景 s2，但最终回退到线性递增 1
        # 因为代码逻辑是：无条件场景 continue 跳过，后面无匹配则返回 default_next (1)
        assert next_idx == 1


# ═══════════════════════════════════════════════════════
# 结局匹配测试
# ═══════════════════════════════════════════════════════

class TestMatchEnding:
    """结局匹配"""

    def test_no_endings_returns_none(self):
        session = GameSession(make_mock_adapter())
        result = session._match_ending([], [])
        assert result is None

    def test_match_by_choice(self):
        endings = [
            {
                "title": "好结局",
                "type": "good",
                "narrative": "一切都很美好",
                "trigger_conditions": [
                    {"type": "choice_made", "value": "在scene_01选择帮助"},
                ],
            },
        ]
        history = [
            {"scene_id": "scene_01", "player_choice": "帮助"},
        ]
        session = GameSession(make_mock_adapter())
        result = session._match_ending(endings, history)

        assert result is not None
        assert result["title"] == "好结局"
        assert result["type"] == "good"

    def test_empty_history_low_score(self):
        """空历史时结局匹配得分应为 0（未命中任何条件），不应返回零分结局"""
        endings = [
            {
                "title": "隐藏结局",
                "type": "hidden",
                "trigger_conditions": [
                    {"type": "choice_made", "value": "在scene_01选择特殊选项"},
                ],
            },
        ]
        session = GameSession(make_mock_adapter())
        result = session._match_ending(endings, [])
        # 空历史时 normalized_score = 0，零分不应被当作匹配成功
        # （旧行为会无条件返回结局列表第一个，导致玩家选择与结局无关）
        assert result is None


# ═══════════════════════════════════════════════════════
# get_state 测试
# ═══════════════════════════════════════════════════════

class TestGetState:
    """状态查询"""

    def test_get_state_idle(self):
        session = GameSession(make_mock_adapter())
        state = session.get_state()

        assert state["state"] == "idle"
        assert state["session_id"] == session.session_id
        assert "current_scene" in state
        assert state["total_scenes"] == 3
        assert state["completed_scenes"] == 0

    def test_get_state_after_generate(self):
        session = GameSession(make_mock_adapter())
        harness = make_mock_harness()
        session.generate(harness)

        state = session.get_state()
        assert state["state"] == "generated"
        assert state["completed_scenes"] == 0  # 还未选择

    def test_get_state_after_choose(self):
        adapter = make_mock_adapter(scenes=[
            {"id": "s1"}, {"id": "s2"}, {"id": "s3"},
        ])
        session = GameSession(adapter)
        harness = make_mock_harness()
        session.generate(harness)
        session.choose(0, "选项1")

        state = session.get_state()
        assert state["state"] == "chosen"
        assert state["completed_scenes"] == 1  # 已完成一个场景


# ═══════════════════════════════════════════════════════
# PlayerState 测试
# ═══════════════════════════════════════════════════════

class TestPlayerState:
    """玩家状态追踪"""

    def test_apply_stats_changes(self):
        ps = PlayerState()
        cons = {"stats_changes": {"courage": 3, "mental_stability": -2}}
        ps.apply_consequences(cons)

        assert ps.stats["courage"] == 3
        assert ps.stats["mental_stability"] == -2

    def test_apply_relationship_changes(self):
        ps = PlayerState()
        cons = {
            "relationship_changes": [
                {"character": "char_a", "change": 2, "reason": "帮助了ta"},
                {"character": "char_b", "change": -1, "reason": "拒绝了ta"},
            ],
        }
        ps.apply_consequences(cons)

        assert ps.relationships["char_a"] == 2
        assert ps.relationships["char_b"] == -1
        assert "帮助了ta" in ps.relationship_reasons["char_a"]

    def test_apply_personality_impact(self):
        ps = PlayerState()
        option_data = {"personality_impact": "展现了果断、勇敢的一面"}
        ps.apply_consequences({}, option_data)

        assert "展现了果断、勇敢的一面" in ps.personality_traits

    def test_accumulate_multiple_choices(self):
        """多次选择应累积效果"""
        ps = PlayerState()
        ps.apply_consequences({"stats_changes": {"courage": 2}})
        ps.apply_consequences({"stats_changes": {"courage": 1}})
        assert ps.stats["courage"] == 3

    def test_format_for_prompt_with_stats(self):
        ps = PlayerState()
        ps.apply_consequences({"stats_changes": {"courage": 3, "mental_stability": -1}})
        formatted = ps.format_for_prompt()

        assert "courage" in formatted
        assert "+3" in formatted
        assert "mental_stability" in formatted

    def test_format_for_prompt_empty(self):
        ps = PlayerState()
        assert ps.format_for_prompt() == ""


# ═══════════════════════════════════════════════════════
# ChoiceRequest 测试
# ═══════════════════════════════════════════════════════

class TestChoiceRequest:
    """选项请求模型"""

    def test_model_creation(self):
        req = ChoiceRequest(choice_text="选项1", choice_index=0)
        assert req.choice_text == "选项1"
        assert req.choice_index == 0
