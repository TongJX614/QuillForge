# -*- coding: utf-8 -*-
"""
内容安全审查模块 — 三层防护

1. `scan_input()`：拦截必然触发供应商审核的用户输入（剧本描述 / 实时生成描述），
   返回带分类名的中文提示，供路由层直接抛 HTTPException(400) 展示给前端。
2. `soften_image_prompt()`：生图提示词只做视觉软化替换，不拦截。
   虚构叙事里的打斗/流血/死亡是合法剧情，但图像模型会按像素描述审核，
   因此把露骨视觉词替换成同等氛围、可安全绘制的表述。
   未命中任何替换规则时原样返回，保证磁盘缓存 key（md5(model|size|prompt)）不变。
3. `moderation_error_or_none()`：把供应商返回的内容审核报错翻译成友好中文，
   避免上层把它当成"生成失败"这种无信息量的错误。

设计原则：**高置信、低误伤**。判定用"共现/疑问式 how-to"而不是单个敏感字，
所以"命案/凶手/尸体/复仇"这类正常悬疑剧情不会被拦；政治、软色情等灰色地带
交给 Prompt 里的【内容安全】规则与第 3 层报错翻译处理，不做输入硬拦截。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import logging
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 第 1 层：用户输入拦截
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class BlockRule:
    """一条拦截规则。

    keywords: 命中任意一个正则即拦截（自身已足够露骨/危险）。
    cooccur:  两组正则必须同时命中才拦截（用于"未成年人 + 性描写"这类组合风险）。
    """
    id: str
    label: str
    keywords: tuple[str, ...] = ()
    cooccur: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    hint: str = ""


BLOCK_RULES: tuple[BlockRule, ...] = (
    BlockRule(
        id="minors_sexual",
        label="未成年人性内容",
        cooccur=(
            ("未成年|未成年人|萝莉|正太|幼女|幼童|男童|女童|儿童|小学生|初中生|"
             r"高中生|小男孩|小女孩|少年|少女|18岁以下|未满\s*18"),
            ("裸|脱衣|扒光|走光|裙底|内裤|上床|床照|性爱|性行为|性关系|发生性|啪啪|呻吟|情趣|陪睡|包养|"
             "下体|私处|生殖器|插入|舔|摸胸|性奴|调教|侵犯|睡了她|玷污|侵害"),
        ),
        hint="请勿涉及未成年角色的性暗示或亲密描写",
    ),
    BlockRule(
        id="sexual",
        label="色情低俗",
        keywords=(
            "色情|黄文|黄图|黄片|黄游|春宫|裸聊|约炮|嫖娼|卖淫|招妓|肉便器|"
            "做爱|性爱|口交|自慰|射精|精液|潮吹|乳交|肛交|内射|"
            "阴茎|阴道|阴蒂|乳头|巨乳|爆乳|鸡巴|下体|私处|生殖器|裸舞|全裸|一丝不挂|"
            "无码|无打码|成人影片|AV女优|群交|换妻|性派对|性交易|迷奸|轮奸|强奸|"
            r"porn|pornhub|hentai|creampie|cumshot|gangbang|bareback|nsfw|onlyfans|"
            r"(?<![a-z0-9])3p(?![a-z0-9])",
        ),
        hint="请改为不含露骨性描写的表述",
    ),
    BlockRule(
        id="selfharm",
        label="自杀自残方法",
        keywords=(
            r"(自杀|自残|轻生|寻死|割腕|上吊|服毒|烧炭|跳楼).{0,10}(方法|方式|教程|指南|攻略|步骤|才不痛|无痛|痛苦最小)",
            r"(如何|怎么|怎样|用什么|什么药).{0,10}(自杀|自残|割腕|上吊|服毒|烧炭|结束生命|解脱)",
            "致死量|致命剂量|中毒剂量",
        ),
        hint="可以写角色的痛苦与挣扎，但不要涉及具体自伤方法与剂量",
    ),
    BlockRule(
        id="drugs",
        label="毒品制作方法",
        keywords=(
            r"(冰毒|海洛因|摇头丸|麻古|氯胺酮|K粉|卡西酮|氟胺酮|致幻剂|迷幻药|LSD|"
            "大麻|吗啡|芬太尼|毒品).{0,10}(制作|配方|合成|提炼|教程|方法|步骤|种植|购买渠道)",
            r"(制作|合成|提炼|配方|教程).{0,10}(冰毒|海洛因|摇头丸|毒品|致幻剂|迷药|听话水)",
            "制毒|毒贩教程|走私毒品方法",
        ),
        hint="可以写毒品带来的冲突与后果，但不要涉及制法与配方",
    ),
    BlockRule(
        id="weapons",
        label="爆炸物与武器制作",
        keywords=(
            r"(炸弹|炸药|爆炸物|燃烧弹|汽油弹|凝固汽油|地雷|诡雷|土制枪|气枪|"
            "射钉枪|消音器|弓弩|电击器|C4|TNT|硝化甘油|雷酸|雷管|黑火药|烟火药|"
            "VX|沙林|芥子气|蓖麻毒素|炭疽).{0,12}(制作|配方|制法|教程|方法|步骤|图纸|材料|如何|怎么)",
            r"(制作|配方|制法|教程|图纸).{0,12}(炸弹|炸药|燃烧弹|地雷|诡雷|C4|TNT|"
            "硝化甘油|毒气|神经毒剂)",
            r"(如何|怎么).{0,8}(开车|用车|货车).{0,6}(撞|碾).{0,4}(人|人群|学生)",
            "车辆冲撞教程|在家自制(炸弹|炸药|枪支|毒气)",
        ),
        hint="虚构武器可以出现，但不要索取真实制作方法与配方",
    ),
    BlockRule(
        id="hate_violence",
        label="现实暴力煽动",
        keywords=(
            r"(种族|民族|宗教|教徒|信徒|穆斯林|基督徒|犹太人|少数族裔|外地人|外省人|"
            r"难民|民工|残疾人|同性恋|女权|某地人|特定群体).{0,12}"
            r"(杀光|屠杀|灭掉|清除|清理|毒打|烧死|炸死|砍死|绞死|虐待|灭绝)",
            r"(杀光|屠杀|灭掉|清除|炸死|砍死|烧死|毒打).{0,12}"
            r"(种族|民族|宗教|教徒|同性恋|女权|难民|外地人|某地人)",
            r"(报复社会).{0,12}(砍|杀|伤人|袭击|投毒|纵火|驾车)",
            "无差别(杀人|砍人|伤人|袭击|报复)",
            r"(号召|招募|组建).{0,8}(杀手|打手|私刑|行刑队)",
        ),
        hint="故事冲突请落在虚构角色之间，不要针对现实群体或真实场景煽动暴力",
    ),
)

_COMPILED: list[tuple[BlockRule, list[re.Pattern], list[re.Pattern]]] = []
for _rule in BLOCK_RULES:
    _kw = [re.compile(p, re.I) for p in _rule.keywords]
    _cc = ([re.compile(p, re.I) for p in _rule.cooccur] if _rule.cooccur else [])
    _COMPILED.append((_rule, _kw, _cc))


def _normalize(text: str) -> str:
    """匹配前的归一化：转小写、去掉空白（让 .{0,N} 邻近判定不受换行影响）。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


@dataclass
class GuardVerdict:
    """一次输入审查的结论。"""
    blocked: bool = False
    labels: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "categories": self.labels,
            "rule_ids": self.rule_ids,
            "message": self.message,
        }


def scan_input(text: str, *, source: str = "user_prompt") -> GuardVerdict:
    """审查用户输入，命中拦截规则时返回分类名与可直接展示的中文提示。"""
    norm = _normalize(text)
    verdict = GuardVerdict()
    if not norm:
        return verdict

    for rule, patterns, cooccur in _COMPILED:
        hit = any(p.search(norm) for p in patterns)
        if not hit and cooccur:
            hit = all(p.search(norm) for p in cooccur)
        if not hit:
            continue
        verdict.blocked = True
        if rule.label not in verdict.labels:
            verdict.labels.append(rule.label)
            verdict.rule_ids.append(rule.id)
            if rule.hint:
                verdict.hints.append(rule.hint)

    if verdict.blocked:
        logger.info("[content_guard] 拦截输入（%s）: %s", source, ",".join(verdict.rule_ids))
        verdict.message = (
            f"您输入的描述包含不支持的内容（{'、'.join(verdict.labels)}），"
            "请调整后重试"
        )
    return verdict


# ═══════════════════════════════════════════════════════
# 第 2 层：生图提示词视觉软化
# ═══════════════════════════════════════════════════════

# (正则, 替换) —— 只替换"图像模型会按像素审核"的露骨视觉词，保留氛围与构图信息
SOFTEN_RULES: tuple[tuple[str, str], ...] = (
    # 裸露 / 性暗示
    (r"全裸|裸体|裸身|赤裸|一丝不挂|未着寸缕|不穿衣服|衣物散落于地|宽衣解带|扒光|脱衣",
     "衣着完整"),
    (r"情趣内衣|情趣制服|蕾丝内衣|性感内衣|暴露的?服装|暴露的?穿着|真空制服",
     "居家服饰"),
    (r"胸部特写|巨乳|爆乳|乳头|乳沟|下体特写|私处|生殖器|高潮脸|湿身诱惑",
     "面部与上半身构图"),
    (r"走光|裙底|仰角偷拍|偷窥",
     "正常视角"),
    (r"性爱|做爱|口交|自慰|精液|裸照",
     "亲密互动"),
    (r"强奸|迷奸|轮奸|性侵|侵犯她|玷污",
     "冲突场面"),
    # 血腥 / 尸体
    (r"血肉模糊|鲜血淋漓|血流成河|遍地鲜血|血液四溅|血泊|满身是血|伤口渗血",
     "光影昏暗"),
    (r"断肢|残肢|肢体残破|碎尸|尸块|分尸|开膛|内脏外露|肠子|腐肉|蛆",
     "战斗后的痕迹"),
    (r"尸体|尸首|腐尸|吊死的?[人尸]|上吊的?[人尸]|倒下的?[尸]?体",
     "静置的人物剪影"),
    (r"砍头示众|斩首示众|悬首示众|砍头|斩首|爆头|开脑|脑浆|眼珠|割舌|剜眼|凌迟|酷刑|虐杀|虐待致死",
     "阴森氛围"),
    # 自伤 / 毒品
    (r"割腕|自残|服毒|吞药|注射毒品|针头扎入|吸毒|毒品|海洛因|冰毒|致幻剂",
     "药瓶与药柜"),
    # 现实武器与政治符号
    (r"自制炸弹|土制炸弹|爆炸装置|炸药包|雷管|硝化甘油",
     "远处火光"),
    (r"游行|抗议|示威|暴动|骚乱|横幅标语|旗帜林立",
     "人群聚集"),
)

_COMPILED_SOFTEN: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.I), r) for p, r in SOFTEN_RULES
]


def soften_image_prompt(prompt: str) -> str:
    """软化生图提示词中的露骨视觉描述；未命中时原样返回（保持缓存 key 不变）。"""
    text = str(prompt or "")
    if not text:
        return text
    softened = text
    replaced: list[str] = []
    for pattern, repl in _COMPILED_SOFTEN:
        if pattern.search(softened):
            replaced.append(pattern.pattern)
            softened = pattern.sub(repl, softened)
    if softened != text:
        logger.info("[content_guard] 生图提示词已软化 %d 处: %s",
                    len(replaced), softened[:120])
    return softened


# ═══════════════════════════════════════════════════════
# 第 3 层：供应商内容审核报错翻译
# ═══════════════════════════════════════════════════════

class ContentSafetyError(RuntimeError):
    """供应商内容审核拒绝 / 输入被本地拦截时抛出，message 可直接展示给前端。"""

    def __init__(self, message: str, *, provider_text: str = ""):
        super().__init__(message)
        self.message = message
        self.provider_text = provider_text


FRIENDLY_MODERATION = (
    "本次生成被模型服务的内容审核拦截（可能包含色情低俗、血腥暴力或危险行为描述），"
    "请调整描述后重试"
)

# 各 OpenAI 兼容供应商的审核报错指纹
# DeepSeek: "Content filtering" / 智谱 code=1301 "内容审核不通过" /
# 通义 Azure: DataInspectionFailed / OpenAI: content_policy / 硅基流动: sensitive
_MODERATION_MARKERS: tuple[str, ...] = (
    "content policy", "content_policy", "content filtering", "content_filter",
    "datainspectionfailed", "content_moderation", "responsible ai",
    "prohibited_content", "sensitive words", "sensitive information",
    "risk control", "内容审核", "审核不通过", "敏感信息", "敏感词", "违规内容",
    "违反", "内容安全", "风控", "不符合我们的内容政策", "1301",
)


def _exc_text(exc: BaseException) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    code = getattr(exc, "code", None)
    if code is not None:
        parts.append(str(code))
    return " ".join(parts).lower()


def is_moderation_error(exc: BaseException) -> bool:
    """判断异常是否为供应商内容审核拒绝（而非网络/额度/参数错误）。"""
    status = getattr(exc, "status_code", None)
    if status is not None and status not in (400, 403, 422, 451):
        return False
    text = _exc_text(exc)
    return any(m in text for m in _MODERATION_MARKERS)


def moderation_error_or_none(exc: BaseException) -> ContentSafetyError | None:
    """把审核报错包成 ContentSafetyError；不是审核错误时返回 None（调用方按原逻辑处理）。"""
    if not is_moderation_error(exc):
        return None
    text = _exc_text(exc)
    logger.info("[content_guard] 供应商内容审核拦截: %s", text[:200])
    return ContentSafetyError(FRIENDLY_MODERATION, provider_text=text[:500])
