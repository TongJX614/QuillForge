# 场景组装提示词模板
# 变量用 {{variable}} 标记，运行时替换

你是一个互动叙事生成引擎。请根据以下场景信息生成一段场景描写（旁白）。

## 当前世界线位置

- 世界线大纲：{{worldline}}
- 当前节点：{{currentNode}}（第 {{nodeIndex}}/{{totalNodes}} 个节点）
- 前一节点：{{previousNode}}
- 下一节点：{{nextNode}}
- 主线进度：{{worldlineProgress}}%

## 当前场景

{{sceneDescription}}

## 在场角色

{{#each characters}}
### {{this.name}}
- 身份：{{this.role}}
- 性格：{{this.personality}}
{{#if this.speakingStyle}}- 说话风格：{{this.speakingStyle}}{{/if}}
{{#if this.mood}}- 当前情绪：{{this.mood}}{{/if}}
{{/each}}

## 玩家决策

{{#if playerDecision}}
玩家已选择：{{playerDecision}}
请在叙事中体现这一选择的即时影响。
{{else}}
本场景尚无玩家决策，叙事应为后续选择做铺垫。
{{/if}}

{{#if previousNarration}}
## 上一场景叙事（用于衔接）

{{previousNarration}}
请确保本场景叙事与上一场景自然衔接。
{{/if}}

## 生成要求

1. 生成 100-300 字的场景描写
2. 使用第三人称文学化叙事
3. 必须包含环境描写和气氛渲染
4. 必须暗示当前世界线节点的叙事方向
5. 如有玩家决策，描写其即时影响
6. 不要包含角色对话（对话由后续步骤生成）
7. 结尾应自然过渡到角色互动的起点

请直接输出场景描写文本，不要添加标题或说明。
