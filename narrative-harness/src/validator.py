"""
Narrative Generation Harness — 验证器
实现 Stage 5 Validation Gate 的所有校验逻辑
"""

from __future__ import annotations
from schemas import (
    Dialogue, Choice, AssembledContext, HarnessOutput,
    ValidationCheck, ValidationResult,
)


class NarrativeValidator:
    """叙事内容验证器"""

    VALID_EMOTIONS = {
        # 中文
        "平静", "急切", "分析", "果断", "担忧",
        "愤怒", "希望", "好奇", "沉着", "讽刺",
        "恐惧", "坚定", "紧张", "释然", "中性",
        # 英文（兼容）
        "calm", "urgent", "analytical", "decisive", "worried",
        "angry", "hopeful", "curious", "composed", "sarcastic",
        "fearful", "excited", "neutral", "tense", "relieved",
        "determined",
    }

    VALID_IMPACTS = {"advance", "advance_risky", "stay_current", "side_branch"}

    def __init__(self, context: AssembledContext, min_dialogues: int = 3, max_dialogues: int = 5):
        self.context = context
        self.min_dialogues = min_dialogues
        self.max_dialogues = max_dialogues
        self.character_names = {c.name for c in context.active_characters}

    def validate_all(
        self,
        narration: str,
        dialogues: list[Dialogue],
        choices: list[Choice],
        retry_count: int = 0,
    ) -> ValidationResult:
        """执行全部验证，返回汇总结果"""
        checks = [
            self.check_schema(narration, dialogues, choices),
            self.check_dialogue_count(dialogues),
            self.check_character_consistency(dialogues),
            self.check_consecutive_speaker(dialogues),
            self.check_personality_match(dialogues),
            self.check_worldline_adherence(narration, dialogues),
            self.check_choice_diversity(choices),
            self.check_choice_validity(choices),
        ]

        # 判断总体是否通过
        # Critical 或 High 级别的失败 = 总体不通过
        passed = all(
            c.passed for c in checks if c.severity in ("critical", "high")
        )

        return ValidationResult(
            passed=passed,
            checks=checks,
            retry_count=retry_count,
        )

    def check_schema(self, narration: str, dialogues: list[Dialogue], choices: list[Choice]) -> ValidationCheck:
        """Schema 合规检查"""
        issues = []
        if not narration or len(narration.strip()) < 10:
            issues.append("旁白过短")
        if not dialogues:
            issues.append("无对话")
        if not choices:
            issues.append("无选择项")
        for d in dialogues:
            if not d.speaker or not d.text:
                issues.append(f"对话缺少 speaker 或 text: {d}")
        for c in choices:
            if not c.text or not c.effect:
                issues.append(f"选择缺少 text 或 effect: {c}")

        return ValidationCheck(
            name="schema_compliance",
            passed=len(issues) == 0,
            severity="critical",
            detail="; ".join(issues) if issues else "合规",
        )

    def check_dialogue_count(self, dialogues: list[Dialogue]) -> ValidationCheck:
        """对话轮数检查：至少 min 轮，最多 max 轮"""
        count = len(dialogues)
        ok = self.min_dialogues <= count <= self.max_dialogues
        return ValidationCheck(
            name="dialogue_count",
            passed=ok,
            severity="high",
            detail=f"{count} 轮对话（要求 {self.min_dialogues}-{self.max_dialogues}）",
        )

    def check_character_consistency(self, dialogues: list[Dialogue]) -> ValidationCheck:
        """角色一致性：所有 speaker 必须在角色卡列表中"""
        unknown = [d.speaker for d in dialogues if d.speaker not in self.character_names]
        ok = len(unknown) == 0
        return ValidationCheck(
            name="character_consistency",
            passed=ok,
            severity="critical",
            detail=f"未识别的角色: {set(unknown)}" if unknown else "所有对话角色匹配",
        )

    def check_consecutive_speaker(self, dialogues: list[Dialogue]) -> ValidationCheck:
        """连续发言检查：同一角色不得连续发言 3 轮以上"""
        max_consecutive = 0
        current_speaker = None
        current_count = 0
        for d in dialogues:
            if d.speaker == current_speaker:
                current_count += 1
            else:
                current_speaker = d.speaker
                current_count = 1
            max_consecutive = max(max_consecutive, current_count)

        ok = max_consecutive <= 2
        return ValidationCheck(
            name="consecutive_speaker",
            passed=ok,
            severity="medium",
            detail=f"最大连续发言 {max_consecutive} 轮" if not ok else "无超长连续发言",
        )

    def check_personality_match(self, dialogues: list[Dialogue]) -> ValidationCheck:
        """
        性格匹配度检查（启发式）
        通过关键词匹配判断对话是否体现角色性格
        生产环境建议用 LLM 做二次评估
        """
        # 性格 → 正面关键词 / 反面关键词
        personality_keywords = {
            "冷静": {
                "positive": ["数据", "概率", "计算", "分析", "参数", "合理", "逻辑", "精确"],
                "negative": ["啊啊啊", "天哪", "完了完了", "救命"],
            },
            "理性": {
                "positive": ["根据", "依据", "推断", "判断", "评估", "考虑"],
                "negative": ["不管了", "随便", "爱怎样怎样"],
            },
            "果断": {
                "positive": ["执行", "现在", "马上", "立刻", "行动", "出发"],
                "negative": ["要不……", "我再想想", "可能也许大概"],
            },
            "务实": {
                "positive": ["结果", "效率", "实际", "方案", "可行"],
                "negative": ["如果当初", "要是能", "要是可以的话"],
            },
            "热血": {
                "positive": ["冲", "拼了", "不怕", "一起上", "绝不退缩"],
                "negative": ["算了", "放弃吧", "太危险了"],
            },
            "温柔": {
                "positive": ["没关系", "别担心", "大家", "一起", "小心"],
                "negative": ["滚", "闭嘴", "你懂什么"],
            },
        }

        scores = []
        for d in dialogues:
            char = next((c for c in self.context.active_characters if c.name == d.speaker), None)
            if not char:
                continue
            score = 1.0  # 默认满分
            for trait in personality_keywords:
                if trait in char.personality:
                    kw = personality_keywords[trait]
                    pos_hits = sum(1 for w in kw["positive"] if w in d.text)
                    neg_hits = sum(1 for w in kw["negative"] if w in d.text)
                    # 有正面词加分，有反面词扣分
                    trait_score = min(1.0, 0.5 + pos_hits * 0.2 - neg_hits * 0.3)
                    score = min(score, trait_score)
            scores.append(score)

        avg_score = sum(scores) / max(len(scores), 1)
        ok = avg_score >= 0.5  # 启发式阈值放宽，生产环境用 LLM 评

        return ValidationCheck(
            name="personality_match",
            passed=ok,
            severity="medium",
            detail=f"启发式匹配得分: {avg_score:.2f}",
            score=round(avg_score, 2),
        )

    def check_worldline_adherence(self, narration: str, dialogues: list[Dialogue]) -> ValidationCheck:
        """主线符合度检查（通用启发式，不绑定特定题材）"""
        all_text = narration + " " + " ".join(d.text for d in dialogues)

        # 检查是否包含当前节点和下一节点的相关关键词
        node_mentioned = 0
        total_nodes_checked = 0
        for node_name in [self.context.current_node, self.context.next_node]:
            if not node_name:
                continue
            total_nodes_checked += 1
            # 直接匹配节点名
            if node_name in all_text:
                node_mentioned += 1
                continue
            # 动态推导：将节点名拆分为 2-3 字子词做模糊匹配
            node_sub_kws = self._extract_keywords(node_name)
            if node_sub_kws and any(kw in all_text for kw in node_sub_kws):
                node_mentioned += 1
                continue
            # 检查场景描述关键词是否出现在节点名中
            scene_kws_for_node = self._extract_keywords(self.context.scene_description)
            if any(kw in node_name for kw in scene_kws_for_node if len(kw) >= 2):
                node_mentioned += 1

        # 检查场景描述中的核心关键词（2-4字的有意义词）
        scene_kws = self._extract_keywords(self.context.scene_description)
        scene_hits = sum(1 for kw in scene_kws if kw in all_text)
        # 用固定基准而非关键词总数，避免长描述稀释命中率
        scene_score = min(1.0, scene_hits / max(min(len(scene_kws), 8), 1))

        # 综合得分：场景关键词权重更高（通用架构下节点名精确匹配不可靠）
        node_score = min(1.0, node_mentioned / max(total_nodes_checked, 1))
        combined = scene_score * 0.7 + node_score * 0.3
        ok = combined >= 0.15  # 通用启发式阈值（AI 生成内容用词不一定与场景描述完全一致）

        return ValidationCheck(
            name="worldline_adherence",
            passed=ok,
            severity="high",
            detail=f"主线关联度: {combined:.2f} (节点匹配: {node_mentioned}, 场景词命中: {scene_hits}/{len(scene_kws)})",
            score=round(combined, 2),
        )

    def check_choice_diversity(self, choices: list[Choice]) -> ValidationCheck:
        """选择多样性检查"""
        if len(choices) < 2:
            return ValidationCheck(
                name="choice_diversity",
                passed=False,
                severity="high",
                detail="选择项不足 2 个",
            )

        # 检查效果描述不重复
        effects = [c.effect for c in choices]
        unique_effects = set(effects)
        ok = len(unique_effects) == len(effects)

        # 检查 impact 类型多样性
        impacts = {c.worldline_impact for c in choices}
        has_advance = any(i in impacts for i in ("advance", "advance_risky"))

        return ValidationCheck(
            name="choice_diversity",
            passed=ok and has_advance,
            severity="medium",
            detail=f"{len(unique_effects)} 个不同效果, {len(impacts)} 种影响类型",
        )

    def check_choice_validity(self, choices: list[Choice]) -> ValidationCheck:
        """选择项内容有效性"""
        issues = []
        for c in choices:
            if len(c.text) < 2:
                issues.append(f"选项过短: '{c.text}'")
            if c.worldline_impact and c.worldline_impact not in self.VALID_IMPACTS:
                issues.append(f"无效 impact: '{c.worldline_impact}'")

        return ValidationCheck(
            name="choice_validity",
            passed=len(issues) == 0,
            severity="high",
            detail="; ".join(issues) if issues else "全部有效",
        )

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """
        提取关键词：标点分割取有意义词块，限制总量避免稀释命中率。
        策略：优先取 2-4 字的标点分割词块，不足时补充 n-gram。
        """
        import re
        stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "他",
            "中", "里", "内", "外", "上", "下", "她", "它", "们", "那",
            "被", "把", "还", "已", "而", "但", "与", "对", "从", "等",
        }
        # 标点分割
        raw_tokens = re.split(r'[，。！？、\s,.!?\n]+', text)
        primary_kws = []
        for t in raw_tokens:
            t = t.strip()
            if not t or t in stopwords or len(t) < 2:
                continue
            # 取 2-4 字的词块
            if len(t) <= 4:
                primary_kws.append(t)
            else:
                # 长词取前 4 字 + 后 2 字作为代表
                primary_kws.append(t[:4])
                if len(t) > 4:
                    primary_kws.append(t[-2:])
    
        # 去重保持顺序
        seen = set()
        result = []
        for kw in primary_kws:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
    
        # 如果不足 5 个，补充 2-gram
        if len(result) < 5:
            chars = text.replace(" ", "")
            for i in range(len(chars) - 1):
                gram = chars[i:i + 2]
                if gram not in stopwords and gram not in seen:
                    seen.add(gram)
                    result.append(gram)
                if len(result) >= 20:
                    break
    
        return result[:15]  # 最多返回 15 个关键词，避免长文本稀释命中率
