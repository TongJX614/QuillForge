# 选择分支生成提示词模板
# 变量用 {{variable}} 标记，运行时替换

请根据以下已生成的叙事内容，生成 2-4 个有意义的后续选择。

## 完整场景

### 旁白
{{narration}}

### 对话
{{#each dialogues}}
{{this.speaker}}：{{this.text}}
{{/each}}

## 世界线信息

- 世界线：{{worldline}}
- 当前节点：{{currentNode}}
- 下一节点：{{nextNode}}
- 主线进度：{{worldlineProgress}}%

## 选择生成规则

1. 生成 2-4 个选择
2. 每个选择必须有 text（选项描述，5-20字）和 effect（预期效果，5-15字）
3. 选择之间的差异要求：
   - 至少一个选择直接推进主线（向世界线下一节点）
   - 至少一个选择是"高风险高回报"路线
   - 可以有一个"探索/收集信息"的侧面选项
4. 选项间不得有包含关系（一个选项不能是另一个的子集）
5. 每个选择都应让玩家觉得"有道理"
6. 避免明显的"好选择 vs 坏选择"，每个选项都有代价

## 世界线影响标记

为每个选择标记 worldlineImpact：
- `advance` — 直接推进到下一世界线节点
- `advance_risky` — 推进主线但伴随风险
- `stay_current` — 停留在当前节点（解决当前问题）
- `side_branch` — 探索支线内容（最终会回到主线）

## 输出格式

输出 JSON 数组：

```json
[
  {
    "text": "选项描述",
    "effect": "预期效果描述",
    "worldlineImpact": "advance|advance_risky|stay_current|side_branch"
  }
]
```

请直接输出 JSON 数组。
