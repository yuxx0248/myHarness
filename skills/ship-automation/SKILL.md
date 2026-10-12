---
name: ship-automation
description: 新建或修改一个自动运行的东西之前的检查单——bot、轮询器、launchd 任务、cron、定时 agent、调 LLM 的脚本、watcher。触发："做个 bot"、"每 10 分钟扫一次"、"设个定时任务"、"自动回复"、"launchd"、"scheduled task"、"为什么自动跑就挂手动就好"。覆盖静默失败、确定性优先、LLM 封装、kill switch、幂等、首次播种、无人值守 prompt 约定。
---

# ship-automation

细节见 `~/.agents/framework/playbooks/automation.md`；权限分级见 `autonomy-and-triage.md` §6。
可运行骨架（LLM 封装、规则导图、分诊、story 归并、tick、校准 harness、launchd）在 `~/.agents/framework/kit/`，先读 `kit/README.md`，能复用就别重写。

## 设计检查单
- [ ] 能确定性完成的部分都是脚本，LLM 只处理输入变了的单元，每轮有上限
- [ ] 规则作为数据独立成文件（唯一真源），改规则走校准
- [ ] LLM 调用集中在一个封装文件；嵌套调用隔离用户配置（无工具 / 无 MCP / 无 skill / 不持久化会话）；JSON schema 输出 + 事后校验
- [ ] v1 只出提案；对外发言 / 写操作有 stop_at；自主权按分支校准后再放
- [ ] 水位 − 重叠窗口 + 按稳定 id 去重；水位只推进到已处理项
- [ ] 认领同步完成再放后台；对外消息带幂等 key；有歧义的错误后不重试非幂等创建
- [ ] 每个模块有 `*_ENABLED` / `*_DRY` / 单 case 覆盖开关；线上 `config.env` 是真实副本
- [ ] 首次运行只播种现状，不处理积压
- [ ] 状态 / 日志不放 `~/Documents`；状态不进 git
- [ ] watchdog 与主任务分开；默认安静，合并成一条摘要私信

## 静默失败检查单
- [ ] stdout + stderr 都进日志；关键路径没有 `2>/dev/null`
- [ ] 成功靠正向标记判断（`Run done` / 真实 message_id），不靠 mtime / 没报错
- [ ] 每轮一行摘要（处理 N、追加 M、花费）；连续多轮 0 追加会告警
- [ ] 没有吞错的默认值（`catch → []`、`coalesce(x, 1)`、INNER JOIN 闸门）

## 验证
- 语法检查 → dry-run → 单 case → 在真实运行上下文触发（`launchctl kickstart` / 手动跑定时任务）→ 读日志里的正向标记。
- 不在会话沙盒里下「好了」的结论。

## 无人值守 prompt（定时 agent）
自包含背景 · 边界段（不许碰什么）· 数字化成功判定 · 成功静默、失败私信一次（日志尾 20 行）· 幂等检查 · 部分失败留可恢复状态 · 预检代码已部署。
