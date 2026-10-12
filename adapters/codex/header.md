<!-- 由 myHarness/scripts/build.sh 生成：header.md + core/AGENTS.md + footer.md。不要直接编辑 dist 里的文件。 -->
# Codex 全局指令（~/.codex/AGENTS.md）

Codex 不支持 `@import`，所以 core 全文内联在下面。框架根目录安装在 `~/.agents/framework/`，
§11 索引里的 playbook / skill 路径都相对这个目录，需要时**直接用 shell 读文件**（`cat ~/.agents/framework/playbooks/<x>.md`）。

## Codex 专属映射

| core 里的动作 | Codex 里 |
|---|---|
| 设会话标题 | 不支持；只附状态卡 |
| skill | `~/.codex/skills/<name>/SKILL.md`（软链自 `~/.agents/skills/`）。如果当前版本没有自动加载 skill，就按 §11 的表格在遇到对应场景时自己 `cat` 对应的 SKILL.md |
| 子 agent / 新上下文 | 没有内置子 agent 时：先用脚本预处理大文件；需要独立上下文就跑一个 `codex exec --sandbox read-only "<自包含 prompt>" > out.md` 子进程，只读它的输出 |
| 给自己定 loop / timer | 没有内置调度：用 launchd / cron 跑 `codex exec`（按 `playbooks/automation.md` §7 的无人值守约定写 prompt），或在状态卡 👉 里提醒我 |
| 提议后续工作给我点 | 在回复末尾列出可直接运行的 `codex exec "<prompt>"` 命令 |
| 横切纪律注入（Claude 用 hook） | 没有 hook：领域 pack 里需要注入的纪律，在 pack README 里写成「做 X 类任务时适用」的段落，遇到时读 |
| memory | 没有自带 memory：跨项目的持久教训按 `playbooks/knowledge-system.md` §2 的格式提议写入 `~/.agents/memory/<主题>.md`，并更新 `~/.agents/memory/MEMORY.md` 索引（先出 DIFF 等我确认）。开始任务时先 `cat ~/.agents/memory/MEMORY.md`，按相关性读具体文件 |
| 非交互 LLM 调用（脚本里） | `codex exec --sandbox read-only --output-schema schema.json -`（prompt 走 stdin）；安装后用 `codex exec --help` 核对参数名 |
| 审批 / 沙盒 | runtime 的审批策略不能代替 core §2 的自主度表：即使沙盒允许，也按动作类型停在 stop_at |

## 排查类任务的交付纪律

1. 每条断言必须带验证产物（实际查询行 / schema / 代码 `file:line` / 日志行 / 消息 id）。没有产物 = ⚠ 假设 = 停止条件，不得进入修复阶段。串联推断的可信度 = 最弱一环。
2. 动手查数 / 读代码前，先扫相关聊天群近 24h，对齐关键人已否决 / 确认的事。
3. 最终交付物 = 可分享文档：每步三件套 —— Query（完整可重跑）+ 结果（表格 / 关键行）+ 解读（证明或否决了哪个假设，为什么走下一步）。
4. 文档链接回写到对应 SOP 的案例库。
唯一例外：5 分钟内可解决、纯内部、不面向客户的小事，可只在聊天里给结论。

---
