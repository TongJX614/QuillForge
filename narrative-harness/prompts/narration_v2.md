# 叙事生成引擎 — 场景旁白生成
# 此模板用于生成场景描写（不含对话）

请根据以下信息生成一段场景旁白。

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

{{#if playerDecision}}
## 玩家刚做出的选择
{{playerDecision}}
请在旁白中体现这个选择的即时影响。
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

{{#if previousNarration}}
## 上一段旁白（衔接用）
{{previousNarration}}
{{/if}}

## 生成要求
1. 字数：150-300字
2. 第二人称叙事（“你”的视角）
3. 必须包含环境描写和氛围渲染
4. 内容必须紧扣当前节点「{{currentNode}}」，并向「{{nextNode}}」推进，不得偏离世界线
5. 如有玩家决策，描写其即时后果
6. 结尾必须自然过渡到角色互动的起点
7. 禁止出现角色对话（对话由下一步生成）
8. 禁止出现超自然元素、时间旅行、万能解决方案

请直接输出旁白文本，不要添加标题、标记或解释。
