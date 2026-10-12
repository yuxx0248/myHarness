---
name: author-skill
description: 新建或修改一个 skill（Claude Code / Codex 通用的 SKILL.md）。触发："写个 skill"、"把这个做成 skill"、"skill 没触发"、"改一下 xxx skill"、"这个流程以后要复用"。按我的 skill 约定：薄索引、关键词堆 description、负范围、固定步骤骨架、输出契约、写前 DIFF、案例库回链、跨 runtime 放 ~/.agents/skills。
---

# author-skill

约定见 `~/.agents/framework/playbooks/skill-authoring.md`。

## 步骤
1. **先找现成的**：`ls ~/.agents/skills ~/.claude/skills ~/.codex/skills`，有相近的就改它，不新建。
2. **定类型**：路由 / 分诊器 · 领域 SOP 索引 · 工作流 · 报告 · 运维触发。分诊器只交棒，不排查。
3. **重内容放 KB**，skill 只放步骤和指针；大文档按章节 grep。
4. **写 description**：做什么 + 「当问题涉及以下任一关键词时必须读取本 skill：…」（标识符、报错原文、口语症状）+ 「不负责：… → 用 …」。
5. **正文**按骨架：输入最小集 → 硬闸 → FAQ 快查 → SOP 章节 → 分诊 → 核心手法 → 真实系统验证 → 决策树 → 输出契约 → 不要做什么（含已知死路）→ 相关 skill → 案例库。
6. **泛化检查**：没有 case 编号 / 个案答案；每条规则一句判据 + 一句原因 + 出处日期。
7. **出 DIFF 给我看**，确认后写入 `~/.agents/skills/<name>/`，再软链到 `~/.claude/skills/` 和 `~/.codex/skills/`。
8. **验证触发**：用 2–3 句我真实会说的话（短、带链接 / ID）测试能否命中；用干净子 agent 跑一个历史 case。
