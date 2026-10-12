# myHarness · 给 agent 的操作说明

**你是被派来操作这个仓库的 agent**（Claude Code、Codex 或别的）。这一页说明怎么安装、怎么让它生效、怎么改、怎么排障。先读完这一页，再动任何文件。

两个 AGENTS.md 不要搞混：
- **本文件（仓库根目录 `AGENTS.md`）** = 怎么操作这个仓库。只在你工作于本仓库时生效。
- **`core/AGENTS.md`** = 产品本身：装好之后，用户的**所有** agent 每轮都会加载的工作约定。改它就是改所有 agent 的行为。

**这是公开仓库。** 绝不写入任何公司 / 产品 / 内部工具 / 人名 / 客户 / 内部链接 / 密钥。领域内容一律放在仓库外的领域 pack 里（见 §5）。

---

## 1. 用户说什么 → 你去哪一节

| 用户说 | 去 |
|---|---|
| 「装上」「在新机器上用这套」「Codex 也要用」 | §2 安装 |
| 「这套东西怎么起作用的」「为什么 agent 会读 X」 | §3 生效方式 |
| 「改一下规则」「把这个经验加进框架」「agent 老是犯 X」 | §4 修改 |
| 「加一个领域」「换公司了」「这条太领域了」 | §5 领域 pack |
| 「给同事 / 别人也用」 | §6 给别人用 |
| 「agent 没按框架来」「状态卡没了」「skill 没触发」 | §7 排障 |
| 「用这套搭个 bot」「改了规则要校准」「做个定时任务」 | `kit/README.md`（可运行骨架） |

## 2. 安装

前提：macOS 或 Linux、bash、git。Codex 可以没装（装好后再跑一次即可）。

1. clone 到一个**长期存在**的位置（不要放在临时 worktree / scratchpad 里，软链会断；`install.sh` 检测到 worktree 会拒绝 `--apply`）：
   ```bash
   git clone git@github.com:yuxx0248/myHarness.git ~/myHarness
   ```
2. 问用户有没有领域 pack（仓库外的一个目录，里面有 `README.md` 和 `leak-terms.txt`）。有就记下路径。
3. **先 dry-run，把输出给用户看**：
   ```bash
   ~/myHarness/scripts/install.sh --pack <pack 目录>
   ```
   它会：跑 `build.sh` 体检 → 列出要建的软链 → 列出要备份的文件（`~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`）。
   特别指出：**用户现有的 `~/.claude/CLAUDE.md` 会被换掉**。先读它，看里面有没有框架没覆盖的规则；有的话，先按 §4 把规则并进框架（或并进 pack），再装。
4. 用户确认后才 `--apply`（这是改全局配置，属于 core §2 里「先给我看」那一类）：
   ```bash
   ~/myHarness/scripts/install.sh --apply --pack <pack 目录>
   ```
5. 验证（在**新会话**里，旧会话不会重新加载）：
   - `ls -l ~/.claude/CLAUDE.md ~/.codex/AGENTS.md ~/.agents/framework ~/.agents/skills/investigate` 都指向本仓库。
   - 开一个新的 Claude Code 会话，问「你的状态卡格式是什么、哪些动作要停下等我」—— 回答应来自 `core/AGENTS.md` §1、§2。
   - 装了 Codex 的话：`codex exec --help` 核对 `adapters/codex/header.md` 和 `playbooks/automation.md` 里写的参数名（`--sandbox`、`--output-schema`），不一致就按 §4 改 adapter。
6. 回滚：把 `*.bak-<时间戳>` 改回原名，删掉 `~/.agents/framework`、`~/.agents/skills/<本仓库的 skill>`、`~/.claude/skills/…`、`~/.codex/skills/…`、`~/.agents/packs/…` 这些软链。

安装后的样子：
```
~/.agents/framework      → ~/myHarness
~/.agents/skills/<x>     → ~/.agents/framework/skills/<x>     （7 个通用 skill）
~/.claude/skills/<x>     → ~/.agents/skills/<x>
~/.codex/skills/<x>      → ~/.agents/skills/<x>
~/.agents/packs/<name>   → 领域 pack 目录
~/.agents/memory/        跨项目共享 memory（Codex 没有自带 memory 时用）
~/.claude/CLAUDE.md      → ~/myHarness/adapters/claude/CLAUDE.md   （@import core）
~/.codex/AGENTS.md       → ~/myHarness/dist/codex/AGENTS.md        （build 时内联 core）
```

## 3. 生效方式（加载模型）

| 层 | 什么时候被读到 | 怎么读到 |
|---|---|---|
| `core/AGENTS.md` | **每轮都在** | Claude：`~/.claude/CLAUDE.md` 里 `@~/.agents/framework/core/AGENTS.md`；Codex：`dist/codex/AGENTS.md` 内联了全文 |
| `adapters/<runtime>/` | 每轮都在 | 同上，紧跟 core 之后；把「开子 agent」「定 timer」翻译成该 runtime 的具体工具 |
| `playbooks/*.md` | 遇到对应场景 | core §11 的索引表告诉 agent 去读哪份（agent 自己用文件读取工具读） |
| `skills/*/SKILL.md` | 用户的话命中 description 里的关键词 | runtime 的 skill 机制；不支持时 agent 按 §11 自己去读 |
| 领域 pack | 任务涉及具体领域 | core §11 最后一行：读 `~/.agents/packs/*/README.md` 里相关的那个 |
| 项目自己的 `AGENTS.md` / `CLAUDE.md` | 在那个项目里工作时 | runtime 自动加载；叠加在 core 之上 |

优先级：用户在对话里的明确指令 > 项目 `AGENTS.md` > `core/AGENTS.md` > playbook。

它是**软约束**：写给 agent 读的约定，不是 harness。需要硬保证的边界（不可逆命令、额度）要在 harness 层做（Claude 的 hook、Codex 的沙盒 / 审批策略、包装脚本），不要指望框架文本拦住。

## 4. 修改（加规则、改规则、吸收经验）

### 4.1 先判断放哪一层

| 内容 | 放哪 |
|---|---|
| 每轮都用得上的判断、边界、沟通方式 | `core/AGENTS.md`（一句判断 + 指针，细节下沉） |
| 某类任务的方法（怎么查、怎么验收、怎么写 skill） | `playbooks/<主题>.md` |
| 可以按步骤执行的流程，并且有明确的触发词 | `skills/<name>/SKILL.md`（薄索引，指向 playbook） |
| runtime 专属的工具名、参数、替代做法 | `adapters/<runtime>/` |
| 换一家公司就不成立的东西（系统、表、人、频道、领域事实） | **仓库外**的领域 pack |
| 个人偏好、项目约束、外部资源指针 | memory（不进本仓库） |

判断标准：「换一家公司还成立吗？」「是方法还是事实？」方法进仓库，事实进 pack。

### 4.2 流程

1. **先出 DIFF 给用户，等确认**（core §2：SOP / skill / 框架的正式改动必须先给用户看）。
2. 改**仓库里的源文件**。不要改 `dist/`（生成物），也不要改 `~/.claude/CLAUDE.md` 这类安装位置（它们是软链，改了也是改仓库，但容易漏 build）。
3. 写法：
   - 规则写成可泛化的判据 + 一句原因；不写个案编号、个案答案、人名、内部系统名。
   - 中文为主，代码标识符英文原样。
   - core 只放判断；**core 超过 ~16KB 就把细节下沉到 playbook**（Codex 默认只读前 32KiB）。
   - 新增 playbook 要在 core §11 加一行索引；新增 skill 要有 `name`（= 目录名）和 `description`（做什么 + 触发词 + 不负责什么）。
4. 跑体检，必须全绿：
   ```bash
   PACK_DIRS="<领域 pack 目录>" ~/myHarness/scripts/build.sh
   ```
   检查项：Codex 体积上限、core / skill / adapter 里引用的 playbook 都存在、skill frontmatter、`kit/` 离线测试、**领域泄漏**（按 pack 的 `leak-terms.txt`）、密钥。
   改了 `kit/` 的代码：补 / 改 `kit/tests/test_kit.py`；涉及 `llm.py` 的改动再用真实后端跑一次冒烟（一个 `triage.propose` 调用即可）。
5. **行为回归**（改了 core 或 playbook 的判断时必做）：开一个干净的子 agent（新上下文，不带本会话的记忆），用下面的 prompt 跑 `tests/scenarios.md`，然后由你对照「期望」列判分。
   ```
   你是一个全新的 agent。关于怎么做事，你只能遵守这些文件：~/myHarness/core/AGENTS.md（常驻规则），
   以及 core §11 指引你去读的 ~/myHarness/playbooks/ 和 ~/myHarness/skills/ 下的文件。
   不要读 ~/myHarness/tests/。对下面每个场景，写出你下一步做什么、原话回复什么（每条 ≤8 行）。
   最后列出「歧义」：框架文件里不清楚、矛盾或没覆盖的地方（标出文件 + 章节）。
   <把 tests/scenarios.md 的「场景」列逐条贴进来，不贴「期望」列>
   ```
   - 全部命中才算过。不命中或报告了歧义 → **改框架，不改期望**。
   - 新加的规则要配一条新场景追加到 `tests/scenarios.md`（领域无关的写法）。
6. 提交：分支 + PR，用户合并（PR 是审批闸）。commit 信息：`<模块>: <做了什么> —— <为什么>`，例如 `core: 向别人要权限也走起草 —— 场景 8 有歧义`。
7. 已安装的机器：core / playbook / skill 改动拉下来即生效（软链）；**adapter 或 core 改了要重跑 `build.sh`** 才会更新 Codex 的 `dist/`。

## 5. 领域 pack

- 规范和模板：`domain-packs/README.md`、`domain-packs/_template/README.md`。
- 新建 pack：在**仓库外**建 `<name>-pack/`，按模板写 `README.md`，并写 `leak-terms.txt`（每行一个只属于这个领域的词，扩展正则，大小写不敏感）。
- 安装 / 挂载：`install.sh --pack <目录>`（可多次）→ `~/.agents/packs/<name>`。
- pack 可以带 `hooks/`（Claude 端的强制注入）；Codex 没有 hook，把同样的纪律写在 pack README 里「做 X 类任务时适用」的段落。
- **绝不把 pack 提交进本仓库**。发现框架文件里有领域内容 → 挪进 pack，把 pack 的词表补上，让 `build.sh` 以后能拦住。

## 6. 给别人用

`core/AGENTS.md` 是写给特定一个人的（文中「我」= 仓库作者）。别人使用时：
1. fork，改 `core/AGENTS.md` 的标题和第一段（「我」是谁）、§1（语言、沟通偏好）、§2（自主度表：哪些直接做、哪些停下）。这三处是个人化的，其余是通用方法。
2. `adapters/claude/CLAUDE.md` 里的会话标题工具只在 Claude 桌面端存在；没有就删掉那一行。
3. 写自己的领域 pack，按 §2 安装。

## 7. 排障

| 症状 | 查 |
|---|---|
| 新会话里 agent 完全没按框架来 | `ls -l ~/.claude/CLAUDE.md` 是否指向 `adapters/claude/CLAUDE.md`；里面的 `@~/.agents/framework/core/AGENTS.md` 路径是否存在（`ls ~/.agents/framework/core/AGENTS.md`）；是否开了**新**会话 |
| Codex 只遵守了前半部分 | `wc -c dist/codex/AGENTS.md` 是否超过 32768（或用户配置的 `project_doc_max_bytes`）；超了就把 core 的细节下沉 |
| Codex 的 `dist/` 是旧的 | 改了 core / adapter 后没跑 `build.sh` |
| skill 没触发 | 软链是否存在（`ls -l ~/.claude/skills/<x> ~/.codex/skills/<x>`）；description 里有没有用户真实会说的词；runtime 不支持 skill 时 agent 应按 core §11 自己读 |
| 软链断了 | 仓库被移动 / 删除，或装在了临时 worktree 里 → 在新位置重跑 `install.sh --apply` |
| `build.sh` 报领域泄漏 | 按报出的行把内容挪进 pack，或换成通用说法 |
| agent 在不该停的地方停、该停的地方没停 | 这是 core §2 自主度表的问题：按 §4 改表，补一条回归场景 |

## 8. 文件地图

| 路径 | 内容 |
|---|---|
| `core/AGENTS.md` | 常驻约定（产品本体） |
| `playbooks/` | 9 份按需方法：diagnosis · verification-and-closure · autonomy-and-triage · communication · severity-and-dispatch · automation · multi-agent · knowledge-system · skill-authoring |
| `skills/` | 7 个通用 skill：investigate · close-out · triage-ask · distill-lessons · ship-automation · author-skill · handoff-prompt |
| `adapters/claude/CLAUDE.md` | Claude Code 全局指令（@import core + 工具映射） |
| `adapters/codex/header.md`、`footer.md` | Codex 全局指令的头尾（build 时夹住 core） |
| `domain-packs/` | 领域 pack 规范 + 模板（pack 本身不在仓库里） |
| `scripts/build.sh` | 生成 `dist/codex/AGENTS.md` + 体检 |
| `scripts/install.sh` | 安装 / 挂 pack（默认 dry-run） |
| `tests/scenarios.md` | 行为回归场景 |
| `kit/` | 可运行骨架：LLM 封装、规则导图、分诊、story 归并、tick、校准 harness、launchd 模板（见 `kit/README.md`） |
| `dist/` | 生成物（不进 git） |
