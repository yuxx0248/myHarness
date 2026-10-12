# myHarness

我（Zuohang）的 agent 工作框架：怎么跟我说话、自主度怎么分、怎么排查、怎么验收、怎么沉淀知识。
**和 runtime 无关**（Claude Code 与 Codex 用同一份真源），**和领域无关**（不含任何公司、产品、内部工具、人名、客户）。
领域内容放在框架外的「领域 pack」里，换领域只换 pack。

---

## 分层

```
core/AGENTS.md          常驻（每轮都加载，~13KB）：怎么跟我说话、按动作类型的自主度、工作循环、证据纪律、
                        判定边界、安全、工程习惯、多 agent、学习闭环、按需加载索引
   │
   ├─ playbooks/        按需（遇到对应场景才读）：领域无关的方法
   │    diagnosis · verification-and-closure · autonomy-and-triage · communication ·
   │    severity-and-dispatch · automation · multi-agent · knowledge-system · skill-authoring
   │
   └─ skills/           按需（关键词触发）：把 playbook 变成可执行步骤，两个 runtime 通用的 SKILL.md
        investigate · close-out · triage-ask · distill-lessons · ship-automation · author-skill · handoff-prompt

adapters/               runtime 映射：同一个通用动作在 Claude Code / Codex 里分别怎么做
  claude/CLAUDE.md      @import core + Claude 专属（会话标题工具、Agent、hook、Artifact、spawn_task…）
  codex/header+footer   Codex 不支持 @import → build 时内联 core；没有 hook / 子 agent / 调度时的替代做法

domain-packs/           领域 pack 的规范和模板（pack 本身在框架外，安装到 ~/.agents/packs/<name>/）
scripts/build.sh        生成 dist/codex/AGENTS.md；检查体积（Codex 默认 32KiB）、引用路径、skill frontmatter、
                        领域泄漏（按各 pack 的 leak-terms.txt）、密钥
scripts/install.sh      默认 dry-run；--apply 才建软链（原配置先备份）；--pack DIR 挂载领域 pack
tests/scenarios.md      行为回归场景（领域无关）：改了 core / playbook 后用干净子 agent 重跑
kit/                    可运行骨架（Python 标准库）：llm 封装 · 规则即数据 · 分诊 · story 归并 · tick · 校准 harness · launchd；离线测试
```

设计原则：
- **单一真源**：core 只写一份，Claude 用 `@import`，Codex 由 build 内联；skill 真源在 `~/.agents/skills`，两边软链。
- **常驻层只放判断，细节按需加载**：core 控制在 Codex 上限的一半以内。
- **领域无关**：判断标准是「换一家公司还成立吗」；不成立的放 pack。build 用 pack 的词表做泄漏检查。
- **runtime 相关的东西只在 adapters/**：core、playbook、skill 里只说「开子 agent」「定 timer」，不说具体工具名。
- **软约束**：框架是写给 agent 读的约定，不是 harness；需要硬保证的边界（不可逆命令、额度）应由 harness 层（hook / 沙盒 / 包装脚本）实现。

## 安装

给 agent 的完整操作说明（安装、日常使用、改规则、加领域 pack、排障）见 [AGENTS.md](AGENTS.md)。

```bash
git clone git@github.com:yuxx0248/myHarness.git ~/myHarness
```

```bash
~/myHarness/scripts/install.sh --pack ~/<领域>-pack
```

```bash
~/myHarness/scripts/install.sh --apply --pack ~/<领域>-pack
```

`--apply` 会把 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md` 备份成 `*.bak-<时间戳>` 后换成软链。回滚：把 `.bak-*` 改回原名，删掉软链。
Codex 装好后先跑 `codex exec --help`，核对 `adapters/codex/header.md` 和 `playbooks/automation.md` 里写的参数名。

## 维护

- **改规则**：改 core / playbook / skill → `scripts/build.sh` → 看 diff。改了 core 的判断，挑几个历史场景用干净子 agent 重跑（只给框架、不给答案），确认行为没走偏。
- **加新经验**：用 `distill-lessons` 出 DIFF。可泛化的进 playbook / core，领域的进 pack 指向的知识库，个人偏好的进 memory。
- **core 变胖了**：超过 ~16KB 就把细节下沉到 playbook，core 只留一句判断 + 指针。
- **换领域 / 换公司**：按 `domain-packs/_template/` 写一个新 pack，`install.sh --pack` 挂上，旧 pack 摘掉。
