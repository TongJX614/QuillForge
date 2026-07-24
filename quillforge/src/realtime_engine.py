# -*- coding: utf-8 -*-
"""
AI 实时剧本生成引擎 (Realtime Story Engine)
仿照 InfiPlot 架构，与文件夹上传管线完全独立。

多智能体协作：
  Agent 1 - StoryPlanner: 一句话 → 故事种子（角色/世界观/剧情大纲/初始场景提示）
  Agent 2 - SceneGenerator: 上下文 + 玩家选择 → 单个完整场景（旁白/对话/选项/配图描述）

预测式生成：用户阅读当前场景时，后台预生成各选项的下一场景。
输出：故事结束后保存为 YAML 文件，兼容 GenericScriptAdapter 重复加载。
"""

from __future__ import annotations

import json
import os
import re
import uuid
import yaml
from pathlib import Path
from typing import Generator
from datetime import date
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI
from dotenv import load_dotenv

# ── 项目路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # quillforge/
WORKSPACE_ROOT = PROJECT_ROOT.parent                    # QuillForge workspace root
SAMPLES_DIR = PROJECT_ROOT / "samples"

MAX_INPUT_LENGTH = 150
MAX_SCENES = 8  # 安全上限，防止无限生成


# ═══════════════════════════════════════════════════════
# LLM 客户端
# ═══════════════════════════════════════════════════════

def _build_client() -> tuple[OpenAI, str]:
    """构建 LLM 客户端，复用 script_generator 的配置逻辑。"""
    load_dotenv(WORKSPACE_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    api_base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("SCRIPT_GEN_MODEL", "deepseek-v4-flash")
    client = OpenAI(api_key=api_key, base_url=api_base)
    return client, model


def _llm_call(client: OpenAI, model: str, system: str, user: str,
              max_tokens: int = 4000, temperature: float = 0.85) -> str:
    """单次 LLM 调用"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


# ═══════════════════════════════════════════════════════
# JSON 提取工具
# ═══════════════════════════════════════════════════════

def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON（处理代码围栏和前后缀文本）。"""
    text = text.strip()
    # 去除 markdown 代码围栏
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    # 尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 提取第一个 { 到最后一个 }
    first = text.find('{')
    last = text.rfind('}')
    if first >= 0 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════
# 提示词
# ═══════════════════════════════════════════════════════

STORY_PLANNER_SYSTEM = """\
你是一位专业的互动叙事剧本设计师。用户会给你一句话描述，你需要据此创作剧本的初始设定。

要求：
- 角色3-4位，每位角色都有详细外貌描述（身高体型、发型发色、标志性特征、穿着风格），用于AI生图
- gender 字段必须填写 male 或 female，用于 TTS 语音合成时区分男女声
- age 字段必须填写数字年龄（如 17、35、68），用于 TTS 语音合成时按年龄段选择声线
- 世界观简洁但有深度，情感有张力
- 剧情大纲精炼，有明确的核心冲突
- 目标游玩4-6个场景
- 所有内容使用中文

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
      "gender": "male",
      "age": 25,
      "occupation": "职业",
      "appearance": "详细外貌描述（身高体型、发型发色、标志性特征、穿着风格）",
      "personality": "性格特征（2-3个关键词）",
      "voice_tone": "说话风格",
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
7. image_description 要描述场景的视觉画面（地点/时间/天气/关键元素），用于AI生图
8. 所有内容使用中文

## 对话/动作旁白规则
- dialogues 输出为 JSON 数组，对话对象与动作旁白对象交替排列：
  · 对话对象：{"speaker_id": "char_01", "speaker": "角色名", "text": "对话内容", "emotion": "情绪"}
  · 动作旁白对象：{"type": "action", "subject": "角色名", "text": "动作/神态/心理描写"}
- 动作旁白用第三人称描写某个在场角色的动作、神态、肢体反应或心理活动
- 动作旁白不要加引号、不要写成角色说的话；心理活动也用叙述口吻（可用"心想、暗自、意识到"等）
- 每条动作旁白简洁，1句为宜
- emotion 只能是以下中文值之一：平静、急切、分析、果断、担忧、愤怒、希望、好奇、沉着、讽刺、恐惧、坚定
- 同一角色不得连续发言超过2轮
- 每句话都必须有强烈的角色辨识度——遮住 speaker 名字也能猜出是谁说的
- 最后一轮必须留下悬念或决策点

请严格以JSON格式输出，不要添加任何额外说明或代码块：

{
  "scene_name": "场景名称",
  "location": "地点",
  "time": "时间（如黄昏/深夜/清晨）",
  "atmosphere": "氛围描述",
  "narration": "旁白文本（150-250字，第二人称）",
  "dialogues": [
    {"speaker_id": "char_01", "speaker": "角色名", "text": "对话内容", "emotion": "平静"},
    {"type": "action", "subject": "角色名", "text": "动作/神态/心理描写"},
    {"speaker_id": "char_02", "speaker": "角色名B", "text": "对话内容", "emotion": "急切"}
  ],
  "choices": [
    {"id": "choice_1", "text": "选项文本", "hint": "方向暗示"}
  ],
  "characters_present": ["char_01"],
  "is_ending": false,
  "ending_type": null,
  "image_description": "场景背景图描述"
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
        self._client: OpenAI | None = None
        self._model: str = ""
        self._pre_gen_executor = ThreadPoolExecutor(max_workers=3)
        self._pre_generated: dict[str, dict] = {}    # key: "scene_idx:choice_idx" → scene
        self._pre_gen_futures: dict[str, object] = {}  # key → Future
        self._save_dir: Path | None = None  # YAML 保存目录（复用，避免重复创建）

    # ── 客户端懒加载 ──

    def _ensure_client(self):
        if self._client is None:
            self._client, self._model = _build_client()

    # ── 公开流程 ──

    def initialize(self) -> Generator[dict, None, None]:
        """初始化故事，生成种子和第一个场景。Yields SSE 事件。"""
        if not self.user_input:
            yield {"type": "error", "message": "请输入剧本描述"}
            return
        if len(self.user_input) > MAX_INPUT_LENGTH:
            yield {"type": "error", "message": f"描述不能超过{MAX_INPUT_LENGTH}字"}
            return

        self._ensure_client()

        # ── Agent 1: StoryPlanner ──
        yield {"type": "progress", "step": "planning", "message": "正在构思故事设定..."}

        seed = None
        for _ in range(2):
            try:
                raw = _llm_call(
                    self._client, self._model, STORY_PLANNER_SYSTEM,
                    f"用户想要的剧本：{self.user_input}",
                    max_tokens=3000, temperature=0.85,
                )
                parsed = _extract_json(raw)
                if parsed and parsed.get("characters") and parsed.get("title"):
                    seed = parsed
                    break
            except Exception:
                pass

        if not seed:
            yield {"type": "error", "message": "故事设定生成失败，请重试"}
            return

        self.seed = seed
        title = seed.get("title", "未命名剧本")

        # 下发故事种子
        yield {
            "type": "story_seed",
            "title": title,
            "genre": seed.get("genre", ""),
            "narrative_style": seed.get("narrative_style", ""),
            "tone": seed.get("tone", ""),
            "characters": seed.get("characters", []),
            "world_setting": seed.get("world_setting", ""),
            "plot_summary": seed.get("plot_summary", ""),
            "estimated_scenes": seed.get("estimated_scenes", 5),
        }

        # ── Agent 2: SceneGenerator（第一个场景）──
        yield {"type": "progress", "step": "scene", "message": f"正在生成《{title}》的开场..."}

        scene = self._generate_scene(0, player_choice="")
        if not scene:
            yield {"type": "error", "message": "首个场景生成失败，请重试"}
            return

        self.scenes.append(scene)
        self.current_scene_index = 0
        yield self._format_scene_event(scene, 0)

        # 首场景生成后立即保存 YAML（出现在“已保存剧本”列表中）
        try:
            self.save_to_yaml()
        except Exception:
            pass

        # 预生成下一批
        if not scene.get("is_ending"):
            self._start_pre_generation(0)

        yield {"type": "done", "session_id": self.session_id}

    def choose(self, choice_index: int) -> Generator[dict, None, None]:
        """玩家做出选择，返回下一场景。Yields SSE 事件。"""
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

        # ── 检查预生成缓存 ──
        pre_key = f"{self.current_scene_index}:{choice_index}"
        scene = self._pre_generated.get(pre_key)

        if scene is None:
            # 检查是否有正在进行的预生成
            future = self._pre_gen_futures.get(pre_key)
            if future and not future.done():
                yield {"type": "progress", "step": "scene", "message": "正在生成下一场景..."}
                try:
                    scene = future.result(timeout=60)
                except Exception:
                    scene = None

        if scene is None:
            # 即时生成
            yield {"type": "progress", "step": "scene", "message": "正在生成下一场景..."}
            scene = self._generate_scene(
                self.current_scene_index + 1,
                player_choice=choice_text,
            )

        if not scene:
            yield {"type": "error", "message": "场景生成失败，请重试"}
            return

        self.scenes.append(scene)
        self.current_scene_index = len(self.scenes) - 1
        yield self._format_scene_event(scene, self.current_scene_index)

        # 每个新场景生成后增量保存 YAML
        try:
            self.save_to_yaml()
        except Exception:
            pass

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

    def _generate_scene(self, scene_idx: int, player_choice: str = "") -> dict | None:
        """调用 SceneGenerator Agent 生成单个场景。"""
        self._ensure_client()

        estimated = self.seed.get("estimated_scenes", 5)
        scene_number = scene_idx + 1

        # 角色列表文本
        chars_text = "\n".join([
            f"- {c.get('id', '')} ({c.get('name', '')})：{c.get('role', '')}，"
            f"{c.get('personality', '')}，动机：{c.get('motivation', '')}"
            for c in self.seed.get("characters", [])
        ])

        # 上下文段落
        if scene_idx == 0:
            context_section = f"【初始场景提示】\n{self.seed.get('initial_scene_hint', '')}"
        else:
            history_parts = []
            for i, s in enumerate(self.scenes):
                narration = s.get("narration", "")[:120]
                chosen_text = ""
                if i < len(self.history):
                    ci = self.history[i]["choice_index"]
                    opts = s.get("choices", [])
                    if 0 <= ci < len(opts):
                        chosen_text = opts[ci].get("text", "")
                history_parts.append(
                    f"场景{i + 1}「{s.get('scene_name', '')}」：{narration}\n"
                    f"  玩家选择：{chosen_text}"
                )
            context_section = "【已发生的故事】\n" + "\n".join(history_parts)

        user_prompt = (
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

        for _ in range(2):
            try:
                raw = _llm_call(
                    self._client, self._model,
                    SCENE_GENERATOR_SYSTEM,
                    user_prompt,
                    max_tokens=8000, temperature=0.8,
                )
                scene = _extract_json(raw)
                if scene and scene.get("narration"):
                    # 补全字段
                    scene.setdefault("scene_id", f"scene_{scene_idx + 1:02d}")
                    scene["scene_index"] = scene_idx
                    # 非结束场景必须有选项
                    if not scene.get("is_ending") and not scene.get("choices"):
                        scene["choices"] = [
                            {"id": "choice_1", "text": "继续探索", "hint": "继续前进"},
                        ]
                    # 结束场景清空选项
                    if scene.get("is_ending"):
                        scene["choices"] = []
                    return scene
            except Exception:
                pass
        return None

    def _start_pre_generation(self, scene_idx: int):
        """后台预生成当前场景各选项对应的下一场景。"""
        if scene_idx < 0 or scene_idx >= len(self.scenes):
            return
        current = self.scenes[scene_idx]
        if current.get("is_ending"):
            return

        choices = current.get("choices", [])
        for i, _choice in enumerate(choices):
            key = f"{scene_idx}:{i}"
            if key in self._pre_generated or key in self._pre_gen_futures:
                continue

            choice_text = _choice.get("text", "")
            next_idx = scene_idx + 1

            def _do_pre_gen(k, idx, ct):
                try:
                    s = self._generate_scene(idx, player_choice=ct)
                    if s:
                        self._pre_generated[k] = s
                    return s
                except Exception:
                    return None

            future = self._pre_gen_executor.submit(_do_pre_gen, key, next_idx, choice_text)
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
        }
        _write_yaml(script_dir / "剧本元数据.yaml", meta)

        # 2. 世界书.md
        worldbook = f"# 世界书\n\n## 世界背景\n{self.seed.get('world_setting', '')}\n\n"
        worldbook += f"## 核心冲突\n{self.seed.get('core_conflict', '')}\n\n## 故事边界\n"
        for rule in self.seed.get("world_rules", [])[:3]:
            worldbook += f"- {rule}\n"
        worldbook += f"\n## 情感基调\n{self.seed.get('tone', '')}\n"
        (script_dir / "世界书.md").write_text(worldbook, encoding="utf-8")

        # 3. 主要角色.yaml
        _write_yaml(script_dir / "角色" / "主要角色.yaml",
                     {"characters": self.seed.get("characters", [])})

        # 4. 角色关系.yaml
        _write_yaml(script_dir / "角色" / "角色关系.yaml",
                     {"relationships": [], "relationship_types": [], "change_rules": []})

        # 5. 主线剧情.yaml
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

        # 6. 结局系统.yaml
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

        # 7. 世界线.yaml
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

        # 8. 场景清单.yaml
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

        # 9. 素材清单.yaml
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

        # 10. 关键选择.yaml
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

        # 11. 事件清单.yaml
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

        return script_dir
