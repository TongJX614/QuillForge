# 叙事生成引擎 — 场景旁白生成
# 此模板用于生成场景描写（不含对话）

请根据以下信息生成一段场景旁白。**生成的内容必须深度贴合剧本数据，不得自由发挥。**

{{#if worldbookRules}}
## ⚠ 世界书约束（最高优先级，必须遵守）
{{worldbookRules}}
任何违反世界书设定的内容都将被视为生成失败。
{{/if}}

{{#if themes}}
## 故事主题（旁白应体现主题内核）
{{themes}}
{{/if}}

## 世界线状态
- 大纲：{{worldline}}
- 当前进度：{{annotatedWorldline}}
  （✓=已完成 【】=当前位置 ○=未到）
- 当前节点：{{currentNode}}（第 {{nodeIndex}}/{{totalNodes}} 个）
- 前一节点：{{previousNode}}
- 下一节点：{{nextNode}}
- 进度：{{worldlineProgress}}%

## 场景描述
{{sceneDescription}}

## 在场角色
{{#each characters}}
- {{this.name}}（{{this.role}}）— 性格：{{this.personality}}
  说话风格：{{this.speakingStyle}}
  背景：{{this.background}}
  动机：{{this.motivation}}
  关系：{{this.relationships}}
  外貌：{{this.appearance}}
  成长弧线：{{this.arc}}
{{/each}}

{{#if choiceHistory}}
## ⚠ 玩家的完整选择历史（必须体现这些选择的累积影响）
{{choiceHistory}}
旁白必须体现玩家此前每个选择的累积后果，而非仅当前选择。特别是：
- 如果玩家此前选择了谨慎路线，旁白应暗示信息不完整
- 如果玩家此前选择了深入路线，旁白应暗示掌握更多真相
- 如果玩家此前选择了对抗路线，旁白应暗示处境更危险
{{/if}}

{{#if playerDecision}}
## 玩家刚做出的选择
{{playerDecision}}
请在旁白中体现这个选择的即时影响。
{{/if}}

{{#if currentStageContext}}
## ⚠ 当前剧情阶段要求（必须遵循）
{{currentStageContext}}
旁白必须完整体现本阶段的关键事件和情感弧线走向，这是剧本的核心脉络。
{{/if}}

{{#if sceneBeats}}
## ⚠ 本场景剧情节拍（必须体现）
{{sceneBeats}}
旁白必须覆盖以上每个节拍的叙事目的，不得遗漏。
{{/if}}

{{#if sceneHooks}}
## 剧情钩子（应在旁白中埋下伏笔）
{{sceneHooks}}
{{/if}}

{{#if stagesOverview}}
## 剧情阶段总览
{{stagesOverview}}
{{/if}}

{{#if plotSummary}}
## 故事概要
{{plotSummary}}
{{/if}}

{{#if coreConflict}}
## 核心冲突
{{coreConflict}}
{{/if}}

{{#if events}}
## 关键事件
{{events}}
{{/if}}

{{#if sceneDetails}}
## 当前场景详细信息
{{sceneDetails}}
必须严格贴合以上场景设定，充分利用场景描述、氛围和叙事指导来构建场景。
{{/if}}

{{#if worldlines}}
## 世界线结构（分支路径/汇聚点/规则）
{{worldlines}}
旁白应体现当前世界线位置，为可能的分支走向做铺垫。
{{/if}}

{{#if relationshipNetwork}}
## 角色关系网络
{{relationshipNetwork}}
旁白应体现角色间的关系张力，利用关系变化来增强叙事深度。
{{/if}}

{{#if previousNarration}}
## 上一段旁白（仅供衔接参考，不得延续上一场景的叙事）
{{previousNarration}}
⚠ 注意：这是上一个场景的旁白。你正在生成的是**新场景**的旁白，
必须以当前场景的地点、时间、氛围为起点，不得继续上一场景的情节。
{{/if}}

## 生成要求
1. **字数：严格 150-250 字，不得超过 250 字。** 宁可少写一个细节也不要超字数。
2. 第二人称叙事（“你”的视角）
3. **这是新场景的开场旁白**——必须以当前场景的 location、time、atmosphere 为起点开始描写，
   不得延续上一场景的叙事内容。即使有上一段旁白，也只做背景衔接而非情节继续。
4. 用环境描写和氛围渲染开场，但不要堆砌形容词
5. 内容紧扣当前节点「{{currentNode}}」，并向「{{nextNode}}」推进
6. **必须融入上方标注为「⚠ 必须遵循」或「⚠ 必须体现」的剧本数据**——用最精炼的语言融入，而非逐条展开
7. 如有玩家决策，用 1-2 句体现其即时后果
8. 结尾用 1 句自然过渡到角色互动（留白给对话）
9. **禁止出现角色对话**（对话由下一步生成）
10. 禁止超自然元素、时间旅行、万能解决方案（除非世界书明确允许）
11. **禁止引入剧本中不存在的地点、角色或重大事件**
12. **这是场景引子而非全文，点到即止。留出空间给对话来展开故事。**
13. 旁白中应自然提及当前世界线节点「{{currentNode}}」相关的关键词，
    让生成内容与世界线产生语义关联。

请直接输出旁白文本，不要添加标题、标记或解释。
