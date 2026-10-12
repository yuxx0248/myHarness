# Claude Code 全局指令（由 myHarness 生成，安装到 ~/.claude/CLAUDE.md）

@~/.agents/framework/core/AGENTS.md

---

## Claude Code 专属映射

core 里提到的通用动作，在 Claude Code 里这样做：

| core 里的动作 | Claude Code 里 |
|---|---|
| 设会话标题 `[工作线] 具体任务` | 前 3 轮内调用 `mcp__ccd_session_mgmt__set_session_title`（`session_id: "self"`），工具是 deferred 的，先用 ToolSearch `select:mcp__ccd_session_mgmt__set_session_title` 加载。只改一次。工作线用一个短词（例：debug / infra / kb / bot；领域 pack 可以列出常用的）；有关键 ID、客户名、PR 号的写进去 |
| 子 agent / 新上下文 | `Agent` 工具（独立的块在一条消息里并行发出，默认后台运行）；大文件交给子 agent 读 |
| 给自己定 loop / timer | `/loop`、`ScheduleWakeup`；长期定时用 scheduled-tasks |
| 提议后续工作给我点 | `spawn_task` chip（顺手发现的范围外问题也用它） |
| 断点续接 | 读所有未归档会话的状态卡（如果装了 `where-was-i` 一类 skill 就用它） |
| skill 位置 | `~/.claude/skills/`（跨 runtime 的 skill 软链自 `~/.agents/skills/`） |
| 横切纪律注入 | `~/.claude/settings.json` 的 hooks（领域 pack 可以自带 hook） |
| 可分享的页面 / 看板 | Artifact，更新时重新发布到同一个 URL |
| memory | 自带的 auto memory（`~/.claude/projects/<cwd>/memory/`）；格式和卫生规则按 `playbooks/knowledge-system.md` §2；跨项目通用的教训写到 `~/.agents/memory/` |
| 非交互 LLM 调用（脚本里） | `claude -p --tools "" --strict-mcp-config --setting-sources "" --no-session-persistence --json-schema …` |
| 浏览器 | 内置浏览器 / Claude in Chrome（通常已登录，先自己去看） |

领域相关的事：读 `~/.agents/packs/*/README.md` 里和当前任务相关的那个领域 pack。
