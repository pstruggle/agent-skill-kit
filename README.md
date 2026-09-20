# agent-skill-kit

跨 Agent 复用的技能包：**技能源文件统一放在 `skills/`，由安装脚本投递到各个 Agent 的技能目录**。一份技能同时可用于 DSH、Codex、Claude Code，以及遵循 `.agents` 约定的其他工具。

> A small, agent-agnostic skill kit. One source of truth under `skills/`, one installer that copies or links each skill into the directory your agent already reads.

## 目录结构

```
agent-skill-kit/
├─ install.ps1           # Windows / PowerShell 安装器
├─ install.sh            # macOS / Linux 安装器
├─ skills/               # 唯一事实源：每个技能一个目录
│  └─ project-task-sop/
│     ├─ SKILL.md
│     ├─ sop.py
│     ├─ reference/
│     └─ templates/
├─ .gitattributes        # 统一 LF、UTF-8
└─ .gitignore            # 忽略各 Agent 的本地安装产物
```

## 包含的技能

| 技能 | 用途 | 版本 |
|------|------|------|
| `project-task-sop` | 轻量项目任务执行规程：规模决策门（light/standard/full）、模块化上下文索引、JSONL 实验去重、教训检索、决策点审计笔记 | 3.1.0 |

## 安装

```powershell
git clone https://github.com/pstruggle/agent-skill-kit.git
cd agent-skill-kit
pwsh ./install.ps1 -Target all -Scope user
```

```sh
git clone https://github.com/pstruggle/agent-skill-kit.git
cd agent-skill-kit
./install.sh --target all --scope user
```

默认装到每个 Agent 的用户级技能目录（`-Scope user`）。只想装某一个 Agent 就换成 `-Target codex`。

## 安装到哪里

| Target | Scope | 目标目录 |
|--------|-------|----------|
| `dsh` | user | `$DSH_HOME/skills`（默认 `~/.dsh/skills`） |
| `dsh` | project | `<project>/.dsh/skills` |
| `codex` | user / project | `$CODEX_HOME/skills`（默认 `~/.codex/skills`） |
| `claude` | user | `~/.claude/skills` |
| `claude` | project | `<project>/.claude/skills` |
| `agents` | user | `~/.agents/skills` |
| `agents` | project | `<project>/.agents/skills` |
| `custom` | — | `-Dest` / `--dest` 指定的任意目录 |

Codex 只从 `CODEX_HOME` 发现技能，所以 `-Scope project` 对 `codex` 仍写入 `$CODEX_HOME/skills`；`-ProjectRoot` 只影响 DSH / Claude / agents 的项目级目录。

## 参数

| PowerShell | shell | 说明 |
|------------|-------|------|
| `-List` | `--list` | 列出技能与将要写入的目录，不落盘 |
| `-Target` | `--target` | `dsh`、`codex`、`claude`、`agents`、`all`，可逗号分隔多选 |
| `-Scope` | `--scope` | `user`（默认）或 `project` |
| `-Skill` | `--skill` | 只装指定技能，逗号分隔；缺省为全部 |
| `-Mode` | `--mode` | `copy`（默认，复制文件）或 `link`（Windows 用 junction，Unix 用符号链接，跟随 `skills/` 的改动） |
| `-ProjectRoot` | `--project-root` | `-Scope project` 时的项目根，默认当前目录 |
| `-Dest` | `--dest` | 额外安装到一个自定义目录 |
| `-Force` | `--force` | 覆盖已存在的同名技能 |
| `-DryRun` | `--dry-run` | 只打印将执行的动作 |
| `-Uninstall` | `--uninstall` | 从目标目录移除技能 |

示例：

```powershell
# 看清单
pwsh ./install.ps1 -List

# 装进某个项目的 DSH 项目级技能目录
pwsh ./install.ps1 -Target dsh -Scope project -ProjectRoot C:\work\my-app

# 用链接方式装到 Codex / Claude，改 skill 源文件即时生效
pwsh ./install.ps1 -Target codex,claude -Mode link

# 升级已装的技能
git pull
pwsh ./install.ps1 -Target all -Force

# 卸载
pwsh ./install.ps1 -Uninstall -Target claude -Skill project-task-sop
```

```sh
./install.sh --list
./install.sh --target dsh --scope project --project-root ~/work/my-app
./install.sh --target codex,claude --mode link
./install.sh --uninstall --target claude --skill project-task-sop
```

## 用 Codex 自带的安装器直接装

不动本仓库，也可以让 Codex 从 GitHub 直接拉取：

```sh
python <skill-installer>/scripts/install-skill-from-github.py \
  --repo pstruggle/agent-skill-kit \
  --path skills/project-task-sop
```

## 新增一个技能

1. 在 `skills/` 下新建目录，目录名用 kebab-case（`^[a-z0-9]+(?:-[a-z0-9]+)*$`）。
2. 目录内放 `SKILL.md`，frontmatter 至少包含 `name` 与 `description`，且 `name` 与目录名一致。
3. 重新执行安装脚本（加 `-Force` / `--force`）即可投递到各 Agent。
4. 在本 README 的「包含的技能」表格里补一行。

## 设计约定

- `skills/` 是唯一事实源；`.dsh/`、`.codex/`、`.claude/`、`.agents/` 都是安装产物，已在 `.gitignore` 中排除。
- `link` 模式便于边改边用；`copy` 模式便于固定版本、避免被测 Agent 直接改到源文件。
- 所有文本文件按 LF 存储、UTF-8 无 BOM 编码（见 `.gitattributes`），中文内容在 Windows 下也不会乱码。
- 安装脚本只操作目标根目录之下的路径，越界即报错退出。

## English

`agent-skill-kit` keeps every skill in `skills/<name>/SKILL.md` and ships two installers that copy or link those folders into the skill directory your agent already reads (DSH, Codex, Claude Code, or the `.agents` convention).

```sh
git clone https://github.com/pstruggle/agent-skill-kit.git
cd agent-skill-kit
./install.sh --target all --scope user      # or: pwsh ./install.ps1 -Target all -Scope user
```

Run `--list` to see the skills and destinations, `--mode link` to symlink instead of copy, `--uninstall` to remove, `--help` for the full flag list.

## 许可证

MIT，见 [LICENSE](LICENSE)。
