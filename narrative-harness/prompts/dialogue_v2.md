# 叙事生成引擎 — 对话生成
# 此模板用于生成角色对话

请根据以下场景和角色信息，生成 {{dialogueCount}} 轮角色对话。

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

{{#if playerDecision}}
## 玩家刚做出的选择
{{playerDecision}}
角色应对此选择做出反应（反应方式必须符合各自性格）
{{/if}}

{{#if previousDialogues}}
## 上一轮对话（必须衔接）
{{#each previousDialogues}}
{{this.speaker}}：{{this.text}}
{{/each}}
{{/if}}

## 硬性规则
1. 必须生成 {{minDialogues}}-{{maxDialogues}} 轮对话（不多不少）
2. 每轮必须有 speaker 和 text
3. 同一角色不得连续发言超过2轮
4. 每句话都必须有强烈的角色辨识度——遮住 speaker 名字也能猜出是谁说的
5. 禁止废话、客套话、重复信息
6. 最后一轮必须留下悬念或决策点
7. 对话内容必须服务于世界线推进

## 输出格式
输出 JSON 数组，严格遵循以下格式：
```json
[
  {"speaker": "角色姓名", "text": "对话内容", "emotion": "情绪标签"}
]
```

emotion 只能是以下中文值之一：平静、急切、分析、果断、担忧、愤怒、希望、好奇、沉着、讽刺、恐惧、坚定

直接输出 JSON 数组，不要添加任何其他文字。
