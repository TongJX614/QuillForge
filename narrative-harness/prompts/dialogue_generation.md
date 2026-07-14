# 对话生成提示词模板
# 变量用 {{variable}} 标记，运行时替换

请根据以下信息生成 {{dialogueCount}} 轮角色对话。

## 场景上下文

{{narration}}

## 在场角色

{{#each characters}}
### {{this.name}}（{{this.role}}）
- 性格标签：{{this.personality}}
- 说话风格：{{#if this.speakingStyle}}{{this.speakingStyle}}{{else}}根据性格自行把握{{/if}}
- 当前情绪：{{#if this.mood}}{{this.mood}}{{else}}根据场景推断{{/if}}
{{/each}}

## 世界线方向

- 当前节点：{{currentNode}}
- 叙事应朝向下个节点「{{nextNode}}」推进
- 对话结尾应自然引出一个需要做出选择的决策点

## 玩家决策影响

{{#if playerDecision}}
玩家已选择：「{{playerDecision}}」
角色应对此选择做出反应（赞同/反对/担忧/执行等），反应方式需符合各自性格。
{{/if}}

{{#if previousDialogues}}
## 上一轮对话（衔接参考）

{{#each previousDialogues}}
{{this.speaker}}：{{this.text}}
{{/each}}

请确保本轮对话与上一轮自然衔接，角色情绪状态合理延续或转变。
{{/if}}

## 对话生成规则

1. 生成 {{dialogueCount}} 轮对话（{{minDialogues}}-{{maxDialogues}} 轮）
2. 每轮必须有 speaker 和 text
3. 同一角色不得连续发言超过 2 轮
4. 对话要口语化，有个性，有信息量
5. 冷静理性的角色 → 用数据/逻辑说话，句式精准
6. 果断务实的角色 → 短句，命令式，重结果
7. 每轮对话都推动情节发展，不要有"废话轮"
8. 最后一轮应自然过渡到决策点
9. 对话长度参考：紧张场景 5-15 字/轮，讨论场景 15-40 字/轮

## 输出格式

输出 JSON 数组，每个元素包含 speaker、text、emotion：

```json
[
  { "speaker": "角色名", "text": "对话内容", "emotion": "情绪标签" }
]
```

emotion 可选值：calm, urgent, analytical, decisive, worried, angry, hopeful, curious, composed, sarcastic

请直接输出 JSON 数组。
