# 叙事生成引擎 — 对话生成
# 此模板用于生成角色对话

请根据以下场景和角色信息，生成至少 {{minDialogues}} 轮角色对话（建议 {{dialogueCount}} 轮左右，场景复杂时可适当增多）。
并在对话之间**适度穿插**角色的动作/神态/心理描写（以旁白形式呈现，增强演出感）。

{{#if worldbookRules}}
## ⚠ 世界书约束（最高优先级，必须遵守）
{{worldbookRules}}
对话内容不得违反世界书设定。禁止引入超自然元素、与世界观矛盾的科技、或世界书明确禁止的内容。
{{/if}}

{{#if themes}}
## 故事主题（对话应自然体现主题内核）
{{themes}}
{{/if}}

{{#if plotSummary}}
## 故事概要（对话必须服务于故事主线）
{{plotSummary}}
{{/if}}

## 场景旁白（刚刚生成的）
{{narration}}

## 在场角色（含详细人设）
{{#each characters}}
### {{this.name}}
- 身份：{{this.role}}
- 性格：{{this.personality}}
- 说话风格：{{this.speakingStyle}}
- 背景故事：{{this.background}}
- 动机：{{this.motivation}}
- 秘密：{{this.secrets}}
- 关系：{{this.relationships}}
- 外貌：{{this.appearance}}
- 成长弧线：{{this.arc}}
{{/each}}

## 世界线方向
- 当前进度：{{annotatedWorldline}}
- 当前节点：{{currentNode}}
- 目标方向：朝向「{{nextNode}}」推进
- 对话必须服务于当前节点的剧情发展，不得引入与世界线无关的内容
- 对话结尾必须自然引出一个需要玩家做出选择的决策点

{{#if currentStageContext}}
## ⚠ 当前剧情阶段要求（必须遵循）
{{currentStageContext}}
对话必须完整体现本阶段的关键事件和情感弧线走向。
{{/if}}

{{#if sceneBeats}}
## ⚠ 本场景剧情节拍（必须体现）
{{sceneBeats}}
对话必须覆盖以上每个节拍的叙事目的，不得遗漏。
{{/if}}

{{#if sceneHooks}}
## 剧情钩子（应在对话中埋下伏笔）
{{sceneHooks}}
{{/if}}

{{#if stagesOverview}}
## 剧情阶段总览
{{stagesOverview}}
{{/if}}

{{#if coreConflict}}
## 核心冲突
{{coreConflict}}
对话应围绕核心冲突展开，体现角色在冲突中的立场与态度。
{{/if}}

{{#if events}}
## 关键事件（对话中可自然提及）
{{events}}
{{/if}}

{{#if relationshipNetwork}}
## 角色关系网络
{{relationshipNetwork}}
{{/if}}

{{#if sceneDetails}}
## 当前场景设定
{{sceneDetails}}
对话必须符合场景氛围，充分利用场景描述中的具体细节。
{{/if}}

{{#if worldlines}}
## 世界线结构
{{worldlines}}
对话应为当前世界线位置服务，并为分支走向埋下伏笔。
{{/if}}

{{#if keyChoices}}
## 剧本中的关键选择（对话应为这些选择做铺垫）
{{keyChoices}}
{{/if}}

{{#if endings}}
## 结局系统（对话应朝向不同结局方向引导）
{{endings}}
{{/if}}

{{#if choiceHistory}}
## ⚠ 玩家的完整选择历史（对话必须体现这些选择的累积影响）
{{choiceHistory}}
对话中角色的态度和行为必须反映玩家此前的选择路径。例如：
- 如果玩家选择了谨慎路线，角色可能掌握的信息更少
- 如果玩家选择了深入路线，角色应知道更多真相
- 如果玩家选择了对抗路线，角色关系应更紧张
{{/if}}

{{#if playerDecision}}
## 玩家刚做出的选择
{{playerDecision}}
角色应对此选择做出反应（反应方式必须符合各自性格）
{{/if}}

{{#if previousDialogues}}
## 上一场景的对话（仅供衔接参考）
{{#each previousDialogues}}
{{this.speaker}}：{{this.text}}
{{/each}}
⚠ 注意：以上是**上一场景**的对话。你正在生成的是**当前新场景**的对话，
必须以当前场景的旁白为起点展开新对话，不得延续上一场景的情节和话题。
{{/if}}

## 硬性规则
1. 必须生成 {{minDialogues}}-{{maxDialogues}} 轮**角色对话**（动作/心理旁白不计入对话轮数）
2. 每轮对话必须有 speaker 和 text
3. **对话的 speaker 必须是上方「在场角色」中列出的角色中文姓名！**
   合法的 speaker 值为：
{{#each characters}}   - "{{this.name}}"
{{/each}}
   **严禁使用角色ID（如 su_ran、lin_xiao、du_weiming）、英文名、拼音、空字符串作为 speaker！**
   **严禁使用「系统」「旁白」「叙述者」或任何其他非角色名称作为 speaker！**
4. 同一角色不得连续发言超过2轮
5. 每句话都必须有强烈的角色辨识度——遮住 speaker 名字也能猜出是谁说的
6. 禁止废话、客套话、重复信息
7. 最后一轮必须留下悬念或决策点
8. 对话内容必须服务于世界线推进
9. **这是当前场景的对话，必须围绕当前场景的地点、时间、氛围展开，
   不得延续上一场景的情节或话题。即使有上一轮对话参考，也只用于了解背景，
   不继续上一场景的对话流。**
10. **每个角色的对话必须严格遵循其说话风格（speakingStyle）**：
    如果角色设定为「语速适中偏快、用词精准、不喜欢废话」，则该角色对话应简短精炼；
    如果角色设定为「习惯用'我们'而非'我'」，则该角色必须使用「我们」；
    如果角色设定为「声音柔和、语速稳定」，则该角色对话应体现这些特征。
11. **每个在场角色都应有充分的对话参与**——不得让某个角色仅在最后出场1轮，
    所有在场角色应在对话中均衡互动（至少2轮以上）。

## 动作/心理旁白规则（穿插在对话之间）
1. 约每 2-4 句对话穿插 1 条动作/心理旁白，跟着对话节奏走，不要每句都加也不要完全没有
2. 旁白用**第三人称**描写某个在场角色的动作、神态、肢体反应或心理活动
3. 旁白不是对话，**不要加引号、不要写成角色说的话**；心理活动也用叙述口吻（可用“心想、暗自、意识到”等）
4. 旁白的 subject 必须是在场角色姓名（即该动作/心理所属的角色）
5. 每条旁白简洁，1 句为宜

## 输出格式
输出 JSON 数组，**对话对象与动作旁白对象交替排列**，严格遵循以下格式：
```json
[
  {"speaker": "角色姓名", "text": "对话内容", "emotion": "情绪标签"},
  {"type": "action", "subject": "角色姓名", "text": "该角色的动作/神态/心理描写"},
  {"speaker": "角色姓名", "text": "对话内容", "emotion": "情绪标签"}
]
```

- 对话对象：speaker / text / emotion 三个字段。
- 动作旁白对象：type 固定为 "action"，subject 为角色姓名，text 为描写内容（无 speaker/emotion 字段）。

emotion 只能是以下中文值之一：平静、急切、分析、果断、担忧、愤怒、希望、好奇、沉着、讽刺、恐惧、坚定

直接输出 JSON 数组，不要添加任何其他文字。
