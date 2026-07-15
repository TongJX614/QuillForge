"""
Narrative Generation Harness — 主引擎
实现完整的 5 阶段 Pipeline：
  Stage 1: Context Assembly
  Stage 2: Narrative Anchoring
  Stage 3: Dialogue Generation
  Stage 4: Choice Branching
  Stage 5: Validation Gate (含重试)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from openai import OpenAI

from schemas import (
    HarnessInput, HarnessOutput, AssembledContext,
    Dialogue, Choice, ValidationResult, GenerationOptions,
)
from validator import NarrativeValidator


# ═══════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CONFIG_DIR = PROJECT_ROOT / "config"


def load_config() -> dict:
    """加载 harness_config.yaml"""
    config_path = CONFIG_DIR / "harness_config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def load_prompt(filename: str) -> str:
    """加载 Prompt 模板文件"""
    path = PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════
# Prompt 模板渲染
# ═══════════════════════════════════════════════════════

def render_template(template: str, variables: dict) -> str:
    """
    简单的模板变量替换
    支持 {{variable}} 和 {{#if var}}...{{/if}} 和 {{#each arr}}...{{/each}}
    """
    result = template

    # 处理 {{#if var}}...{{#else}}...{{/if}}
    def replace_if(match):
        var_name = match.group(1).strip()
        then_block = match.group(2)
        else_block = match.group(3) if match.group(3) is not None else ""
        if variables.get(var_name):
            return then_block
        return else_block

    result = re.sub(
        r'\{\{#if\s+(\w+)\}\}(.*?)(?:\{\{#else\}\}(.*?))?\{\{/if\}\}',
        replace_if,
        result,
        flags=re.DOTALL,
    )

    # 处理 {{#each arr}}...{{/each}}
    def replace_each(match):
        var_name = match.group(1).strip()
        block = match.group(2)
        items = variables.get(var_name, [])
        parts = []
        for item in items:
            rendered = block
            # 统一转为 dict 处理，支持 dict / dataclass / 普通对象
            if isinstance(item, dict):
                item_dict = item
            elif hasattr(item, 'to_dict'):
                item_dict = item.to_dict()
            elif hasattr(item, '__dataclass_fields__'):
                item_dict = {k: getattr(item, k, '') for k in item.__dataclass_fields__}
            else:
                rendered = rendered.replace("{{this}}", str(item))
                parts.append(rendered)
                continue
            # 用 dict 的键值替换模板变量
            for k, v in item_dict.items():
                rendered = rendered.replace(f"{{{{this.{k}}}}}", str(v))
            # 清理空值行：删除只包含标签但值为空的行
            rendered = re.sub(r'(?m)^[\s]*[^{]*\{\{this\.\w+\}\}.*$', '', rendered)
            # 清理标签已替换但值为空的行（如 "  动机：\n" 或 "- 秘密：\n"）
            rendered = re.sub(r'(?m)^[\s]*[-•]?\s*[^：:\n]+[：:]\s*$', '', rendered)
            # 清理连续空行
            rendered = re.sub(r'\n{3,}', '\n\n', rendered)
            parts.append(rendered)
        return "\n".join(parts)

    result = re.sub(
        r'\{\{#each\s+(\w+)\}\}(.*?)\{\{/each\}\}',
        replace_each,
        result,
        flags=re.DOTALL,
    )

    # 处理简单变量 {{variable}}
    for key, value in variables.items():
        if isinstance(value, (str, int, float, bool)):
            result = result.replace(f"{{{{{key}}}}}", str(value))

    return result


# ═══════════════════════════════════════════════════════
# LLM 调用封装
# ═══════════════════════════════════════════════════════

class LLMClient:
    """统一的 LLM 调用接口"""

    def __init__(self, config: dict):
        # 加载项目根目录下的 .env（支持 DEEPSEEK_API_KEY / OPENAI_API_KEY）
        env_path = PROJECT_ROOT.parent / ".env"
        load_dotenv(env_path)
        load_dotenv(PROJECT_ROOT / ".env")  # 也尝试加载 narrative-harness/.env

        llm_cfg = config.get("harness", {}).get("llm", {})
        self.model = llm_cfg.get("model", "deepseek-chat")
        self.temperature = llm_cfg.get("temperature", 0.8)
        self.max_tokens = llm_cfg.get("maxTokens", 3000)

        # 优先使用 DEEPSEEK_API_KEY，兼容 OPENAI_API_KEY
        api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or llm_cfg.get("apiKey", "sk-placeholder")
        )
        api_base = llm_cfg.get("apiBase", "https://api.deepseek.com")

        self.client = OpenAI(api_key=api_key, base_url=api_base)

    def generate(self, system_prompt: str, user_prompt: str, model: str = "") -> str:
        """调用 LLM 生成文本"""
        response = self.client.chat.completions.create(
            model=model or self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content or ""

    def generate_json(self, system_prompt: str, user_prompt: str, model: str = "", _retries: int = 2) -> dict | list:
        """调用 LLM 并解析 JSON 输出，失败自动重试"""
        last_err = None
        for attempt in range(_retries + 1):
            raw = self.generate(system_prompt, user_prompt, model)
            # 尝试提取 JSON 块
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
            json_str = json_match.group(1).strip() if json_match else raw.strip()

            # 如果整体是数组或对象，直接解析
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 尝试找第一个 [ 或 { 到最后一个 ] 或 }
                for start_char, end_char in [('[', ']'), ('{', '}')]:
                    start = json_str.find(start_char)
                    end = json_str.rfind(end_char)
                    if start != -1 and end != -1 and end > start:
                        try:
                            return json.loads(json_str[start:end + 1])
                        except json.JSONDecodeError:
                            continue
                last_err = raw[:500]
                if attempt < _retries:
                    print(f"  [JSON RETRY] 解析失败，重试第 {attempt + 1} 次...")
                    continue
        raise ValueError(f"无法解析 LLM 输出为 JSON (重试 {_retries} 次):\n{last_err}")


# ═══════════════════════════════════════════════════════
# Pipeline 引擎
# ═══════════════════════════════════════════════════════

class NarrativeHarness:
    """叙事生成 Harness 主引擎"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
        else:
            self.config = load_config()

        self.llm = LLMClient(self.config)
        self.system_prompt = load_prompt("system_prompt_v2.md")
        self.system_prompt_dynamic = load_prompt("system_prompt_v2.md")
        self.scene_template = load_prompt("narration_v2.md")
        self.dialogue_template = load_prompt("dialogue_v2.md")
        self.choice_template = load_prompt("choices_v2.md")

    def run(self, input_data: dict | HarnessInput) -> dict:
        """
        执行完整 Pipeline，返回结果字典
        支持传入 dict 或 HarnessInput 对象
        """
        start_time = time.time()

        # 提取额外上下文（来自适配器的世界书、叙事风格等）
        extra_context = {}
        if isinstance(input_data, dict):
            extra_context = {k: v for k, v in input_data.items() if k.startswith("_")}
            harness_input = HarnessInput.from_dict(input_data)
        else:
            harness_input = input_data

        # 覆盖模型配置
        if harness_input.options.model:
            self.llm.model = harness_input.options.model

        # 构建系统提示词（融合世界书约束和叙事风格）
        sys_prompt_vars = {
            "narrativeStyle": extra_context.get("_narrativeStyle", "第三人称叙事"),
            "worldbookRules": extra_context.get("_worldbook", ""),
            "plotSummary": extra_context.get("_plotSummary", ""),
            "coreConflict": extra_context.get("_coreConflict", ""),
            "themes": extra_context.get("_themes", ""),
            "relationshipNetwork": extra_context.get("_relationshipNetwork", ""),
            "sceneAtmosphere": (
                extra_context.get("_sceneAtmosphere", "")
                or extra_context.get("_sceneDetails", "")[:200]
            ),
            "narrativeNotes": extra_context.get("_narrativeNotes", ""),
            "emotionalArc": extra_context.get("_emotionalArc", ""),
            "minDialogues": harness_input.options.dialogue_count_min,
            "maxDialogues": harness_input.options.dialogue_count_max,
        }
        rendered_sys_prompt = render_template(self.system_prompt_dynamic, sys_prompt_vars)

        # ── Stage 1: Context Assembly ──
        print("[Stage 1/5] 上下文组装...")
        context = AssembledContext.from_input(harness_input)

        # ── Stage 2-4 with retry loop ──
        max_retries = (
            self.config.get("harness", {})
            .get("stages", {})
            .get("validationGate", {})
            .get("maxRetries", 2)
        )

        narration = ""
        dialogues: list[Dialogue] = []
        choices: list[Choice] = []
        validation: Optional[ValidationResult] = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                print(f"  [RETRY] 重试第 {attempt} 次...")

            # ── Stage 2: Narrative Anchoring ──
            print(f"[Stage 2/5] 叙事锚定 (attempt {attempt + 1})...")
            narration = self._stage_narrative(context, harness_input, rendered_sys_prompt, extra_context)

            # ── Stage 3: Dialogue Generation ──
            print(f"[Stage 3/5] 对话生成 (attempt {attempt + 1})...")
            dialogues = self._stage_dialogue(context, narration, harness_input, rendered_sys_prompt, extra_context)

            # ── Stage 4: Choice Branching ──
            # 当剧本已定义选项时（_hasScriptChoices），跳过 LLM 生成，
            # 直接使用剧本选项（同时也让验证器看到真实选项）
            if extra_context.get("_hasScriptChoices"):
                print(f"[Stage 4/5] 选择分支 (跳过：剧本已定义选项)")
                script_choices_raw = extra_context.get("_scriptChoices", [])
                choices = [
                    Choice(
                        text=c.get("text", ""),
                        effect=c.get("effect", ""),
                        worldline_impact=c.get("worldlineImpact", "advance"),
                    )
                    for c in script_choices_raw
                ]
            else:
                print(f"[Stage 4/5] 选择分支 (attempt {attempt + 1})...")
                choices = self._stage_choices(context, narration, dialogues, harness_input, rendered_sys_prompt, extra_context)

            # ── Stage 5: Validation Gate ──
            print(f"[Stage 5/5] 验证门控 (attempt {attempt + 1})...")
            validator = NarrativeValidator(
                context,
                min_dialogues=harness_input.options.dialogue_count_min,
                max_dialogues=harness_input.options.dialogue_count_max,
            )
            validation = validator.validate_all(narration, dialogues, choices, retry_count=attempt)

            if validation.passed:
                print("  [OK] 验证通过")
                break
            else:
                failed = [c.name for c in validation.checks if not c.passed]
                print(f"  [FAIL] 验证未通过: {failed}")

        # ── Fallback：对话保底机制（三层保底：LLM重试 → 简化prompt → 硬编码）──
        min_req = harness_input.options.dialogue_count_min
        if len(dialogues) < min_req:
            print(f"  [FALLBACK] 对话不足 ({len(dialogues)}/{min_req})，启用保底生成...")
            fallback = self._generate_fallback_dialogues(context, narration, min_req)
            if fallback:
                dialogues = fallback
                print(f"  [FALLBACK] 保底生成 {len(fallback)} 轮对话")
            # 重新验证（无论保底是否成功）
            validator = NarrativeValidator(
                context,
                min_dialogues=min_req,
                max_dialogues=harness_input.options.dialogue_count_max,
            )
            validation = validator.validate_all(narration, dialogues, choices, retry_count=max_retries + 1)

        # 构建输出
        elapsed = int((time.time() - start_time) * 1000)
        worldline_state = {
            "currentNode": context.current_node,
            "nodeIndex": context.node_index,
            "totalNodes": context.total_nodes,
            "progress": round(context.worldline_progress, 2),
        }

        output = HarnessOutput(
            success=validation.passed if validation else False,
            narration=narration,
            dialogues=dialogues,
            next_choices=choices,
            worldline_state=worldline_state,
            validation=validation or ValidationResult(passed=False, checks=[]),
            metadata={
                "generationTime": elapsed,
                "model": self.llm.model,
            },
        )

        result = output.to_dict()
        print(f"\n{'=' * 50}")
        print(f"Pipeline 完成 | 耗时: {elapsed}ms | 验证: {'通过' if output.success else '未通过'}")
        print(f"{'=' * 50}")
        return result

    # ───────────────────────────────────────────────────
    # Stage 实现
    # ───────────────────────────────────────────────────

    def _stage_narrative(self, ctx: AssembledContext, inp: HarnessInput, sys_prompt: str = "", extra: dict = None) -> str:
        """Stage 2: 生成场景旁白"""
        extra = extra or {}
        variables = {
            "worldline": ctx.raw_worldline,
            "annotatedWorldline": ctx.annotated_worldline,
            "currentNode": ctx.current_node,
            "nodeIndex": ctx.node_index + 1,
            "totalNodes": ctx.total_nodes,
            "previousNode": ctx.previous_node or "（起始）",
            "nextNode": ctx.next_node or "（终局）",
            "worldlineProgress": int(ctx.worldline_progress * 100),
            "sceneDescription": ctx.scene_description,
            "characters": ctx.active_characters,
            "playerDecision": ctx.player_decision,
            "previousNarration": inp.previous_context.narration if inp.previous_context else "",
            # 剧本全局上下文
            "stagesOverview": extra.get("_stagesOverview", ""),
            "events": extra.get("_events", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "plotSummary": extra.get("_plotSummary", ""),
            "sceneDetails": extra.get("_sceneDetails", ""),
            "worldlines": extra.get("_worldlines", ""),
            "endings": extra.get("_endings", ""),
            "keyChoices": extra.get("_keyChoices", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
            # 当前场景专属剧情上下文
            "currentStageContext": extra.get("_currentStageContext", ""),
            "sceneBeats": extra.get("_sceneBeats", ""),
            "sceneHooks": extra.get("_sceneHooks", ""),
        }

        user_prompt = render_template(self.scene_template, variables)
        narration = self.llm.generate(sys_prompt or self.system_prompt, user_prompt)

        # 清理可能的 markdown 标记
        narration = narration.strip()
        if narration.startswith("```"):
            narration = re.sub(r'^```[a-z]*\n?', '', narration)
            narration = re.sub(r'\n?```$', '', narration)

        return narration.strip()

    def _stage_dialogue(self, ctx: AssembledContext, narration: str, inp: HarnessInput, sys_prompt: str = "", extra: dict = None) -> list[Dialogue]:
        """Stage 3: 生成对话"""
        extra = extra or {}
        opts = inp.options
        variables = {
            "narration": narration,
            "characters": ctx.active_characters,
            "annotatedWorldline": ctx.annotated_worldline,
            "currentNode": ctx.current_node,
            "nextNode": ctx.next_node or "（终局）",
            "worldlineProgress": int(ctx.worldline_progress * 100),
            "playerDecision": ctx.player_decision,
            "dialogueCount": (opts.dialogue_count_min + opts.dialogue_count_max) // 2,
            "minDialogues": opts.dialogue_count_min,
            "maxDialogues": opts.dialogue_count_max,
            "previousDialogues": inp.previous_context.dialogues if inp.previous_context else [],
            # 剧本全局上下文
            "events": extra.get("_events", ""),
            "stagesOverview": extra.get("_stagesOverview", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "relationshipNetwork": extra.get("_relationshipNetwork", ""),
            "sceneDetails": extra.get("_sceneDetails", ""),
            "worldlines": extra.get("_worldlines", ""),
            "endings": extra.get("_endings", ""),
            "keyChoices": extra.get("_keyChoices", ""),
            # 当前场景专属剧情上下文
            "currentStageContext": extra.get("_currentStageContext", ""),
            "sceneBeats": extra.get("_sceneBeats", ""),
            "sceneHooks": extra.get("_sceneHooks", ""),
        }

        user_prompt = render_template(self.dialogue_template, variables)

        dialogues = []
        try:
            raw_dialogues = self.llm.generate_json(sys_prompt or self.system_prompt, user_prompt)
        except (ValueError, Exception) as e:
            print(f"  [DIALOGUE] LLM JSON 解析失败: {e}")
            return dialogues  # 返回空，由 fallback 机制处理

        if isinstance(raw_dialogues, list):
            # 过滤非法 speaker：必须是角色卡中的角色名
            valid_speakers = {c.name for c in ctx.active_characters}
            filtered_count = 0
            for d in raw_dialogues:
                if isinstance(d, dict):
                    speaker = d.get("speaker", "").strip()
                    # 过滤掉「系统」「旁白」「叙述者」等非角色 speaker
                    if speaker not in valid_speakers:
                        filtered_count += 1
                        print(f"  [FILTER] 过滤 speaker: '{speaker}' (合法: {valid_speakers})")
                        continue
                    dialogues.append(Dialogue(
                        speaker=speaker,
                        text=d.get("text", ""),
                        emotion=d.get("emotion", ""),
                    ))
            print(f"  [DIALOGUE] LLM生成 {len(raw_dialogues)} 轮, 过滤 {filtered_count} 轮, 保留 {len(dialogues)} 轮")
        else:
            print(f"  [DIALOGUE] LLM返回非列表类型: {type(raw_dialogues).__name__}")

        return dialogues

    def _generate_fallback_dialogues(self, ctx: AssembledContext, narration: str, min_count: int) -> list[Dialogue]:
        """保底对话生成：用极简 prompt 强制生成最少轮数的对话，失败则硬编码保底"""
        char_names = [c.name for c in ctx.active_characters]
        if not char_names:
            return []

        # 极简 prompt：只给核心信息，避免复杂上下文干扰
        chars_text = "\n".join(
            f"- {c.name}（{c.role}）：{c.personality[:80]}，说话风格：{c.speaking_style[:50]}"
            for c in ctx.active_characters
        )
        prompt = (
            f"你是一位剧本对话作家。请为以下场景写 {min_count} 轮角色对话。\n\n"
            f"场景：{narration[:200]}\n\n"
            f"角色：\n{chars_text}\n\n"
            f"要求：\n"
            f"1. 必须生成恰好 {min_count} 轮对话\n"
            f"2. speaker 只能是以下名字之一：{', '.join(char_names)}\n"
            f"3. 每轮必须有 speaker 和 text\n"
            f"4. 同一角色不得连续发言超过2轮\n"
            f"5. 直接输出 JSON 数组，不要添加其他文字\n\n"
            f'输出格式：[{{"speaker": "角色名", "text": "对话内容", "emotion": "平静"}}]'
        )

        system = "你是一位专业的剧本对话作家，严格按JSON格式输出。"

        # 尝试 LLM 保底生成
        try:
            raw = self.llm.generate_json(system, prompt, _retries=2)
            dialogues = []
            valid_speakers = set(char_names)
            if isinstance(raw, list):
                for d in raw:
                    if isinstance(d, dict):
                        speaker = d.get("speaker", "").strip()
                        if speaker in valid_speakers and d.get("text"):
                            dialogues.append(Dialogue(
                                speaker=speaker,
                                text=d.get("text", ""),
                                emotion=d.get("emotion", "平静"),
                            ))
            if len(dialogues) >= min_count:
                return dialogues
            print(f"  [FALLBACK] LLM保底生成仅 {len(dialogues)} 轮，不足 {min_count}，启用硬编码保底")
        except Exception as e:
            print(f"  [FALLBACK] LLM保底生成异常: {e}，启用硬编码保底")

        # 最终保底：硬编码生成最小对话（轮流发言，基于角色性格）
        print(f"  [FALLBACK] 硬编码保底：为 {char_names} 生成 {min_count} 轮对话")
        dialogues = []
        for i in range(min_count):
            speaker = char_names[i % len(char_names)]
            # 找到该角色的 Character 对象，获取性格信息
            char = next((c for c in ctx.active_characters if c.name == speaker), None)
            role_hint = char.role if char else ""
            text = f"（{speaker}观察着周围的情况，思考着下一步行动）"
            if char and char.personality:
                text = f"（{speaker}以{char.personality[:10]}的态度回应）目前的情况需要我们谨慎应对。"
            dialogues.append(Dialogue(
                speaker=speaker,
                text=text,
                emotion="平静",
            ))
        return dialogues

    def _stage_choices(self, ctx: AssembledContext, narration: str,
                        dialogues: list[Dialogue], inp: HarnessInput, sys_prompt: str = "", extra: dict = None) -> list[Choice]:
        """Stage 4: 生成选择分支"""
        extra = extra or {}
        variables = {
            "narration": narration,
            "dialogues": [d.to_dict() for d in dialogues],
            "worldline": ctx.raw_worldline,
            "currentNode": ctx.current_node,
            "nextNode": ctx.next_node or "（终局）",
            "worldlineProgress": int(ctx.worldline_progress * 100),
            # 剧本全局上下文
            "keyChoices": extra.get("_keyChoices", ""),
            "endings": extra.get("_endings", ""),
            "coreConflict": extra.get("_coreConflict", ""),
            "sceneDetails": extra.get("_sceneDetails", ""),
            "worldlines": extra.get("_worldlines", ""),
            "events": extra.get("_events", ""),
            # 当前场景专属剧情上下文
            "currentStageContext": extra.get("_currentStageContext", ""),
            "sceneBeats": extra.get("_sceneBeats", ""),
            "sceneHooks": extra.get("_sceneHooks", ""),
        }

        user_prompt = render_template(self.choice_template, variables)

        choices = []
        try:
            raw_choices = self.llm.generate_json(sys_prompt or self.system_prompt, user_prompt)
        except (ValueError, Exception) as e:
            print(f"  [CHOICES] LLM JSON 解析失败: {e}")
            return choices  # 返回空，不影响整体流程

        if isinstance(raw_choices, list):
            for c in raw_choices:
                if isinstance(c, dict):
                    choices.append(Choice(
                        text=c.get("text", ""),
                        effect=c.get("effect", ""),
                        worldline_impact=c.get("worldlineImpact", "advance"),
                    ))

        return choices


# ═══════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════

def main():
    """命令行入口：读取 YAML/JSON 输入文件并执行 Pipeline"""
    import argparse
    import io

    # Windows console UTF-8 fix (only in CLI entry point)
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Narrative Generation Harness")
    parser.add_argument("input", help="输入文件路径 (YAML 或 JSON)")
    parser.add_argument("-o", "--output", help="输出文件路径 (JSON)", default="output.json")
    parser.add_argument("-c", "--config", help="配置文件路径", default=None)
    args = parser.parse_args()

    # 加载输入
    input_path = Path(args.input)
    with open(input_path, "r", encoding="utf-8") as f:
        if input_path.suffix in (".yaml", ".yml"):
            input_data = yaml.safe_load(f)
            # 如果是 example_scene.yaml 格式，提取 scene 部分
            if "scene" in input_data:
                scene = input_data["scene"]
                input_data = {
                    "currentScene": scene.get("currentScene", ""),
                    "characters": scene.get("characters", []),
                    "worldline": scene.get("worldline", ""),
                    "playerChoice": scene.get("playerChoice", ""),
                }
        else:
            input_data = json.load(f)

    # 执行 Pipeline
    harness = NarrativeHarness(config_path=args.config)
    result = harness.run(input_data)

    # 输出结果
    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存到: {output_path}")

    # 打印摘要
    if result.get("success"):
        data = result["data"]
        print(f"\n--- 旁白 ---\n{data['narration'][:200]}...")
        print(f"\n--- 对话 ({len(data['dialogues'])} 轮) ---")
        for d in data["dialogues"]:
            print(f"  {d['speaker']}: {d['text']}")
        print(f"\n--- 后续选择 ---")
        for i, c in enumerate(data["nextChoices"], 1):
            print(f"  {i}. {c['text']} → {c['effect']}")


if __name__ == "__main__":
    main()
