# -*- coding: utf-8 -*-
"""
AI 实时剧本生成引擎 (Realtime Story Engine)

多智能体协作：
  Agent 1 - StoryPlanner: 一句话 → 故事种子（角色/世界观/剧情大纲/初始场景提示）
  Agent 2 - SceneGenerator: 上下文 + 玩家选择 → 单个完整场景（旁白/对话/选项/配图描述）

预测式生成：用户阅读当前场景时，后台预生成各选项的下一场景。
输出：故事结束后保存为 YAML 文件，兼容 GenericScriptAdapter 重复加载。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
import yaml
import queue as queue_mod
import concurrent.futures
from pathlib import Path
from typing import Generator
from datetime import date
from concurrent.futures import ThreadPoolExecutor

from llm_client import LLMClient, extract_json as _extract_json
from logger import get_logger, bind_context, clear_context
from image_gen import (
    build_character_prompt, build_background_prompt,
    slugify, DEFAULT_BG_SIZE, DEFAULT_CHAR_SIZE,
)

logger = get_logger(__name__)

# ── 项目路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # quillforge/
WORKSPACE_ROOT = PROJECT_ROOT.parent                    # QuillForge workspace root
SAMPLES_DIR = PROJECT_ROOT / "samples"

MAX_INPUT_LENGTH = 150
MAX_SCENES = 8  # 安全上限，防止无限生成


# ═══════════════════════════════════════════════════════
# LLM 客户端（使用统一模块）
# ═══════════════════════════════════════════════════════

def _build_realtime_client() -> LLMClient:
    """构建实时引擎专用 LLM 客户端。

    实时管线优先保障响应速度，必须禁用 thinking（与 Harness 管线一致），
    避免推理模型将 token 预算消耗在 reasoning_content 上导致 JSON 输出截断。
    """
    model = (
        os.environ.get("SCRIPT_GEN_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or "deepseek-chat"
    )
    return LLMClient(
        model=model,
        temperature=0.85,
        max_tokens=6000,
        timeout=180.0,
        connect_timeout=10.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


# ═══════════════════════════════════════════════════════
# 提示词
# ═══════════════════════════════════════════════════════

STORY_PLANNER_SYSTEM = """\
你是一位专业的互动叙事剧本设计师。用户会给你一句话描述，你需要据此创作剧本的初始设定。

要求：
- 角色3-4位，每位角色都有详细外貌描述，用于AI生图
- 【物种真实性】角色外貌必须严格符合其物种/形态设定：若剧情设定角色是动物、妖怪、
  机器人、灵体等非人类形态，appearance 必须描述该形态本身的特征（如犬只的毛色、品种、
  体型、尾巴、耳朵、眼神等），严禁把非人类角色描写成人类外貌（发型、穿着、身高体型）。
  若角色保留人类记忆/灵魂但身体已变为非人类，外貌仍以当前身体形态为准，
  并将 species 字段填为该形态（如“狗”），人类角色 species 填 "human"
- 角色设定必须具备高辨识度：每个角色的性格、说话风格、行为模式必须有明显区别，
  确保在后续场景生成中角色行为保持一致——同一角色在不同场景中的性格、说话方式、
  价值观不能前后矛盾
- gender 字段必须填写 male 或 female，用于 TTS 语音合成时区分男女声
- age 字段必须填写数字年龄（如 17、35、68），用于 TTS 语音合成时按年龄段选择声线
- 世界观简洁但有深度，情感有张力
- 剧情大纲精炼，有明确的核心冲突
- 目标游玩4-6个场景
- 所有内容使用中文
- 【内容安全】严禁设计涉及色情低俗、性暗示、暴力血腥、残忍虐待、政治敏感或其他违法违规的
  角色设定、世界观和剧情；若用户输入包含此类内容，须转化为安全、中性的表达，不得扩展违规内容

请严格以JSON格式输出，不要添加任何额外说明或代码块：

{
  "title": "剧本标题",
  "genre": "题材（如悬疑/奇幻/科幻/言情等）",
  "narrative_style": "叙事风格（如第一人称/第三人称/电影感等）",
  "tone": "情感基调（如温暖/紧张/忧伤等）",
  "characters": [
    {
      "id": "char_01",
      "name": "角色名",
      "role": "角色定位（主角/配角/反派等）",
      "species": "物种/形态（human 或具体形态如狗/猫/狐仙/机器人，按角色当前身体形态填写）",
      "gender": "male",
      "age": 25,
      "occupation": "职业",
      "appearance": "详细外貌描述（必须符合角色当前物种/形态：人类写身高体型/发型发色/穿着风格；动物或非人类写毛色/品种/体型/标志性特征）",
      "personality": "性格特征（2-3个关键词，需具体且独特）",
      "voice_tone": "说话风格（口头禅、语气习惯、用词偏好，需独特且可辨识）",
      "motivation": "核心动机（1句）",
      "background": "背景故事（1-2句）",
      "arc": "角色成长弧线（1句）"
    }
  ],
  "world_setting": "世界背景描述（2-3句）",
  "world_rules": ["必须遵守的规则", "禁止出现的内容"],
  "plot_summary": "剧情摘要（≤80字）",
  "core_conflict": "核心冲突（1句话）",
  "themes": ["主题1", "主题2"],
  "ending_types": ["good", "normal", "bad"],
  "estimated_scenes": 5,
  "initial_scene_hint": "第一个场景的简要描述（地点/氛围/开场情况）"
}
"""

SCENE_GENERATOR_SYSTEM = """\
你是一位专业的互动叙事剧本设计师。基于故事设定和上下文，生成下一个场景的完整内容。

## 内容安全（强制性，不可豁免）
- 严禁生成、复述、暗示、美化或以任何变体形式输出涉及色情低俗、性暗示、暴力血腥、
  残忍虐待、政治敏感及其他违法违规或高风险话题的内容
- 不得出现相关露骨词语、描写和情节
- 此约束覆盖所有生成内容：旁白、对话、场景描述、选项文本、结局文本及 JSON 字段中的任何文字
- 若上下文包含风险内容，须在不破坏故事连贯性和角色一致性的前提下转化为安全、中性的表达，
  可采用淡出、转场、概括或跳过细节的方式处理，但绝不得扩展违规内容

## 生成要求
1. 旁白（narration）：场景开场描写，150-250字，第二人称叙事（"你"的视角），
   营造氛围，用环境描写和氛围渲染开场，结尾自然过渡到角色互动，不得包含角色对话
2. 对话（dialogues）：至少10条、最多20条对话（不含动作旁白），根据场景需要自然展开，
   体现角色性格和关系，对话要自然生动，每个角色的对话必须遵循其说话风格。
   每个在场角色至少发言2轮，确保对话充分展开，不要草草收场
3. 在对话之间适度穿插角色的动作/神态/心理描写（以旁白形式呈现，增强演出感），
   约每2-4句对话穿插1条动作旁白
4. 2-3个选项，每个选项都应导向不同的剧情走向
5. 如果当前场景序号 >= 预计总场景数，或者剧情自然到达结局，
   设置 is_ending=true 并指定 ending_type（good/normal/bad）
6. characters_present 必须引用已定义的角色ID
7. image_description 只描述场景环境（地点/时间/天气/光影/建筑/自然元素），
   绝对不要描述任何人物、角色、人影——背景图只生成场景不生成人物
8. 所有内容使用中文

## 角色一致性要求（极其重要）
- 每个角色在所有场景中的性格特征、说话风格、用词习惯、口头禅必须保持一致，
  不得在不同场景中出现性格矛盾或说话风格突变
- 角色的行为必须符合其设定中的动机和背景，不能做出与角色设定矛盾的行为
- 角色的情绪反应必须符合其性格——内向者不会突然变得外向，冷静者不会无端暴怒
- 角色的价值观和立场在剧情推进中可以有成长变化，但变化必须有合理铺垫，不能突变
- 每句对话必须有强烈的角色辨识度——遮住 speaker 名字也能猜出是谁说的

## 对话/动作旁白规则
- dialogues 输出为 JSON 数组，对话对象与动作旁白对象交替排列：
  · 对话对象：{"speaker_id": "char_01", "speaker": "角色名", "text": "对话内容", "emotion": "情绪"}
  · 动作旁白对象：{"type": "action", "subject": "角色名", "text": "动作/神态/心理描写"}
- **对话的 text 字段必须是纯台词**——只包含角色嘴里说出的话，
  严禁在 text 中夹带任何动作、神态、心理描写或括号注释（如「（微微一怔）」「（转身离开）」）。
  动作/神态必须作为独立的 action 对象输出。
- 动作旁白用第三人称描写某个在场角色的动作、神态、肢体反应或心理活动
- 动作旁白不要加引号、不要写成角色说的话；心理活动也用叙述口吻（可用“心想、暗自、意识到”等）
- 每条动作旁白简洁，1句为宜
- emotion 只能是以下中文值之一：平静、急切、分析、果断、担忧、愤怒、希望、好奇、沉着、讽刺、恐惧、坚定
- 同一角色不得连续发言超过2轮
- 最后一轮必须留下悬念或决策点

请严格以JSON格式输出，不要添加任何额外说明或代码块：

{
  "scene_name": "场景名称",
  "location": "地点",
  "time": "时间（如黄昏/深夜/清晨）",
  "narration": "旁白文本（150-250字，第二人称）",
  "dialogues": [
    {"speaker_id": "char_01", "speaker": "角色名", "text": "对话内容", "emotion": "平静"},
    {"type": "action", "subject": "角色名", "text": "动作/神态/心理描写"},
    {"speaker_id": "char_02", "speaker": "角色名B", "text": "对话内容", "emotion": "急切"}
  ],
  "atmosphere": "氛围描述",
  "choices": [
    {"id": "choice_1", "text": "选项文本", "hint": "方向暗示"}
  ],
  "characters_present": ["char_01"],
  "is_ending": false,
  "ending_type": null,
  "image_description": "场景背景图描述（仅环境，不含人物）"
}
"""


# ═══════════════════════════════════════════════════════
# 核心会话类
# ═══════════════════════════════════════════════════════

class RealtimeStorySession:
    """AI 实时故事生成会话

    生命周期：
      1. initialize() → 生成故事种子 + 第一个场景
      2. choose()    → 玩家选择，返回下一场景（预生成命中或即时生成）
      3. 重复步骤 2 直到 is_ending=true
      4. save_to_yaml() → 保存为 YAML 文件，可用 GenericScriptAdapter 重新加载
    """

    def __init__(self, user_input: str):
        self.session_id: str = str(uuid.uuid4())[:8]
        self.user_input: str = user_input.strip()
        self.seed: dict = {}
        self.scenes: list[dict] = []
        self.current_scene_index: int = -1
        self.history: list[dict] = []  # [{scene_index, choice_index, choice_text}]
        self._client: LLMClient | None = None
        self._pre_gen_executor = ThreadPoolExecutor(max_workers=3)
        self._bg_executor = ThreadPoolExecutor(max_workers=10)  # bg/TTS 独立池，不抢占预生成
        self._pre_generated: dict[str, dict] = {}    # key: "scene_idx:choice_idx" → scene
        self._pre_gen_futures: dict[str, object] = {}  # key → Future
        self._pre_gen_lock = threading.Lock()  # 保护 _pre_generated / _pre_gen_futures 的复合操作
        self._save_dir: Path | None = None  # YAML 保存目录（复用，避免重复创建）
        # 增量写入标记：只有变化的部分才重写
        self._dirty: dict[str, bool] = {
            "meta": True, "worldbook": True, "characters": True,
            "relationships": True, "plot": True, "endings": True,
            "worldline": True, "scenes": True, "assets": True,
            "choices": True, "events": True,
        }

    # ── 资源释放 ──

    def _mark_scenes_dirty(self):
        """新场景加入后，标记所有与场景相关的文件为脏。"""
        for key in ("plot", "endings", "worldline", "scenes", "assets", "choices", "events"):
            self._dirty[key] = True

    def close(self):
        """关闭线程池，释放资源。会话过期时由 TTLCache 回调触发。"""
        try:
            self._pre_gen_executor.shutdown(wait=False)
        except Exception as e:
            logger.debug("[realtime] 预生成线程池关闭异常: %s", e)
        try:
            self._bg_executor.shutdown(wait=False)
        except Exception as e:
            logger.debug("[realtime] 后台线程池关闭异常: %s", e)

    # ── 客户端懒加载 ──

    def _ensure_client(self):
        if self._client is None:
            self._client = _build_realtime_client()

    # ── 公开流程 ──

    def initialize(self) -> Generator[dict, None, None]:
        """初始化故事，生成种子和第一个场景。Yields SSE 事件。"""
        bind_context(session_id=self.session_id)
        if not self.user_input:
            yield {"type": "error", "message": "请输入剧本描述"}
            return
        if len(self.user_input) > MAX_INPUT_LENGTH:
            yield {"type": "error", "message": f"描述不能超过{MAX_INPUT_LENGTH}字"}
            return

        self._ensure_client()

        # ── Agent 1: StoryPlanner（流式收集，避免无反馈等待）──
        yield {"type": "progress", "step": "planning", "message": "正在构思故事设定..."}

        seed = None
        try:
            chunks = []
            for chunk in self._client.generate_stream(
                STORY_PLANNER_SYSTEM,
                f"用户想要的剧本：{self.user_input}",
                max_tokens=6000, temperature=0.85,
            ):
                chunks.append(chunk)
            raw = "".join(chunks)
            parsed = _extract_json(raw)
            if parsed and parsed.get("characters") and parsed.get("title"):
                seed = parsed
            else:
                logger.warning("[realtime] 故事种子解析失败, raw[:200]=%s", raw[:200] if raw else "(empty)")
        except Exception as e:
            logger.error("[realtime] 故事种子生成异常: %s", e)

        if not seed:
            yield {"type": "error", "message": "故事设定生成失败，请重试"}
            return

        self.seed = seed
        title = seed.get("title", "未命名剧本")

        # 下发故事种子
        yield {
            "type": "story_seed",
            "session_id": self.session_id,
            "title": title,
            "genre": seed.get("genre", ""),
            "narrative_style": seed.get("narrative_style", ""),
            "tone": seed.get("tone", ""),
            "characters": seed.get("characters", []),
            "world_setting": seed.get("world_setting", ""),
            "plot_summary": seed.get("plot_summary", ""),
            "estimated_scenes": seed.get("estimated_scenes", 5),
        }

        # ── Agent 2: SceneGenerator（第一个场景，流式）──
        yield {"type": "progress", "step": "scene", "message": f"正在生成《{title}》的开场..."}

        scene = None
        for evt in self._generate_scene_stream(0, player_choice=""):
            if evt.get("type") == "scene_end":
                scene = evt  # scene_end 事件本身就是格式化后的场景数据
            yield evt  # 透传所有流式事件

        if not scene:
            return  # _generate_scene_stream 已 yield error

        self.scenes.append(scene)
        self.current_scene_index = 0
        self._mark_scenes_dirty()

        # 首场景生成后立即保存 YAML
        try:
            self.save_to_yaml()
        except Exception as e:
            logger.warning("[realtime] 首场景生成后 YAML 保存失败: %s", e)

        # 预生成下一批
        if not scene.get("is_ending"):
            self._start_pre_generation(0)

        yield {"type": "done", "session_id": self.session_id}

    def choose(self, choice_index: int) -> Generator[dict, None, None]:
        """玩家做出选择，返回下一场景。Yields SSE 事件。"""
        bind_context(session_id=self.session_id)
        if self.current_scene_index < 0 or self.current_scene_index >= len(self.scenes):
            yield {"type": "error", "message": "无当前场景"}
            return

        current = self.scenes[self.current_scene_index]
        choices = current.get("choices", [])
        if choice_index < 0 or choice_index >= len(choices):
            yield {"type": "error", "message": "无效的选项"}
            return

        chosen = choices[choice_index]
        choice_text = chosen.get("text", "")

        # 记录历史
        self.history.append({
            "scene_index": self.current_scene_index,
            "choice_index": choice_index,
            "choice_text": choice_text,
        })

        # ── 取消当前场景所有未完成的预生成 Future ──
        # 玩家已做出选择，其他分支的预生成不再需要
        prefix = f"{self.current_scene_index}:"
        with self._pre_gen_lock:
            cancelled_keys = []
            for k, fut in list(self._pre_gen_futures.items()):
                if k.startswith(prefix):
                    if not fut.done():
                        fut.cancel()
                    cancelled_keys.append(k)
            for k in cancelled_keys:
                self._pre_gen_futures.pop(k, None)

            # ── 检查预生成缓存（仅用已完成的结果，不阻塞等待）──
            pre_key = f"{self.current_scene_index}:{choice_index}"
            scene = self._pre_generated.pop(pre_key, None)
            # 清理同场景其他分支的预生成残留，避免缓存只增不减
            for k in [k for k in self._pre_generated if k.startswith(prefix)]:
                self._pre_generated.pop(k, None)
        scene_from_cache = scene is not None

        if scene is None:
            # 即时流式生成
            yield {"type": "progress", "step": "scene", "message": "正在生成下一场景..."}
            for evt in self._generate_scene_stream(
                self.current_scene_index + 1,
                player_choice=choice_text,
            ):
                if evt.get("type") == "scene_end":
                    scene = evt  # scene_end 事件本身就是格式化后的场景数据
                yield evt  # 透传流式事件

        if not scene:
            # 生成失败：回滚本次选择的历史记录，避免 history 与 scenes 错位
            if self.history and self.history[-1].get("scene_index") == self.current_scene_index:
                self.history.pop()
            return  # _generate_scene_stream 已 yield error

        self.scenes.append(scene)
        self.current_scene_index = len(self.scenes) - 1
        self._mark_scenes_dirty()

        # 如果是预生成命中，直接发送完整场景事件
        if scene_from_cache:
            yield self._format_scene_event(scene, self.current_scene_index)

        # 每个新场景生成后增量保存 YAML
        try:
            self.save_to_yaml()
        except Exception as e:
            logger.warning("[realtime] 场景生成后 YAML 增量保存失败: %s", e)

        # 预生成下一批或保存
        if not scene.get("is_ending") and self.current_scene_index < MAX_SCENES - 1:
            self._start_pre_generation(self.current_scene_index)
        else:
            # 故事结束 → 最终保存 YAML
            yield {"type": "progress", "step": "saving", "message": "正在保存剧本..."}
            saved_path = self.save_to_yaml()
            yield {
                "type": "story_complete",
                "ending_type": scene.get("ending_type", "normal"),
                "saved_path": str(saved_path) if saved_path else "",
            }

        yield {"type": "done", "session_id": self.session_id}

    # ── 内部方法 ──

    def _build_scene_prompt(self, scene_idx: int, player_choice: str,
                            scenes_snapshot: list[dict] | None = None,
                            history_snapshot: list[dict] | None = None) -> str:
        """构建场景生成的 user prompt（供流式/非流式复用）

        Args:
            scenes_snapshot: 场景列表快照（预生成时传入，避免读取被主线程修改的 self.scenes）
            history_snapshot: 历史记录快照（预生成时传入，避免读取被主线程修改的 self.history）
        """
        scenes = scenes_snapshot if scenes_snapshot is not None else self.scenes
        history = history_snapshot if history_snapshot is not None else self.history
        estimated = self.seed.get("estimated_scenes", 5)
        scene_number = scene_idx + 1

        chars_text = "\n".join([
            f"- {c.get('id', '')} ({c.get('name', '')})：{c.get('role', '')}，"
            f"性格：{c.get('personality', '')}，说话风格：{c.get('voice_tone', '')}，"
            f"动机：{c.get('motivation', '')}，背景：{c.get('background', '')}"
            for c in self.seed.get("characters", [])
        ])

        if scene_idx == 0:
            context_section = f"【初始场景提示】\n{self.seed.get('initial_scene_hint', '')}"
        else:
            history_parts = []
            for i, s in enumerate(scenes):
                is_last = (i == len(scenes) - 1)
                narration = s.get("narration", "")
                chosen_text = ""
                if i < len(history):
                    ci = history[i]["choice_index"]
                    opts = s.get("choices", [])
                    if 0 <= ci < len(opts):
                        chosen_text = opts[ci].get("text", "")

                if is_last:
                    # 最近一个场景：传递完整旁白 + 对话摘要，确保下文严格衔接
                    dlg_lines = []
                    for d in s.get("dialogues", []):
                        if isinstance(d, dict):
                            if d.get("type") == "action":
                                dlg_lines.append(f"  （{d.get('subject', '')}{d.get('text', '')}）")
                            else:
                                dlg_lines.append(f"  {d.get('speaker', '')}：{d.get('text', '')}")
                    # 最多保留最后 12 条对话/动作，避免 prompt 过长
                    if len(dlg_lines) > 12:
                        dlg_lines = dlg_lines[-12:]
                    dlg_text = "\n".join(dlg_lines)
                    history_parts.append(
                        f"场景{i + 1}「{s.get('scene_name', '')}」（上一场景，必须严格衔接）：\n"
                        f"  旁白：{narration}\n"
                        f"  对话：\n{dlg_text}\n"
                        f"  玩家选择：{chosen_text}"
                    )
                else:
                    # 更早的场景：简要摘要
                    history_parts.append(
                        f"场景{i + 1}「{s.get('scene_name', '')}」：{narration[:150]}\n"
                        f"  玩家选择：{chosen_text}"
                    )
            context_section = (
                "【已发生的故事】\n" + "\n".join(history_parts) + "\n\n"
                "【衔接要求】\n"
                "- 新场景必须严格承接上一场景的结尾状态：角色的情绪、立场、未解决的悬念必须延续\n"
                "- 玩家的选择必须在新场景开头产生可见的即时后果\n"
                "- 不得忽略上一场景对话中已确立的事实、承诺或冲突"
            )

        return (
            f"【故事设定】\n"
            f"- 标题：{self.seed.get('title', '')}\n"
            f"- 题材：{self.seed.get('genre', '')}\n"
            f"- 基调：{self.seed.get('tone', '')}\n"
            f"- 世界观：{self.seed.get('world_setting', '')}\n"
            f"- 规则：{'；'.join(self.seed.get('world_rules', []))}\n"
            f"- 核心冲突：{self.seed.get('core_conflict', '')}\n\n"
            f"【角色列表】\n{chars_text}\n\n"
            f"{context_section}\n\n"
            f"【当前任务】\n"
            f"- 正在生成第 {scene_number} 个场景（预计共 {estimated} 个场景）\n"
            f"- 玩家选择：{player_choice or '（故事开始，无前置选择）'}\n"
        )

    def _postprocess_scene(self, scene: dict, scene_idx: int) -> dict:
        """补全场景字段"""
        scene.setdefault("scene_id", f"scene_{scene_idx + 1:02d}")
        scene["scene_index"] = scene_idx
        # 达到场景上限时强制结局，防止玩家无限继续生成
        if scene_idx >= MAX_SCENES - 1:
            scene["is_ending"] = True
        if not scene.get("is_ending") and not scene.get("choices"):
            scene["choices"] = [
                {"id": "choice_1", "text": "继续探索", "hint": "继续前进"},
            ]
        if scene.get("is_ending"):
            scene["choices"] = []
        return scene

    def _generate_scene(self, scene_idx: int, player_choice: str = "",
                         scenes_snapshot: list[dict] | None = None,
                         history_snapshot: list[dict] | None = None) -> dict | None:
        """调用 SceneGenerator Agent 生成单个场景（非流式，用于预生成）。

        Args:
            scenes_snapshot: 场景列表快照（预生成时传入）
            history_snapshot: 历史记录快照（预生成时传入）
        """
        self._ensure_client()
        user_prompt = self._build_scene_prompt(
            scene_idx, player_choice,
            scenes_snapshot=scenes_snapshot,
            history_snapshot=history_snapshot,
        )

        try:
            raw = self._client.generate(
                SCENE_GENERATOR_SYSTEM,
                user_prompt,
                max_tokens=8000, temperature=0.8,
            )
            scene = _extract_json(raw)
            if scene and scene.get("narration"):
                return self._postprocess_scene(scene, scene_idx)
        except Exception as e:
            logger.warning("[realtime] 预生成场景 %d 失败: %s", scene_idx, e)
        return None

    # ── 流式场景生成 ──

    def _generate_scene_stream(self, scene_idx: int, player_choice: str = "") -> Generator[dict, None, None]:
        """流式生成场景，yield SSE 事件字典。

        事件序列：
          scene_start  → 场景元信息（scene_name/location/time）
          narration_delta → 旁白增量文本（多次，逐段推送）
          narration → 旁白完整文本（标记流式结束）
          dialogue → 逐条对话/动作旁白
          scene_end → 完整场景数据（含 choices/is_ending）
        失败时 yield {"type": "error", ...}。
        注意：流式模式不做重试（事件已推送无法收回）。
        """
        self._ensure_client()
        user_prompt = self._build_scene_prompt(scene_idx, player_choice)

        buf = ""
        scene_start_sent = False
        # 旁白流式状态
        narr_start_pos = -1   # narration 值的起始位置（开引号后）
        narr_sent_len = 0     # 已发送的解码字符数
        narr_complete = False
        narr_full_text = ""   # 完整旁白文本
        # 对话增量解析状态
        dlg_pos = -1
        dlg_depth = 0
        dlg_in_str = False
        dlg_esc = False
        dlg_obj_start = -1
        dlg_arr_found = False
        dlg_done = False
        dlg_early_sent = False  # 当前对象是否已提前推送

        try:
            for chunk in self._client.generate_stream(
                SCENE_GENERATOR_SYSTEM,
                user_prompt,
                max_tokens=8000, temperature=0.8,
            ):
                buf += chunk

                # 1) 场景元信息
                if not scene_start_sent:
                    sname = self._extract_field_str(buf, "scene_name")
                    if sname:
                        loc = self._extract_field_str(buf, "location") or ""
                        tm = self._extract_field_str(buf, "time") or ""
                        yield {"type": "scene_start", "scene_index": scene_idx,
                               "scene_name": sname, "location": loc, "time": tm}
                        scene_start_sent = True

                # 2) 旁白逐段流式推送
                if not narr_complete:
                    # 定位 narration 值的起始
                    if narr_start_pos < 0:
                        key = '"narration"'
                        kidx = buf.find(key)
                        if kidx != -1:
                            colon = buf.find(':', kidx + len(key))
                            if colon != -1:
                                q = buf.find('"', colon + 1)
                                if q != -1:
                                    narr_start_pos = q + 1
                    # 提取可用的部分旁白
                    if narr_start_pos >= 0:
                        partial, complete = self._extract_partial_narration(buf, narr_start_pos)
                        if partial and len(partial) > narr_sent_len:
                            delta = partial[narr_sent_len:]
                            narr_sent_len = len(partial)
                            yield {"type": "narration_delta", "delta": delta}
                        if complete:
                            narr_complete = True
                            narr_full_text = partial
                            yield {"type": "narration", "text": narr_full_text}

                # 3) 对话逐条推送
                if not dlg_arr_found and not dlg_done and '"dialogues"' in buf:
                    arr_idx = buf.find('[', buf.find('"dialogues"'))
                    if arr_idx != -1:
                        dlg_arr_found = True
                        dlg_pos = arr_idx + 1

                if dlg_arr_found and dlg_pos >= 0:
                    while dlg_pos < len(buf):
                        ch = buf[dlg_pos]
                        if dlg_in_str:
                            if dlg_esc:
                                dlg_esc = False
                            elif ch == '\\':
                                dlg_esc = True
                            elif ch == '"':
                                dlg_in_str = False
                                # 字符串结束 → 尝试提前提取对话（text 字段完成即推送）
                                if dlg_depth > 0 and dlg_obj_start >= 0 and not dlg_early_sent:
                                    partial_obj = buf[dlg_obj_start:dlg_pos + 1]
                                    early = self._extract_dialogue_early(partial_obj)
                                    if early:
                                        yield {"type": "dialogue", "item": early}
                                        dlg_early_sent = True
                        else:
                            if ch == '"':
                                dlg_in_str = True
                            elif ch == '{':
                                if dlg_depth == 0:
                                    dlg_obj_start = dlg_pos
                                    dlg_early_sent = False
                                dlg_depth += 1
                            elif ch == '}':
                                if dlg_depth > 0:
                                    dlg_depth -= 1
                                    if dlg_depth == 0 and dlg_obj_start != -1:
                                        if not dlg_early_sent:
                                            # 未提前推送，解析完整对象
                                            try:
                                                item = json.loads(buf[dlg_obj_start:dlg_pos + 1])
                                                yield {"type": "dialogue", "item": item}
                                            except json.JSONDecodeError as e:
                                                logger.debug("[realtime] 流式对话 JSON 解析失败 (pos=%d): %s", dlg_obj_start, e)
                                        dlg_obj_start = -1
                                        dlg_early_sent = False
                            elif ch == ']' and dlg_depth == 0:
                                dlg_arr_found = False
                                dlg_done = True
                                break
                        dlg_pos += 1

        except Exception as e:
            logger.warning("[realtime] 流式生成中断: %s", e)

        # 解析完整 JSON
        scene = _extract_json(buf)
        if not scene or not scene.get("narration"):
            yield {"type": "error", "message": "场景生成失败，请重试"}
            return

        scene = self._postprocess_scene(scene, scene_idx)
        formatted = self._format_scene_event(scene, scene_idx)
        formatted["type"] = "scene_end"
        yield formatted

    # ── 流式 JSON 解析工具 ──

    @staticmethod
    def _decode_json_escapes(s: str) -> str:
        r"""手动解码 JSON 字符串中的转义序列（\n/\t/\uXXXX 等）。

        用于 json.loads 失败时的安全回退，避免返回原始转义文本。
        """
        result = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                nxt = s[i + 1]
                if nxt == 'n':
                    result.append('\n')
                elif nxt == 't':
                    result.append('\t')
                elif nxt == '"':
                    result.append('"')
                elif nxt == '\\':
                    result.append('\\')
                elif nxt == '/':
                    result.append('/')
                elif nxt == 'b':
                    result.append('\b')
                elif nxt == 'f':
                    result.append('\f')
                elif nxt == 'r':
                    result.append('\r')
                elif nxt == 'u' and i + 5 < len(s):
                    try:
                        result.append(chr(int(s[i + 2:i + 6], 16)))
                        i += 6
                        continue
                    except ValueError:
                        result.append(nxt)
                else:
                    result.append(nxt)
                i += 2
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    @staticmethod
    def _extract_field_str(buf: str, field: str) -> str | None:
        """从部分 JSON 中提取简单字符串字段的值（要求字段已完整）"""
        pattern = rf'"{field}"\s*:\s*"'
        match = re.search(pattern, buf)
        if not match:
            return None
        start = match.end()
        i = start
        while i < len(buf):
            if buf[i] == '\\':
                i += 2
                continue
            if buf[i] == '"':
                raw = buf[start:i]
                try:
                    return json.loads(f'"{raw}"')
                except Exception as e:
                    # json.loads 失败时，手动解码转义序列（\n/\uXXXX 等）
                    logger.debug("[realtime] _extract_field_str JSON 解码回退: %s", e)
                    return RealtimeStorySession._decode_json_escapes(raw)
            i += 1
        return None  # 字段尚未完整

    @staticmethod
    def _extract_narration_stream(buf: str) -> str | None:
        """从部分 JSON 中提取 narration 字段（仅当字符串完整时返回）"""
        key = '"narration"'
        idx = buf.find(key)
        if idx == -1:
            return None
        colon = buf.find(':', idx + len(key))
        if colon == -1:
            return None
        q_start = buf.find('"', colon + 1)
        if q_start == -1:
            return None
        # 扫描到未转义的关闭引号
        i = q_start + 1
        while i < len(buf):
            if buf[i] == '\\':
                i += 2
                continue
            if buf[i] == '"':
                raw = buf[q_start + 1:i]
                try:
                    return json.loads(f'"{raw}"')
                except Exception as e:
                    logger.debug("[realtime] _extract_narration_stream JSON 解码回退: %s", e)
                    return raw.replace('\\n', '\n').replace('\\"', '"')
            i += 1
        return None  # 尚未完整

    @staticmethod
    def _extract_partial_narration(buf: str, start: int) -> tuple[str, bool]:
        """从 buf[start:] 提取部分 narration 字符串值。

        返回 (decoded_text_so_far, is_complete)。
        即使字符串未关闭，也返回已解码的部分文本。
        """
        result = []
        i = start
        while i < len(buf):
            ch = buf[i]
            if ch == '\\':
                if i + 1 >= len(buf):
                    break  # 转义序列不完整，等待更多数据
                nxt = buf[i + 1]
                if nxt == 'n':
                    result.append('\n')
                elif nxt == 't':
                    result.append('\t')
                elif nxt == '"':
                    result.append('"')
                elif nxt == '\\':
                    result.append('\\')
                elif nxt == '/':
                    result.append('/')
                elif nxt == 'u':
                    if i + 6 <= len(buf):
                        try:
                            result.append(chr(int(buf[i + 2:i + 6], 16)))
                            i += 6
                            continue
                        except (ValueError, IndexError):
                            break  # hex 无效，等待下一个 chunk 补全
                    else:
                        break  # unicode 转义不完整
                else:
                    result.append(nxt)
                i += 2
                continue
            if ch == '"':
                return ''.join(result), True  # 字符串结束
            result.append(ch)
            i += 1
        return ''.join(result), False  # 尚未结束

    @staticmethod
    def _extract_dialogue_early(partial_obj: str) -> dict | None:
        """从部分对话对象中提前提取 speaker/text（text 字段完成即返回，无需等待整个对象关闭）。

        支持两种格式：
          对话: {"speaker_id": "...", "speaker": "...", "text": "...", ...}
          动作: {"type": "action", "subject": "...", "text": "...", ...}
        """
        # 检查是否为动作旁白
        type_match = re.search(r'"type"\s*:\s*"action"', partial_obj)
        if type_match:
            subject = RealtimeStorySession._extract_field_str(partial_obj, "subject")
            text = RealtimeStorySession._extract_field_str(partial_obj, "text")
            if text:
                return {"type": "action", "subject": subject or "", "text": text}
            return None
        # 对话对象：需要 speaker 和 text
        text = RealtimeStorySession._extract_field_str(partial_obj, "text")
        if not text:
            return None
        speaker = RealtimeStorySession._extract_field_str(partial_obj, "speaker")
        speaker_id = RealtimeStorySession._extract_field_str(partial_obj, "speaker_id")
        emotion = RealtimeStorySession._extract_field_str(partial_obj, "emotion")
        item = {"speaker": speaker or "", "text": text}
        if speaker_id:
            item["speaker_id"] = speaker_id
        if emotion:
            item["emotion"] = emotion
        return item

    @staticmethod
    def _iter_dialogues(buf: str):
        """增量解析 dialogues 数组中的对象，逐个 yield。
        生成器内部维护游标，每次被调用时从上次停止处继续扫描。"""
        key = '"dialogues"'
        idx = buf.find(key)
        if idx == -1:
            return
        arr_start = buf.find('[', idx)
        if arr_start == -1:
            return
        pos = arr_start + 1
        depth = 0
        in_str = False
        esc = False
        obj_start = -1
        while pos < len(buf):
            ch = buf[pos]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    if depth == 0:
                        obj_start = pos
                    depth += 1
                elif ch == '}':
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and obj_start != -1:
                            try:
                                yield json.loads(buf[obj_start:pos + 1])
                            except json.JSONDecodeError as e:
                                logger.debug("[realtime] _iter_dialogues JSON 解析失败 (pos=%d): %s", obj_start, e)
                            obj_start = -1
                elif ch == ']' and depth == 0:
                    return
            pos += 1

    def _start_pre_generation(self, scene_idx: int):
        """后台预生成当前场景各选项对应的下一场景。"""
        if scene_idx < 0 or scene_idx >= len(self.scenes):
            return
        current = self.scenes[scene_idx]
        if current.get("is_ending"):
            return

        # 快照当前 scenes 和 history，防止后台线程读取被主线程修改的数据
        scenes_snapshot = copy.deepcopy(self.scenes)
        history_snapshot = copy.deepcopy(self.history)

        choices = current.get("choices", [])
        for i, _choice in enumerate(choices):
            key = f"{scene_idx}:{i}"
            with self._pre_gen_lock:
                if key in self._pre_generated or key in self._pre_gen_futures:
                    continue

            choice_text = _choice.get("text", "")
            next_idx = scene_idx + 1

            def _do_pre_gen(k, idx, ct, sc_snap, hist_snap):
                # 后台线程绑定 session_id，确保预生成日志可关联会话
                bind_context(session_id=self.session_id)
                try:
                    s = self._generate_scene(
                        idx, player_choice=ct,
                        scenes_snapshot=sc_snap,
                        history_snapshot=hist_snap,
                    )
                    if s:
                        with self._pre_gen_lock:
                            # 守卫：玩家已离开该场景时丢弃结果，避免写入永远不会被消费的缓存
                            key_scene = int(k.split(":")[0]) if ":" in k else -1
                            if key_scene == self.current_scene_index:
                                self._pre_generated[k] = s
                    return s
                except Exception as e:
                    logger.warning("[realtime] 后台预生成 key=%s 失败: %s", k, e)
                    return None
                finally:
                    clear_context()

            try:
                future = self._pre_gen_executor.submit(
                    _do_pre_gen, key, next_idx, choice_text,
                    scenes_snapshot, history_snapshot,
                )
            except RuntimeError as e:
                # executor 已关闭（如会话临近过期被 close），预生成失败不影响主流程
                logger.warning("[realtime] 预生成任务提交失败（executor 已关闭）: %s", e)
                break
            with self._pre_gen_lock:
                self._pre_gen_futures[key] = future

    def _format_scene_event(self, scene: dict, scene_idx: int) -> dict:
        """格式化场景为 SSE 事件 dict。
        对 speaker 字段做归一化：用 seed 中的角色名替换 LLM 输出的名字，
        确保前端 vnAssets.characters[name] 能匹配到立绘 URL。
        """
        # ── 构建角色名映射 ──
        char_map: dict[str, str] = {}  # id → name, lowercased_name → name
        for c in self.seed.get("characters", []):
            cid = str(c.get("id", "")).strip()
            cname = str(c.get("name", "")).strip()
            if cid:
                char_map[cid] = cname
            if cname:
                char_map[cname.lower()] = cname

        # ── 归一化 dialogues 中的 speaker / subject ──
        dialogues = scene.get("dialogues", [])
        for d in dialogues:
            if d.get("type") == "action":
                subject = str(d.get("subject", "")).strip()
                if subject:
                    d["subject"] = char_map.get(subject.lower(),
                                                  char_map.get(subject, subject))
                continue
            speaker_id = str(d.get("speaker_id", "")).strip()
            speaker = str(d.get("speaker", "")).strip()
            # 优先用 speaker_id 查找标准角色名
            if speaker_id and speaker_id in char_map:
                d["speaker"] = char_map[speaker_id]
            elif speaker:
                d["speaker"] = char_map.get(speaker.lower(),
                                             char_map.get(speaker, speaker))

        return {
            "type": "scene",
            "scene_index": scene_idx,
            "scene_id": scene.get("scene_id", f"scene_{scene_idx + 1:02d}"),
            "scene_name": scene.get("scene_name", f"场景{scene_idx + 1}"),
            "location": scene.get("location", ""),
            "time": scene.get("time", ""),
            "atmosphere": scene.get("atmosphere", ""),
            "narration": scene.get("narration", ""),
            "dialogues": dialogues,
            "choices": scene.get("choices", []),
            "characters_present": scene.get("characters_present", []),
            "is_ending": scene.get("is_ending", False),
            "ending_type": scene.get("ending_type"),
            "image_description": scene.get("image_description", ""),
        }

    # ── 实时素材生成（引擎层） ──

    def _start_char_gen_bg(self, image_gen):
        """在后台并行生成所有角色立绘（fire-and-forget）。"""
        if not image_gen.enabled:
            return
        script_slug = slugify(self.seed.get("title", "realtime"))
        characters = self.seed.get("characters", [])
        for c in characters:
            name = str(c.get("name", "")).strip()
            cid = str(c.get("id", "") or name)
            if not name:
                continue
            prompt = build_character_prompt(c)
            self._bg_executor.submit(
                image_gen.generate, script_slug, "char", cid, prompt, DEFAULT_CHAR_SIZE
            )

    def _realtime_bg_event(self, scene_event: dict, image_gen) -> str | None:
        """为实时场景生成背景图，返回 asset_bg_ready SSE 事件字符串（失败返回 None）。"""
        script_slug = slugify(self.seed.get("title", "realtime"))
        scene_id = scene_event.get("scene_id", f"scene_{scene_event.get('scene_index', 0)+1:02d}")
        scene_name = scene_event.get("scene_name", scene_id)
        bg_data = {
            "name": scene_name,
            "location": scene_event.get("location", ""),
            "atmosphere": scene_event.get("atmosphere", "") or scene_event.get("time", ""),
            "details": scene_event.get("image_description", ""),
        }
        prompt = build_background_prompt(bg_data)
        try:
            url = image_gen.generate(script_slug, "bg", scene_id, prompt, DEFAULT_BG_SIZE)
            if url:
                evt = {"event": "asset_bg_ready", "scene_id": scene_id, "scene_name": scene_name, "url": url}
                return f"event: asset_bg_ready\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning("[realtime] 背景图生成失败 (scene=%s): %s", scene_id, e)
        return None

    def _realtime_tts_events(self, scene_event: dict, tts_gen) -> list[str]:
        """为实时场景中的对话行并行生成 TTS 事件（SSE 字符串列表）。
        用于预生成缓存命中（scene 事件）的一次性场景。
        """
        if not tts_gen.enabled:
            return []
        script_slug = slugify(self.seed.get("title", "realtime"))
        characters = self.seed.get("characters", [])
        tasks = []
        queue_idx = 1 if scene_event.get("narration") else 0
        for d in scene_event.get("dialogues", []):
            if d.get("type") == "action":
                queue_idx += 1
                continue
            speaker = d.get("speaker", "") or d.get("speaker_name", "")
            text = d.get("text", "")
            if speaker and text:
                voice_id = tts_gen.get_voice_id(speaker, characters)
                tasks.append((speaker, text, voice_id, queue_idx))
            queue_idx += 1
        if not tasks:
            return []
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as pool:
            future_map = {
                pool.submit(tts_gen.generate_to_file, script_slug, sp, txt, vid): (sp, qi)
                for sp, txt, vid, qi in tasks
            }
            try:
                for fut in concurrent.futures.as_completed(future_map, timeout=90):
                    sp, qi = future_map[fut]
                    try:
                        tts_url = fut.result()
                        if tts_url:
                            evt = {"event": "tts", "speaker": sp, "url": tts_url, "lineIdx": qi}
                            results.append(f"event: tts\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n")
                    except Exception as e:
                        logger.warning("[realtime] TTS future 结果获取失败: %s", e)
            except concurrent.futures.TimeoutError:
                not_done = [f for f in future_map if not f.done()]
                logger.warning("[_realtime_tts_events] %d TTS tasks timed out", len(not_done))
        return results

    def stream_realtime_sse(self, event_gen, image_gen=None, tts_gen=None, on_story_seed=None):
        """Queue-based SSE generator: scene events + TTS results interleaved in real-time.

        A background producer thread iterates *event_gen* (session.initialize / session.choose)
        and pushes scene events into a queue.  Each dialogue event immediately spawns a TTS
        task whose callback pushes the TTS SSE into the same queue the moment it completes.
        The SSE generator simply drains the queue, so TTS events reach the frontend as soon
        as they are ready — no longer batched after scene_end.

        Yields pre-formatted SSE strings (ready for StreamingResponse).
        """
        q: queue_mod.Queue = queue_mod.Queue()
        tts_enabled = tts_gen.enabled if tts_gen else False
        _sentinel = object()

        def _tts_task(sp, txt, vid, qi, slug):
            try:
                url = tts_gen.generate_to_file(slug, sp, txt, vid)
                if url:
                    evt = {"event": "tts", "speaker": sp, "url": url, "lineIdx": qi}
                    q.put(f"event: tts\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n")
            except Exception as e:
                logger.warning("[realtime] TTS 任务失败 (speaker=%s): %s", sp, e)

        def _bg_task(ev):
            try:
                sse = self._realtime_bg_event(ev, image_gen)
                if sse:
                    q.put(sse)
            except Exception as e:
                logger.warning("[realtime] 背景图推送任务失败: %s", e)

        def _batch_tts_task(scene_event):
            try:
                for sse in self._realtime_tts_events(scene_event, tts_gen):
                    q.put(sse)
            except Exception as e:
                logger.warning("[realtime] 批量 TTS 推送任务失败: %s", e)

        def producer():
            queue_idx = 0
            tts_futures = []
            try:
                for event in event_gen:
                    evt_type = event.get("type", "progress")

                    if evt_type == "story_seed":
                        if on_story_seed:
                            on_story_seed()
                        q.put((evt_type, event))
                        continue

                    if evt_type == "scene_start":
                        if image_gen:
                            self._bg_executor.submit(_bg_task, event)
                        q.put((evt_type, event))
                        continue

                    if evt_type == "narration":
                        queue_idx = 1
                        q.put((evt_type, event))
                        continue

                    if evt_type == "dialogue":
                        item = event.get("item", {})
                        if item.get("type") == "action":
                            queue_idx += 1
                        else:
                            if tts_enabled:
                                speaker = item.get("speaker", "") or item.get("speaker_name", "")
                                text = item.get("text", "")
                                if speaker and text:
                                    chars = self.seed.get("characters", [])
                                    slug = slugify(self.seed.get("title", "realtime"))
                                    vid = tts_gen.get_voice_id(speaker, chars)
                                    fut = self._bg_executor.submit(_tts_task, speaker, text, vid, queue_idx, slug)
                                    tts_futures.append(fut)
                            queue_idx += 1
                        q.put((evt_type, event))
                        continue

                    if evt_type in ("scene_end", "scene"):
                        q.put((evt_type, event))
                        if evt_type == "scene":
                            if image_gen:
                                self._bg_executor.submit(_bg_task, event)
                            if tts_enabled:
                                fut = self._bg_executor.submit(_batch_tts_task, event)
                                tts_futures.append(fut)
                        continue

                    q.put((evt_type, event))
            except Exception as e:
                logger.error("[realtime_sse] producer error: %s", e)
            finally:
                if tts_futures:
                    done, not_done = concurrent.futures.wait(tts_futures, timeout=90)
                    if not_done:
                        logger.warning("%d TTS tasks timed out", len(not_done))
                q.put(_sentinel)

        t = threading.Thread(target=producer, daemon=True)
        t.start()

        while True:
            try:
                item = q.get(timeout=180)
            except queue_mod.Empty:
                # 超时不能静默断流，必须告知前端，否则 UI 会永久卡死在等待态
                err_payload = json.dumps(
                    {"type": "error", "message": "生成超时，请重试"}, ensure_ascii=False
                )
                yield f"event: error\ndata: {err_payload}\n\n"
                break
            if item is _sentinel:
                break
            if isinstance(item, str):
                yield item
            else:
                evt_type, event = item
                payload = json.dumps(event, ensure_ascii=False)
                yield f"event: {evt_type}\ndata: {payload}\n\n"

        t.join(timeout=5)

    def stream_assets_sse(self, image_gen, tts_gen=None, image_concurrency: int = 7):
        """预生成角色立绘和场景背景，yield SSE 事件 dict。

        包含 assets_meta / asset_progress / assets_ready / assets_done 事件。
        """
        script_slug = slugify(self.seed.get("title", "realtime"))
        characters = self.seed.get("characters", [])
        scenes = self.scenes or []

        char_urls: dict[str, str] = {}
        scene_urls: dict[str, str] = {}

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
        yield {"event": "assets_meta", "enabled": image_gen.enabled, "total": total,
               "phase1_total": total,
               "message": (f"正在生成角色立绘和场景背景（共 {total} 张）" if image_gen.enabled
                           else "加载美术素材（缓存模式）")}

        done_count = 0
        if all_tasks:
            max_workers = min(len(all_tasks), image_concurrency)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(image_gen.generate, script_slug, t["cache_kind"],
                                    t["cache_id"], t["prompt"], t["size"]): t
                    for t in all_tasks
                }
                try:
                    for fut in concurrent.futures.as_completed(future_map, timeout=180):
                        t = future_map[fut]
                        try:
                            url = fut.result()
                        except Exception as e:
                            logger.warning("[realtime_assets] 图片生成任务失败: %s", e)
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
                    logger.warning("[stream_assets_sse] 超时(180s)，跳过未完成任务")

        yield {"event": "assets_ready", "enabled": image_gen.enabled,
               "characters": dict(char_urls), "scenes": dict(scene_urls),
               "remaining": 0}
        yield {"event": "assets_done", "enabled": image_gen.enabled,
               "characters": char_urls, "scenes": scene_urls}

    # ── 状态查询 ──

    def get_state(self) -> dict:
        if self.current_scene_index < 0:
            return {"state": "idle", "session_id": self.session_id}

        current = (self.scenes[self.current_scene_index]
                   if self.current_scene_index < len(self.scenes) else {})
        is_ending = current.get("is_ending", False)

        return {
            "state": "finished" if is_ending else "waiting_choice",
            "session_id": self.session_id,
            "title": self.seed.get("title", ""),
            "current_scene_index": self.current_scene_index,
            "total_scenes": len(self.scenes),
            "scene_name": current.get("scene_name", ""),
            "is_ending": is_ending,
            "choices": current.get("choices", []),
            "characters": self.seed.get("characters", []),
        }

    # ── YAML 保存 ──

    def save_to_yaml(self) -> Path | None:
        """将完整故事保存为 YAML 文件包，兼容 GenericScriptAdapter 重复加载。"""
        if not self.seed or not self.scenes:
            return None

        title = self.seed.get("title", "未命名剧本")
        safe_title = re.sub(r'[<>:"/\\|?*]', '', title).strip() or "未命名剧本"
        # 复用已创建的保存目录，避免每次保存都生成新目录
        if self._save_dir:
            script_dir = self._save_dir
        else:
            script_dir = SAMPLES_DIR / safe_title
            if script_dir.exists():
                script_dir = SAMPLES_DIR / f"{safe_title}_{self.session_id}"
            self._save_dir = script_dir

        script_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("角色", "剧情", "剧情/事件", "场景", "素材"):
            (script_dir / sub).mkdir(exist_ok=True)

        _write_yaml = lambda path, data: path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # 1. 剧本元数据.yaml
        if self._dirty.get("meta"):
            meta = {
                "script_id": self.session_id,
                "title": title,
                "version": "1.0",
                "author": "AI Realtime Engine",
                "created_date": str(date.today()),
                "last_modified": str(date.today()),
                "target_audience": "general",
                "age_rating": "全年龄",
                "language": "zh-CN",
                "estimated_duration": f"{self.seed.get('estimated_scenes', 5) * 5}分钟",
                "difficulty": "medium",
                "replay_value": "medium",
                "genre": self.seed.get("genre", ""),
                "narrative_style": self.seed.get("narrative_style", ""),
                "is_realtime": True,
            }
            _write_yaml(script_dir / "剧本元数据.yaml", meta)
            self._dirty["meta"] = False

        # 2. 世界书.md
        if self._dirty.get("worldbook"):
            worldbook = f"# 世界书\n\n## 世界背景\n{self.seed.get('world_setting', '')}\n\n"
            worldbook += f"## 核心冲突\n{self.seed.get('core_conflict', '')}\n\n## 故事边界\n"
            for rule in self.seed.get("world_rules", [])[:3]:
                worldbook += f"- {rule}\n"
            worldbook += f"\n## 情感基调\n{self.seed.get('tone', '')}\n"
            (script_dir / "世界书.md").write_text(worldbook, encoding="utf-8")
            self._dirty["worldbook"] = False

        # 3. 主要角色.yaml
        if self._dirty.get("characters"):
            _write_yaml(script_dir / "角色" / "主要角色.yaml",
                         {"characters": self.seed.get("characters", [])})
            self._dirty["characters"] = False

        # 4. 角色关系.yaml
        if self._dirty.get("relationships"):
            _write_yaml(script_dir / "角色" / "角色关系.yaml",
                         {"relationships": [], "relationship_types": [], "change_rules": []})
            self._dirty["relationships"] = False

        # 5. 主线剧情.yaml
        if self._dirty.get("plot"):
            stages = [{
                "name": s.get("scene_name", f"场景{i + 1}"),
                "goal": "",
                "conflict": "",
                "scenes": [s.get("scene_id", f"scene_{i + 1:02d}")],
            } for i, s in enumerate(self.scenes)]
            _write_yaml(script_dir / "剧情" / "主线剧情.yaml", {
                "main_plot": {
                    "summary": self.seed.get("plot_summary", ""),
                    "themes": self.seed.get("themes", []),
                    "core_conflict": self.seed.get("core_conflict", ""),
                    "stages": stages,
                    "beats": [],
                    "hooks": [],
                }
            })
            self._dirty["plot"] = False

        # 6. 结局系统.yaml
        if self._dirty.get("endings"):
            endings = []
            for s in self.scenes:
                if s.get("is_ending"):
                    endings.append({
                        "id": f"ending_{s.get('ending_type', 'normal')}",
                        "type": s.get("ending_type", "normal"),
                        "title": s.get("scene_name", ""),
                        "trigger_conditions": [],
                        "narrative": s.get("narration", "")[:100],
                        "epilogue": "",
                        "replay_value": "",
                        "unlock_conditions": "",
                    })
            if not endings:
                endings = [{"id": "ending_normal", "type": "normal", "title": "结局",
                            "trigger_conditions": [], "narrative": "", "epilogue": "",
                            "replay_value": "", "unlock_conditions": ""}]
            _write_yaml(script_dir / "剧情" / "结局系统.yaml", {"endings": endings})
            self._dirty["endings"] = False

        # 7. 世界线.yaml
        if self._dirty.get("worldline"):
            path = [{
                "scene_id": s.get("scene_id", f"scene_{i + 1:02d}"),
                "sequence": i + 1,
                "description": s.get("scene_name", ""),
            } for i, s in enumerate(self.scenes)]
            _write_yaml(script_dir / "剧情" / "世界线.yaml", {
                "world_lines": [{
                    "id": "main_line", "name": "主线",
                    "description": self.seed.get("plot_summary", ""),
                    "path": path, "key_choices": [],
                    "end_ending": f"ending_{self.scenes[-1].get('ending_type', 'normal')}" if self.scenes else "",
                }],
                "convergence_points": [], "rules": [],
            })
            self._dirty["worldline"] = False

        # 8. 场景清单.yaml
        if self._dirty.get("scenes"):
            scenes_yaml = [{
                "id": s.get("scene_id", f"scene_{i + 1:02d}"),
                "name": s.get("scene_name", f"场景{i + 1}"),
                "location": s.get("location", ""),
                "time": s.get("time", ""),
                "atmosphere": s.get("atmosphere", ""),
                "description": s.get("narration", ""),
                "trigger_conditions": {"type": "game_start"} if i == 0 else {},
                "characters_present": s.get("characters_present", []),
                "interactions": [],
                "assets_needed": [],
                "branching_point": i < len(self.scenes) - 1,
                "critical_choice": "",
                "narrative_notes": s.get("image_description", ""),
            } for i, s in enumerate(self.scenes)]
            _write_yaml(script_dir / "场景" / "场景清单.yaml", {"scenes": scenes_yaml})
            self._dirty["scenes"] = False

        # 9. 素材清单.yaml
        if self._dirty.get("assets"):
            _write_yaml(script_dir / "素材" / "素材清单.yaml", {
                "assets": {
                    "visual": [
                        {"type": "角色立绘", "characters": [
                            {"name": c.get("name", "")} for c in self.seed.get("characters", [])
                        ]},
                        {"type": "场景背景", "scenes": [
                            {"name": s.get("scene_name", ""), "mood": s.get("atmosphere", ""),
                             "details": s.get("image_description", "")}
                            for s in self.scenes
                        ]},
                    ]
                }
            })
            self._dirty["assets"] = False

        # 10. 关键选择.yaml
        if self._dirty.get("choices"):
            choices_list = []
            for i, s in enumerate(self.scenes):
                sc = s.get("choices", [])
                if sc:
                    options = [{
                        "id": c.get("id", f"choice_{i + 1}_{j + 1}"),
                        "text": c.get("text", ""),
                        "leads_to_ending": None,
                    } for j, c in enumerate(sc)]
                    choices_list.append({
                        "id": f"choice_{i + 1:02d}",
                        "scene_id": s.get("scene_id", f"scene_{i + 1:02d}"),
                        "prompt": s.get("scene_name", ""),
                        "description": "",
                        "options": options,
                    })
            _write_yaml(script_dir / "剧情" / "事件" / "关键选择.yaml", {"choices": choices_list})
            self._dirty["choices"] = False

        # 11. 事件清单.yaml
        if self._dirty.get("events"):
            events = []
            for i, s in enumerate(self.scenes):
                if 0 < i < len(self.scenes) - 1:
                    events.append({
                        "id": f"event_{i:02d}",
                        "name": s.get("scene_name", f"事件{i}"),
                        "type": "plot",
                        "description": s.get("narration", "")[:50],
                        "trigger_conditions": {},
                        "location": s.get("location", ""),
                        "time": s.get("time", ""),
                        "characters_involved": s.get("characters_present", []),
                        "content": s.get("narration", "")[:100],
                        "choices": [{"text": c.get("text", "")} for c in s.get("choices", [])[:2]],
                        "rewards": "",
                    })
            if not events:
                events = [{"id": "event_01", "name": "关键事件", "type": "plot",
                           "description": "", "trigger_conditions": {}, "location": "",
                           "time": "", "characters_involved": [], "content": "",
                           "choices": [], "rewards": ""}]
            _write_yaml(script_dir / "剧情" / "事件" / "事件清单.yaml", {"events": events})
            self._dirty["events"] = False

        return script_dir
