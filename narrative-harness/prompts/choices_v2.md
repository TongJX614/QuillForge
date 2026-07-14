# 叙事生成引擎 — 选择分支生成

请根据当前场景内容，生成 2-4 个有意义的玩家选择。

## 场景旁白
{{narration}}

## 对话内容
{{#each dialogues}}
{{this.speaker}}：{{this.text}}
{{/each}}

## 世界线状态
- 大纲：{{worldline}}
- 当前节点：{{currentNode}}
- 下一节点：{{nextNode}}
- 进度：{{worldlineProgress}}%

{{#if coreConflict}}
## 核心冲突
{{coreConflict}}
选择应围绕核心冲突设计，让玩家在不同立场间徽徫。
{{/if}}

{{#if keyChoices}}
## 剧本中的关键选择（参考，生成的选择应与之风格一致）
{{keyChoices}}
{{/if}}

{{#if endings}}
## 结局系统（选择应朝向不同结局方向引导）
{{endings}}
{{/if}}

{{#if sceneDetails}}
## 当前场景设定
{{sceneDetails}}
选择必须基于当前场景的具体内容设计，利用场景中的互动和分支点。
{{/if}}

{{#if worldlines}}
## 世界线结构（分支路径/汇聚点/规则）
{{worldlines}}
选择应尊重世界线的分支逻辑和规则约束。
{{/if}}

## 选择设计规则
1. 生成 2-4 个选项
2. 每个选项包含：text（选项描述 5-20字）和 effect（后果描述 5-15字）
3. 选项间必须有实质差异，禁止"换皮"选项
4. 至少一个选项直接推进主线到下一节点
5. 至少一个选项有风险但有更大回报
6. 每个选项都有代价——不存在完美选择
7. 选项描述要简洁有力，让玩家能快速理解后果

## 世界线影响标记
为每个选项标记 worldlineImpact：
- advance：直接推进到下一世界线节点
- advance_risky：推进主线但伴随风险或代价
- stay_current：停留在当前节点处理问题
- side_branch：探索支线内容（最终回到主线）

## 输出格式
输出 JSON 数组：
```json
[
  {"text": "选项描述", "effect": "后果描述", "worldlineImpact": "impact类型"}
]
```

直接输出 JSON 数组，不要添加任何其他文字。
