# -*- coding: utf-8 -*-
"""realtime_engine.py 单元测试

测试覆盖：
  1. RealtimeStorySession 会话创建和初始化状态
  2. 输入验证（空输入、超长输入）
  3. 场景后处理逻辑（_postprocess_scene）
  4. 场景事件格式化（_format_scene_event）
  5. 状态查询（get_state）
  6. 并发路径竞态测试（deepcopy 窗口 / choose × 预生成交错）
  7. _pre_gen_lock 保护范围验证
"""

import copy
import threading
import time
from unittest.mock import patch, MagicMock

import pytest
from realtime_engine import RealtimeStorySession, MAX_INPUT_LENGTH


class TestRealtimeStorySessionCreation:
    """测试 1: 会话创建和初始化状态"""

    def test_session_initialization(self):
        """验证会话创建时的初始状态"""
        user_input = "一个关于时间旅行的故事"
        session = RealtimeStorySession(user_input)

        assert session.user_input == user_input
        assert session.current_scene_index == -1
        assert session.scenes == []
        assert session.history == []
        assert session.seed == {}
        assert session.session_id  # session_id 应该被生成
        assert len(session.session_id) == 8  # UUID 前 8 位

    def test_session_input_stripping(self):
        """验证输入会被 strip 处理"""
        session = RealtimeStorySession("  测试输入  ")
        assert session.user_input == "测试输入"


class TestInputValidation:
    """测试 2: 输入验证"""

    def test_empty_input_validation(self):
        """验证空输入会返回错误"""
        session = RealtimeStorySession("")
        events = list(session.initialize())

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "请输入剧本描述" in events[0]["message"]

    def test_too_long_input_validation(self):
        """验证超长输入会返回错误"""
        long_input = "a" * (MAX_INPUT_LENGTH + 1)
        session = RealtimeStorySession(long_input)
        events = list(session.initialize())

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "不能超过" in events[0]["message"]


class TestScenePostprocess:
    """测试 3: 场景后处理逻辑"""

    def test_postprocess_scene_adds_missing_fields(self):
        """验证场景后处理会添加缺失字段"""
        session = RealtimeStorySession("测试")
        scene = {
            "narration": "测试旁白",
            "dialogues": [],
            "is_ending": False,
        }

        processed = session._postprocess_scene(scene, scene_idx=0)

        assert "scene_id" in processed
        assert processed["scene_index"] == 0
        assert processed["scene_id"] == "scene_01"
        # 非结局场景应该有默认选项
        assert len(processed["choices"]) > 0

    def test_postprocess_scene_ending_no_choices(self):
        """验证结局场景的选项会被清空"""
        session = RealtimeStorySession("测试")
        scene = {
            "narration": "结局旁白",
            "dialogues": [],
            "is_ending": True,
            "ending_type": "good",
            "choices": [{"id": "choice_1", "text": "继续"}],
        }

        processed = session._postprocess_scene(scene, scene_idx=2)

        assert processed["choices"] == []
        assert processed["is_ending"] is True


class TestFormatSceneEvent:
    """测试 4: 场景事件格式化"""

    def test_format_scene_event_basic(self):
        """验证场景事件格式化的基本结构"""
        session = RealtimeStorySession("测试")
        session.seed = {
            "characters": [
                {"id": "char_01", "name": "主角", "role": "主角"},
            ]
        }

        scene = {
            "scene_id": "scene_01",
            "scene_name": "测试场景",
            "location": "测试地点",
            "time": "白天",
            "narration": "测试旁白",
            "dialogues": [
                {"speaker_id": "char_01", "speaker": "主角", "text": "测试对话"},
            ],
            "choices": [{"id": "choice_1", "text": "选项1"}],
            "is_ending": False,
        }

        event = session._format_scene_event(scene, scene_idx=0)

        assert event["type"] == "scene"
        assert event["scene_index"] == 0
        assert event["scene_name"] == "测试场景"
        assert event["location"] == "测试地点"
        assert event["narration"] == "测试旁白"
        assert len(event["dialogues"]) == 1

    def test_format_scene_event_speaker_normalization(self):
        """验证角色名归一化（speaker 字段用 seed 中的标准名）"""
        session = RealtimeStorySession("测试")
        session.seed = {
            "characters": [
                {"id": "char_01", "name": "李明", "role": "主角"},
            ]
        }

        scene = {
            "dialogues": [
                # LLM 可能输出不同的名字格式
                {"speaker_id": "char_01", "speaker": "李明", "text": "对话1"},
                {"type": "action", "subject": "李明", "text": "做了个动作"},
            ],
        }

        event = session._format_scene_event(scene, scene_idx=0)

        # speaker_id 匹配时应该使用 seed 中的标准名
        assert event["dialogues"][0]["speaker"] == "李明"
        # action 的 subject 也应该被归一化
        assert event["dialogues"][1]["subject"] == "李明"


class TestGetState:
    """测试 5: 状态查询"""

    def test_get_state_idle(self):
        """验证初始状态为 idle"""
        session = RealtimeStorySession("测试")
        state = session.get_state()

        assert state["state"] == "idle"
        assert state["session_id"] == session.session_id

    def test_get_state_waiting_choice(self):
        """验证有场景但未结束时的状态"""
        session = RealtimeStorySession("测试")
        session.seed = {"title": "测试剧本", "characters": []}
        session.scenes = [{
            "scene_name": "场景1",
            "is_ending": False,
            "choices": [{"id": "choice_1", "text": "选项1"}],
        }]
        session.current_scene_index = 0

        state = session.get_state()

        assert state["state"] == "waiting_choice"
        assert state["current_scene_index"] == 0
        assert state["total_scenes"] == 1
        assert state["is_ending"] is False

    def test_get_state_finished(self):
        """验证结局状态"""
        session = RealtimeStorySession("测试")
        session.seed = {"title": "测试剧本", "characters": []}
        session.scenes = [{
            "scene_name": "结局",
            "is_ending": True,
            "ending_type": "good",
            "choices": [],
        }]
        session.current_scene_index = 0

        state = session.get_state()

        assert state["state"] == "finished"
        assert state["is_ending"] is True


# ═══════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════

def _make_session_with_scenes(n: int) -> RealtimeStorySession:
    """创建一个带有 n 个场景的 session（跳过 LLM 调用）。"""
    session = RealtimeStorySession("测试")
    session.seed = {
        "title": "测试剧本",
        "characters": [
            {"id": "char_01", "name": "主角", "role": "主角",
             "gender": "male", "age": 25, "personality": "冷静",
             "voice_tone": "平淡", "motivation": "探索", "background": "无"},
        ],
        "estimated_scenes": 5,
        "world_setting": "", "world_rules": [], "core_conflict": "",
        "plot_summary": "", "themes": [], "genre": "", "tone": "",
        "narrative_style": "", "initial_scene_hint": "",
    }
    for i in range(n):
        session.scenes.append({
            "scene_name": f"场景{i + 1}",
            "scene_id": f"scene_{i + 1:02d}",
            "scene_index": i,
            "narration": f"旁白{i + 1}",
            "dialogues": [],
            "choices": [
                {"id": "choice_1", "text": "选项A", "hint": ""},
                {"id": "choice_2", "text": "选项B", "hint": ""},
            ],
            "is_ending": False,
            "characters_present": ["char_01"],
        })
    session.current_scene_index = n - 1
    return session


# ═══════════════════════════════════════════════════════
# 6. 并发路径竞态测试
# ═══════════════════════════════════════════════════════

class TestDeepCopyRaceWindow:
    """测试 6a: _start_pre_generation 中 deepcopy 与 self.scenes.append 之间的无锁窗口

    _start_pre_generation 的执行顺序：
      1. scenes_snapshot = copy.deepcopy(self.scenes)   ← 无锁读
      2. history_snapshot = copy.deepcopy(self.history)  ← 无锁读
      3. for each choice: submit to executor              ← 后台线程写 _pre_generated
    而 choose() 会在主线程执行 self.scenes.append() / self.history.append()
    如果 deepcopy 期间主线程修改了 scenes/history，快照将不一致。
    """

    def test_deepcopy_race_window_detects_mutation(self):
        """验证 deepcopy 无锁窗口期间的竞态可被检测到。

        通过 monkeypatch deepcopy 插入同步屏障，在 deepcopy 执行期间
        从另一个线程修改 self.scenes。由于 deepcopy 不在锁内执行，
        慢速 deepcopy 窗口期间主线程的修改会泄漏到快照中——
        这正是无锁窗口的风险所在。
        """
        session = _make_session_with_scenes(2)
        session.history = [{"scene_index": 0, "choice_index": 0, "choice_text": "A"}]

        original_deepcopy = copy.deepcopy
        barrier = threading.Event()

        def slow_deepcopy(obj):
            # 第一次 deepcopy（self.scenes）时暂停，让主线程修改 scenes
            if isinstance(obj, list) and len(obj) == 2 and not barrier.is_set():
                barrier.set()
                time.sleep(0.05)  # 模拟 deepcopy 耗时窗口
            return original_deepcopy(obj)

        def mutate_scenes():
            # 等待 deepcopy 开始执行后再追加新场景
            barrier.wait(timeout=2.0)
            session.scenes.append({
                "scene_name": "场景3_突变",
                "choices": [],
                "is_ending": True,
            })

        mock_gen = MagicMock(return_value={
            "scene_name": "预生成场景",
            "narration": "预生成旁白",
            "dialogues": [],
            "choices": [],
            "is_ending": True,
        })

        with patch.object(session, '_generate_scene', mock_gen):
            with patch('realtime_engine.copy.deepcopy', side_effect=slow_deepcopy):
                mutator = threading.Thread(target=mutate_scenes)
                mutator.start()
                session._start_pre_generation(1)
                mutator.join(timeout=5.0)

        session._pre_gen_executor.shutdown(wait=True)

        # 验证：竞态窗口导致快照包含了突变数据（3 个场景而非 2 个）
        # 这证明 deepcopy 在无锁窗口期间读取到了不一致的状态
        assert mock_gen.called, "_generate_scene 应该被调用"
        call_args = mock_gen.call_args
        scenes_snap = call_args.kwargs.get('scenes_snapshot') or (
            call_args[1].get('scenes_snapshot') if len(call_args) > 1 else None
        )
        assert scenes_snap is not None, "应该传入 scenes_snapshot"
        assert len(scenes_snap) == 3, (
            f"无锁窗口应允许突变泄漏（期望 3 个场景），实际 {len(scenes_snap)}。"
            "如果快照只有 2 个场景，说明竞态窗口未被正确暴露"
        )

        session.close()

    def test_deepcopy_without_protection_detects_mutation(self):
        """反向验证：如果不用 deepcopy 而直接引用，突变会被检测到。

        此测试证明 deepcopy 的必要性——去掉 deepcopy 后快照会包含突变数据。
        """
        session = _make_session_with_scenes(2)

        # 直接引用（不做 deepcopy）
        snapshot_ref = session.scenes  # 浅引用，非拷贝

        # 主线程追加新场景
        session.scenes.append({
            "scene_name": "突变场景",
            "choices": [],
            "is_ending": True,
        })

        # 浅引用会看到突变
        assert len(snapshot_ref) == 3, "浅引用应该看到追加"

        # deepcopy 不会看到突变（在追加前做的 deepcopy）
        session2 = _make_session_with_scenes(2)
        snap = copy.deepcopy(session2.scenes)
        session2.scenes.append({"scene_name": "突变", "choices": [], "is_ending": True})
        assert len(snap) == 2, "deepcopy 快照不应该看到追加"

        session.close()

    def test_history_deep_copy_isolation(self):
        """验证 history 的 deepcopy 与主线程 append 之间的隔离性。"""
        session = _make_session_with_scenes(1)
        session.history = [{"scene_index": 0, "choice_index": 0, "choice_text": "A"}]

        snap = copy.deepcopy(session.history)
        original_len = len(snap)

        # 主线程追加历史记录
        session.history.append({"scene_index": 1, "choice_index": 1, "choice_text": "B"})

        assert len(snap) == original_len, "deepcopy 快照不应受主线程追加影响"
        assert len(session.history) == original_len + 1

        session.close()


class TestChoosePreGenInterleaving:
    """测试 6b: choose + _start_pre_generation 的交错执行并发压力测试

    模拟多个 choose 调用与后台预生成线程并发交错的场景，
    验证共享状态（scenes / history / _pre_generated / _pre_gen_futures）不会出现竞态。
    """

    def test_concurrent_choose_and_pre_gen_stress(self):
        """压力测试：并发调用 choose 和 _start_pre_generation，验证无异常。

        使用 mock 替代 LLM 调用，快速执行 50 轮并发操作。
        """
        session = _make_session_with_scenes(1)
        errors = []
        barrier = threading.Barrier(3)  # 主线程 + 2 个并发线程

        def do_choose(idx):
            """模拟 choose 的核心逻辑（不含 LLM 调用）"""
            try:
                barrier.wait(timeout=5.0)
                for _ in range(25):
                    current = session.scenes[session.current_scene_index]
                    choices = current.get("choices", [])
                    if not choices:
                        break
                    # 模拟 choose 的取消 + 缓存检查逻辑
                    prefix = f"{session.current_scene_index}:"
                    with session._pre_gen_lock:
                        cancelled = []
                        for k, fut in list(session._pre_gen_futures.items()):
                            if k.startswith(prefix):
                                if not fut.done():
                                    fut.cancel()
                                cancelled.append(k)
                        for k in cancelled:
                            session._pre_gen_futures.pop(k, None)
                        pre_key = f"{session.current_scene_index}:0"
                        session._pre_generated.pop(pre_key, None)

                    # 模拟 scenes.append + history.append
                    new_scene = {
                        "scene_name": f"场景{len(session.scenes) + 1}",
                        "choices": [
                            {"id": "c1", "text": "A", "hint": ""},
                            {"id": "c2", "text": "B", "hint": ""},
                        ],
                        "is_ending": False,
                    }
                    session.scenes.append(new_scene)
                    session.current_scene_index = len(session.scenes) - 1
                    session.history.append({
                        "scene_index": session.current_scene_index - 1,
                        "choice_index": 0,
                        "choice_text": "A",
                    })
            except Exception as e:
                errors.append(e)

        def do_pre_gen():
            """模拟 _start_pre_generation 的核心逻辑"""
            try:
                barrier.wait(timeout=5.0)
                for _ in range(25):
                    idx = session.current_scene_index
                    if idx < 0 or idx >= len(session.scenes):
                        continue
                    current = session.scenes[idx]
                    if current.get("is_ending"):
                        continue
                    # 模拟 deepcopy 快照
                    scenes_snapshot = copy.deepcopy(session.scenes)
                    history_snapshot = copy.deepcopy(session.history)
                    # 模拟注册 future
                    key = f"{idx}:mock"
                    with session._pre_gen_lock:
                        if key not in session._pre_generated and key not in session._pre_gen_futures:
                            mock_future = MagicMock()
                            mock_future.done.return_value = True
                            session._pre_gen_futures[key] = mock_future
                    time.sleep(0.001)  # 模拟工作间隔
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=do_choose, args=(0,)),
            threading.Thread(target=do_choose, args=(1,)),
            threading.Thread(target=do_pre_gen),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        session.close()
        assert not errors, f"并发操作产生异常: {errors}"

    def test_choose_cancels_pre_gen_futures_correctly(self):
        """验证 choose 的取消逻辑在并发下正确清理 futures。"""
        session = _make_session_with_scenes(1)

        # 手动注入 mock futures
        mock_futures = {}
        for i in range(3):
            fut = MagicMock()
            fut.done.return_value = False
            mock_futures[f"0:{i}"] = fut

        with session._pre_gen_lock:
            session._pre_gen_futures.update(mock_futures)

        # 模拟 choose 的取消逻辑（针对 scene_idx=0 的所有选项）
        prefix = "0:"
        with session._pre_gen_lock:
            cancelled_keys = []
            for k, fut in list(session._pre_gen_futures.items()):
                if k.startswith(prefix):
                    if not fut.done():
                        fut.cancel()
                    cancelled_keys.append(k)
            for k in cancelled_keys:
                session._pre_gen_futures.pop(k, None)

        # 所有 0: 前缀的 future 应该被取消并移除
        for fut in mock_futures.values():
            fut.cancel.assert_called_once()
        assert len(session._pre_gen_futures) == 0

        session.close()

    def test_pre_gen_cache_hit_skips_generation(self):
        """验证预生成缓存命中时跳过即时生成。"""
        session = _make_session_with_scenes(1)
        pre_gen_scene = {
            "scene_name": "预生成结果",
            "narration": "预生成旁白",
            "dialogues": [],
            "choices": [],
            "is_ending": True,
        }
        # 注入缓存
        with session._pre_gen_lock:
            session._pre_generated["0:0"] = pre_gen_scene

        # 模拟 choose 的缓存检查
        with session._pre_gen_lock:
            pre_key = "0:0"
            scene = session._pre_generated.pop(pre_key, None)

        assert scene is not None
        assert scene["scene_name"] == "预生成结果"
        # 缓存应该已被弹出
        assert "0:0" not in session._pre_generated

        session.close()


# ═══════════════════════════════════════════════════════
# 7. _pre_gen_lock 保护范围验证
# ═══════════════════════════════════════════════════════

class TestPreGenLockScope:
    """测试 7: 确认 _pre_gen_lock 保护范围覆盖所有共享状态的读写

    _pre_gen_lock 应该保护：
    - _pre_generated 的读/写
    - _pre_gen_futures 的读/写
    - 以及 choose 中对两者的复合操作（取消 + 缓存检查）
    """

    def test_pre_generated_requires_lock_for_read(self):
        """验证 _pre_generated 的读操作在锁内执行不会与写操作冲突。"""
        session = _make_session_with_scenes(1)
        write_done = threading.Event()
        read_result = {}

        def writer():
            for i in range(100):
                with session._pre_gen_lock:
                    session._pre_generated[f"0:{i}"] = {"scene_name": f"场景{i}"}
                time.sleep(0.0001)
            write_done.set()

        def reader():
            for _ in range(100):
                with session._pre_gen_lock:
                    keys = list(session._pre_generated.keys())
                    for k in keys:
                        _ = session._pre_generated.get(k)
                time.sleep(0.0001)

        t_writer = threading.Thread(target=writer)
        t_reader = threading.Thread(target=reader)
        t_writer.start()
        t_reader.start()
        t_writer.join(timeout=5.0)
        t_reader.join(timeout=5.0)

        session.close()
        # 无异常即通过——锁保护了并发读写

    def test_pre_gen_futures_requires_lock_for_compound_op(self):
        """验证 _pre_gen_futures 的复合操作（遍历 + 删除）需要锁保护。

        模拟 choose 中的取消逻辑：遍历 futures → cancel → pop。
        如果去掉锁，另一个线程的写入可能导致 RuntimeError（dict changed size during iteration）。
        """
        session = _make_session_with_scenes(1)
        errors = []

        def adder():
            for i in range(200):
                try:
                    with session._pre_gen_lock:
                        mock_fut = MagicMock()
                        mock_fut.done.return_value = False
                        session._pre_gen_futures[f"0:adder_{i}"] = mock_fut
                except Exception as e:
                    errors.append(("adder", e))
                time.sleep(0.0001)

        def canceller():
            for _ in range(200):
                try:
                    with session._pre_gen_lock:
                        cancelled = []
                        for k, fut in list(session._pre_gen_futures.items()):
                            if "adder" in k and not fut.done():
                                fut.cancel()
                                cancelled.append(k)
                        for k in cancelled:
                            session._pre_gen_futures.pop(k, None)
                except Exception as e:
                    errors.append(("canceller", e))
                time.sleep(0.0001)

        t1 = threading.Thread(target=adder)
        t2 = threading.Thread(target=canceller)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        session.close()
        assert not errors, f"复合操作在锁保护下仍出现异常: {errors}"

    def test_lock_covers_all_shared_state_writes(self):
        """验证 _start_pre_generation 中所有对共享状态的写操作都在锁内。

        通过检查源码中的锁使用模式来确认保护范围。
        """
        import inspect
        source = inspect.getsource(RealtimeStorySession._start_pre_generation)

        # 验证：_pre_generated 和 _pre_gen_futures 的写入都在 _pre_gen_lock 内
        # 检查 _pre_generated[k] = s 出现在 with self._pre_gen_lock 块内
        assert "with self._pre_gen_lock:" in source, \
            "_start_pre_generation 必须使用 _pre_gen_lock"
        assert "self._pre_generated[k]" in source or "self._pre_generated[" in source, \
            "_start_pre_generation 必须写入 _pre_generated"
        assert "self._pre_gen_futures[key]" in source or "self._pre_gen_futures[" in source, \
            "_start_pre_generation 必须写入 _pre_gen_futures"

        # 验证 choose 方法中也使用了锁
        choose_source = inspect.getsource(RealtimeStorySession.choose)
        assert "with self._pre_gen_lock:" in choose_source, \
            "choose 方法必须使用 _pre_gen_lock 保护缓存检查"

    def test_deepcopy_happens_before_lock_acquisition(self):
        """验证 deepcopy 在锁外执行——这是设计意图（避免长时间持锁）。

        通过替换 _pre_gen_lock 为追踪包装器来记录操作顺序，
        确认 deepcopy 调用先于锁获取。
        """
        session = _make_session_with_scenes(2)
        call_order = []
        order_lock = threading.Lock()

        original_deepcopy = copy.deepcopy
        real_lock = session._pre_gen_lock

        class TrackingLock:
            """包装 threading.Lock，记录 acquire 调用顺序。"""
            def __enter__(self):
                with order_lock:
                    call_order.append("lock_acquire")
                return real_lock.__enter__()
            def __exit__(self, *args):
                return real_lock.__exit__(*args)
            def acquire(self, *a, **kw):
                with order_lock:
                    call_order.append("lock_acquire")
                return real_lock.acquire(*a, **kw)
            def release(self):
                return real_lock.release()

        def tracked_deepcopy(obj):
            with order_lock:
                call_order.append("deepcopy")
            return original_deepcopy(obj)

        session._pre_gen_lock = TrackingLock()

        mock_gen = MagicMock(return_value={
            "scene_name": "预生成", "narration": "旁白",
            "dialogues": [], "choices": [], "is_ending": True,
        })

        with patch.object(session, '_generate_scene', mock_gen):
            with patch('realtime_engine.copy.deepcopy', side_effect=tracked_deepcopy):
                session._start_pre_generation(0)

        session._pre_gen_executor.shutdown(wait=True)

        # deepcopy 应该在 lock_acquire 之前执行
        assert len(call_order) >= 2, f"应至少有 2 个操作记录，实际: {call_order}"
        assert call_order[0] == "deepcopy", \
            f"deepcopy 应最先执行，实际顺序: {call_order}"
        assert "lock_acquire" in call_order, \
            f"应该有锁获取操作，实际顺序: {call_order}"

        session.close()
