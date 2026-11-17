---
name: project-task-sop
description: 多会话、多 Agent 的项目里维护上下文与任务台账，让下一个会话不用重新摸清仓库。触发语："接着上次做"、"上次做到哪了"、"这个仓库之前怎么改的"、"别重复踩坑"、"上下文过期了吗"、"任务排一下，接下来做哪个"、"开工前先对齐边界"、"收工前把门跑一遍"。命令：init/scan/refresh/entry/tasks/next/dedup/record/render/check/lessons/tokens/dims/audit/gates/migrate
whenToUse:
  - 用户说"接着上次做"、"上次做到哪了"、"继续这个项目"
  - 用户问"这个仓库之前怎么改的"、"为什么这里这么设计"
  - 用户说"别重复踩坑"、"以前踩过什么坑"、"教训查一下"
  - 用户问"上下文过期了吗"、"维度要不要更新"、"check 跑一下"
  - 用户说"任务排一下"、"接下来做哪个"、"这个任务到哪一步了"
  - 开工前需要先对齐边界、写 plan.md / NOTES 时
  - 接手陌生代码库、跨会话长期项目需要共享上下文时
disable-model-invocation: false
---

# Project Task SOP v4.12.3 — 轻量任务执行规程

> **原则**：先判断该不该用，再决定用多重的流程。文书越少，Token 越省。
> **v3.x 基础**：决策门 light/standard/full；JSONL 单一事实源；教训沉淀 + `sop.py lessons`；上下文 token 账本；启动前边界对齐门；维度 9 项目级教训。
> **v4.0 变更**：⑩ 维度体系下沉为 **`ai/context.manifest.json` 单一事实源**——维度集合/锚点（glob）/任务映射全部由项目声明，sop.py 退化为纯执行器；⑪ AGENTS.md 分区：生成区（`<!-- SOP-GEN:BEGIN/END -->`）由 `refresh --mark-fresh` 重写，手工区（身份/任务记录/归档）不碰；⑫ 维度文件头部新增 `> **摘要**:`（agent 维护，索引表自动聚合）；⑬ `check --context` 校验 manifest 结构/一致性/锚点命中；⑭ 锚点 glob 相对 `codeRoot` 解析（支持 ai 产物目录与代码目录分离）。
> **v4.1 追加**：⑮ **`sop.py migrate`**（v3 → v4 半自动迁移）：AST 解析旧 sop.py 常量 → 生成 manifest + 补维度文件摘要行 + AGENTS.md 分区改造；⑯ 新增 `reference/migration-v3-to-v4.md` 升级手册（分步操作 / 验收清单 / 已知坑）。
> **v4.2 追加**：⑰ **维度归属判定表**（§1.5，信息类型→维度 + 反例排除，唯一权威，各维度头部不再重复进档说明）；⑱ **`sop.py audit`** 子命令（扫环境事实误入教训维度 / 跨维度重复 / 交叉引用失效，支持 `ai/.sop-audit-ignore.json` 已裁决豁免）。
> **v4.3 追加**：⑲ **对抗自查清单**（§0.1.1，分级触发、挂边界对齐门后，力度由决策门决定）；⑳ NOTES **决策点前置**对抗自查；DoD 新增对抗自查验收项。
> **v4.4 追加**：㉑ **归属判定动态化**——§1.5 表下沉为 `ai/context.manifest.json` 的 `attribution`（信息类型→维度 key，init 写入、项目可覆盖）；`sop.py audit` 的合法维度集合改从 attribution 读取（不再硬编码编号）；`check --context` 校验 attribution 引用的维度 key 存在；migrate 生成默认 attribution。

## 0. 启动门 — 先判断是否适用，再对齐关键边界，最后决定流程

- 任务 <5 分钟、纯问答、或无多会话价值 → **不适用本 skill，直接干活，不写任何文书。**
- 接手已有台账的项目：先 `python sop.py next`（现在能做什么、被谁挡着、后面几波），再对齐本次任务边界。`next` 只算 `depends` 约束，**不做优先级判断**——"先做哪个"仍由人/agent 按业务判断。
- 动手前先认领：`python sop.py next --claim <task-id>`。多会话并行时这一步是**唯一能分辨"谁在做、是否还活着"的信号**（`status: doing` 只说明有人在改，不说明是谁）；收工或放手用 `next --release`。

### 0.1 边界对齐门（适用本 skill 的任务）

开始产生交付物或改变状态的步骤前，先识别是否缺少会实质影响目标、范围、交付形式、验收标准、关键约束、风险等级或不可逆操作的关键边界信息。

- 可通过项目文件、已有上下文或只读调查确认的，先查证并记录依据，不重复询问。
- 仍无法确认的，集中列出会阻塞执行的用户决策问题，并尽量给出建议选项。
- 关键边界明确前，不自行补全条件，不进入实现或正式产出阶段；只读调查不受限制。
- 执行中新暴露的依赖性问题，可以继续询问。
- 用户明确表示“先按你的理解执行/继续”或作出等价授权时，可解除等待，但须将关键假设记录到 `plan.md` 的 `assumptions`，并在交付时说明。

### 0.1.1 对抗自查清单（分级，挂边界对齐门后）

> 触发规则：进入任务先按 §0.2 判定任务模式，再取对应力度。**不靠模型自觉"要不要对抗"，力度一律由决策门决定**，避免每次判断歧义。

- **力度 0（light · 一次性小改动）**：回答 1 条即可开工——需求是否真的成立？有没有更简单、更小的做法？
- **力度 1（standard · 多步迭代）**：结论记入 NOTES——① 剥离手段：真正要的结果是什么，当前方案只是其中一种实现路径？② 隐含假设：依赖哪些假设，哪条最可能不成立？③ 替代思路：至少 1 条差异较大的替代方案并简述取舍。④ 反向反问：换一种做法能省多少成本 / 避免哪些坑？（答不上写"暂未发现"）
- **力度 2（full / 高风险：架构、数据库结构变更、支付/分佣结算、不可逆、大金额、长周期）**：完整评审——① 目标剥离：把"要的结果"和"自以为的实现路径"分开，确认成功指标；② 假设爆破：列出全部隐含假设，逐条追问"若不成立会怎样、有无证据支撑"；③ 方案缺陷：成本、技术债务、合规、维护、长期隐患里哪些是致命的；④ 异构方案：产出范式不同的替代方案（非微调）；⑤ 反事实：若失败最可能因何失败，如何提前验证 / 止损；⑥ 收敛：推荐 + 写明"什么条件选 A / 选 B / 何时应暂缓该需求"。

**通用规则（各级都遵守）**
- 质疑必须关联成本、风险、目标偏差，禁止无理由抬杠。
- 0/1 级自查控制在 20~30 秒、2 级几分钟，不做流水账。
- 豁免开关：用户输入【直接执行】即跳过本清单。
- 自查只在发现实质问题时写进 NOTES / 方案，不新增文书。

### 0.2 决策门

| 判断 | 模式 | 产出 | 适用 |
|------|------|------|------|
| 一次性小改动（<30min、<5 文件、不重复） | **light** | 仅 `plan.md` | 常规修 bug、小功能 |
| 多步迭代或会重复 | **standard** | `plan.md` + `NOTES.md`（决策点） | 功能开发、重构 |
| 长周期 / 实验性 / 需审计复盘 | **full** | `plan.md` + `NOTES.md` + `.experiments.jsonl` | 调参、探索、跨会话任务 |

- **light**：跳过步骤 3–4，直接执行。
- **standard**：记录决策点，不做实验索引。
- **full**：额外用 `sop.py record/dedup/render/check` 维护实验记录。

## 目录结构

```
ai/
├── context.manifest.json   # 单一事实源：维度体系声明（id/key/title/anchors/tasks/related/attribution/codeRoot）
├── AGENTS.md                # 主索引：生成区（版本/索引表/映射表，sop 重写）+ 手工区（身份/任务记录）
├── TASKS.md                 # 任务台账：状态 + 相互关联（`sop.py tasks` 生成，禁止手改）
├── context/                 # 维度文件（数量 = manifest 声明，头部含变更锚点 + 摘要）
│   └── 09-project-lessons.md# 项目级教训（跨任务复用，所有任务必读，详见 reference/project-lessons.md）
├── {task}/
│   ├── plan.md              # 任务计划（必填字段见模板）
│   ├── NOTES.md             # 决策点审计笔记（standard/full）
│   └── .experiments.jsonl   # 实验记录唯一事实源（full）
└── templates/               # 可选：团队模板（优先于 skill 内置）
```

## 1. 读取/初始化上下文

1. `read("ai/AGENTS.md")` — 索引（生成区为当前维度体系快照）。
2. 按任务类型只读所需维度（索引内映射表由 manifest 聚合：feature→1,2,7,9,10；bugfix→1,2,3,7,9；…）；**维度 9（项目级教训）所有任务必读**——开跑前先扫一遍是否命中已知坑。
3. 无 `ai/` 或 `ai/context.manifest.json` 缺失 → 在本 skill 根目录执行 `python sop.py init`（写入内置 9 维度建议集 manifest），随后 `python sop.py scan`：按 codeRoot 实际文件自动填充维度 1/2/4/5/6/8 的事实初稿（标 `自动推导，待复核`），并把 0 命中的锚点改为真实文件或置空；再人工补维度 3/7/9 等语义判断，`refresh --mark-fresh` 封存。
4. 检测到相关文件变更 → `python sop.py refresh` 查看过期维度，重分析后 `--mark-fresh` 封存（同时重写 AGENTS.md 生成区）；只复核了部分维度时用 `--mark-fresh --only 12,13`，避免把没复核的维度一起封存成"最新"。
5. 新增/调整维度：直接编辑 `ai/context.manifest.json`（增删 dimensions 条目 + 同步 `attribution` 归属表 + 建/删对应 `context/NN-key.md`），再 `python sop.py check --context` 校验、`refresh --mark-fresh` 封存。**无需改 sop.py 代码。**
6. 需要仓库入口文件（`AGENTS.md` / 类似物）时，**先定落点，再生成——落点不许默认**：
   1. **询问落点**（三选一，等用户明确答复再动手）：① 仓库根 `codeRoot/AGENTS.md`；② 与 `ai/` 同级（`entry.file = "../AGENTS.md"`）；③ 项目更合适的位置（子项目根 / monorepo 包根等，写相对 `codeRoot` 的路径）。判据与代价见 `reference/repo-entry-file.md` §2.1。
   2. **记录结论**：把落点与理由（谁会读它、`ai/` 是否进版本库）写进 `plan.md` 的 `assumptions` 或 NOTES 决策点；没有这段记录=没确认过落点。
   3. **再生**：manifest 加 `entry`（可选，`file` 空 = 不启用）+ 维度加 `entryHint`（一条 ≤1 行纪律摘要），`python sop.py entry` 生成、`entry --check` 校验。**入口只做指向，规则正文留在维度**——详见 `reference/repo-entry-file.md`。

## 1.5 维度归属判定（信息该进哪个维度）— 默认归属表，可由 manifest 覆盖

信息落地前先按归属表判定，避免错位与跨维度重复。**判据是"信息类型"不是"看起来像教训"**：
教训维度（`project-lessons`）只存"踩坑后的正确做法"，**不存事实本身**；事实归各自维度。

> **归属表 = `ai/context.manifest.json` 的 `attribution` 字段（v4.4+，init 写入、项目可增删改，`sop.py audit` 从它读取）；下表为默认值。** 新增/改名维度时同步调整 attribution；内置建议集未含"前端页面/组件约定"维度，需要时由项目增维并将其 key 加入 `ui-conventions`。各维度头部不再重复进档说明。

| 信息类型（attribution key） | 默认归属（维度 key） | 反例排除 |
|----------|----------|----------|
| `lessons` 跨任务踩坑"触发场景 → 正确做法"（跨 ≥2 任务 + 环境/框架级） | `project-lessons` 项目级教训 | 环境事实本身（路径/凭据/命令）**不进 lessons** |
| `env-facts` 环境事实/工具路径/登录凭据/本地服务地址/远程库连接/构建命令 | `dev-workflow` 开发工作流与环境（另允许 `topology`/`docs-assets`/`dependencies` 列本职路径） | — |
| `tech-usage` 框架/库版本与用法事实（Jackson3/SpringBoot4/…） | `tech-stack` 技术栈识别 | 用法**教训**进 `lessons` |
| `deps` 外部服务/依赖/镜像源/第三方库清单 | `dependencies` 依赖与供应链 | 连接凭据进 `env-facts` |
| `topology` 目录/关键文件分布 | `topology` 目录拓扑 | — |
| `git` Git 仓库/分支/提交历史 | `git-history` Git 历史 | — |
| `docs` 文档/知识资产清单 | `docs-assets` 文档与知识资产 | — |
| `quality` 测试/CI/质量体系 | `quality-system` 测试与质量体系 | — |
| `architecture` 架构/模块/模式 | `architecture` 架构模式 | — |
| `ui-conventions` 前端页面/组件约定 | 内置建议集无对应维度（需项目自定义） | — |
| 任务特有的一次性坑 | 任务 NOTES.md（`sop.py lessons` 检索） | **不进维度** |

> 判定口诀：**"事实"按 `attribution` 归对应维度，"坑→正确做法"归 `project-lessons`，"一次性坑"留任务 NOTES**。
> 例行检查：`python sop.py audit --root .` 扫描环境事实误入非法维度、跨维度重复、交叉引用失效三类可疑点（合法维度集合从 `attribution["env-facts"]` 读取；只报可疑，最终裁决仍按归属表人工判定）。

## 2. 定位任务计划 → plan.md

- 必填：`task.id/title/type`、`objective`、`scope.included`、`acceptance`、`steps`。
- 可选：`assumptions`，仅在用户授权跳过关键边界澄清时记录所采用的关键假设。
- **台账字段**（`status` / `parent` / `depends` / `relates` / `supersedes` / `owner` / `claimed` / `updated`）：写在 plan.md 顶部字段表，由 `sop.py tasks` 聚合到 `ai/TASKS.md`。开任务写 `status: doing|pending`，收工改 `done` + `updated`；要让对方挡住我用 `depends`，只是同主题用 `relates`（详见 `reference/task-ledger.md`）。
- 缺失或字段不全 → 先补全再执行（light 可只写最小计划；台账字段缺失只会告警，不阻塞执行）。

## 3. 教训与实验（按模式）

- **standard/full**：先读 `NOTES.md` 历史决策与「教训」条目；遇阻先 `python sop.py lessons <task> <关键词>` 检索历史教训，再决定方案。
- **教训升级（维度 9）**：任务收尾复盘时，把 NOTES 中**满足升级门槛**（跨 ≥2 任务 + 与任务无关 + 环境/框架级）的教训补进 `context/09-project-lessons.md`（格式与维护见 `reference/project-lessons.md`），并同步 AGENTS.md 维度 9 摘要。
- **full**：写实验前先 `sop.py dedup` 查重；追加用 `sop.py record`；给人看的报告用 `sop.py render`（按需生成，不落盘存储）。

## 4. 审计笔记（standard/full）

- 只在**决策点**记录：改变方向、遇阻换方案、验收通过。**不记录每个工具调用。**
- **决策点前置**：改变方向 / 换方案前，先按 §0.1.1 对应力度做对抗自查，结论（含"暂未发现"）一并记录。
- 条目：时间戳 + 决策 + 依据 + 关联 plan 步骤/实验哈希。纠错用 CORRECTION 条目，不删历史。

## 5. 执行与收尾

```
循环：执行 plan 步骤 → 决策点追加 NOTES → (full) record 实验 → 更新 plan 状态
收尾：`python sop.py gates` 一次跑完五道门（context / dims / audit / entry / tasks），红了先修；对照 DoD
```

## sop.py 速查（位于本 skill 根目录）

| 命令 | 作用 |
|------|------|
| `python sop.py init [--root .]` | 初始化 ai/ 结构：manifest 缺失时写入内置 9 维度建议集 + 生成维度骨架 + AGENTS.md |
| `python sop.py scan [--dry-run] [--force] [--only 2,5]` | init 后自动推导：按 codeRoot 实际文件填充维度 1/2/4/5/6/8 的事实清单（标"自动推导，待复核"）+ 修复 0 命中锚点 + 填身份表；只写事实不做架构判断，已有手工内容默认跳过（`--force` 覆盖） |
| `python sop.py refresh [--mark-fresh] [--only 12,13]` | 按 manifest 锚点检测过期维度；`--mark-fresh` 封存锚点 + 版本递增 + 重写 AGENTS.md 生成区；`--only` 只封存指定维度（id 或 key，逗号/空格分隔），其余维度保持旧锚点 |
| `python sop.py check --context [--root .]` | 校验 manifest 结构 / manifest↔context 一致性 / 锚点命中率；启用 `entry` 时顺带做入口文件 lint（告警不阻塞） |
| `python sop.py entry [--check] [--force] [--out 路径]` | 按 manifest `entry` + 各维度 `entryHint` 生成/刷新仓库入口文件（生成区=纪律索引+任务路由+维护契约；手写区不动）；`--check` 校验行数/权威声明/凭据/时效状态/生成区是否同步 |
| `python sop.py tasks [--check] [--backfill [--dry-run] [--status S] [--no-suggest]] [--out 路径] [--json]` | 任务台账：扫 `ai/*/plan.md` 字段表（status/parent/depends/relates/supersedes/owner/claimed/updated）聚合为 `ai/TASKS.md`；`--check` 校验状态枚举/id 唯一/引用存在/成环 + 台账是否与 plan.md 同步 + 认领租约告警；`--backfill` 存量接入：给缺台账字段的老 plan.md 补行（**先 `--dry-run` 预览**，缺省 `pending` 并列入「需人工确认」，NOTES 有验收记录才推断 `done`，只打印关系建议不写入）；`--json` 只读导出全量快照（含派生 `depends_open` / `depends_missing` / `claim`），不写台账 |
| `python sop.py next [--json]` | **可执行前沿**：按 `status` + `depends` 算第 1 波（现在可做）/ 后续波次 / 阻塞原因（等谁、它是什么状态）/ 无法排序（引用缺失或成环）；`dropped` 前置不阻塞后继并在行内标注「前置已放弃」；**不排优先级、不推工期**（那是语义判断） |
| `python sop.py next --claim <id> [--as 名] [--lease 分] [--force]` / `--release <id>` | **认领/释放**：`--claim` 写 `owner`/`claimed` + `status=doing` + 建 `ai/<task>/.claim` 租约锁（同机 `O_EXCL` 原子，抢不到会报"刚被另一个会话抢到"）；未到期抢占要 `--force`，超时可接管；`--release` 删锁并回 `pending`。`tasks --check` 会对超时/缺锁/owner 不一致告警 |
| `python sop.py dedup <task> -m <method> -p '<json>'` | 实验查重（`-p` 也可用 `-p k=v` 重复多次，规避 shell 引号问题） |
| `python sop.py record <task> -m <method> -p '<json>' -r <result>` | 追加实验记录（同上） |
| `python sop.py render <task> [--out f.md]` | 从 JSONL 渲染人读报告 |
| `python sop.py check <task>` | 校验 JSONL 完整性（哈希/序号） |
| `python sop.py lessons <task> [关键词]` | 检索 NOTES.md 教训条目（按关键词过滤） |
| `python sop.py tokens <file...>` | 估算 token 量（中 ~1.5 字/token）；`refresh --mark-fresh` 自动写入 AGENTS.md 上下文估算 |
| `python sop.py dims <task_type> [--root .]` | 从 manifest 聚合查询任务类型必读/可选维度（免读索引） |
| `python sop.py audit [--root .] [--check]` | 维度归属审计：扫环境事实误入非法维度 / 跨维度重复 / 交叉引用失效三类可疑点（合法维度集合从 manifest `attribution["env-facts"]` 读取，只报可疑，判定按 §1.5 归属表人工裁决；`--check` 有问题时退出码非 0） |
| `python sop.py gates [--json] [--skip-if-missing]` | **五道门一次跑完**：context / dims（过期）/ audit / entry / tasks，逐项打 ✅❌~ 并给修法，任一失败退出码 1；`--json` 输出单行机器可读结果；`--skip-if-missing` 在未接入 SOP 的项目里安静放行（给全局 hook 用） |
| `python sop.py migrate [--root .] [--old-sop path] [--code-root path] [--dims-json path] [--dry-run] [--force]` | v3 → v4 半自动迁移：解析旧 sop.py 常量生成 manifest + 补维度文件摘要行 + AGENTS.md 分区改造（详见 `reference/migration-v3-to-v4.md`） |

> `python sop.py --help` 查看全部参数。所有产出 UTF-8、LF。变更检测基于文件哈希（工作区感知），不依赖 git。锚点 glob 相对 manifest 的 `codeRoot` 解析（缺省为 root；ai 产物目录与代码目录分离时务必设置）。

### 强制力：把门交给机制（可选，默认不装）

四道门最大的敌人不是难度，是"忘了跑"。`gates` 的价值就是把五条命令压成一个退出码，于是可以挂到机制上：

```sh
# ① git pre-commit（每个项目各装一次；.git/hooks/pre-commit 需 chmod +x）
#!/bin/sh
python path/to/skills/project-task-sop/sop.py gates --root "$(git rev-parse --show-toplevel)" --skip-if-missing || exit 1
```

```json
// ② Claude Code 项目级 hook（<project>/.claude/settings.json）：Stop 时跑一次，避免"说完了才发现门是红的"
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
        "command": "python path/to/skills/project-task-sop/sop.py gates --root . --skip-if-missing" } ] }
    ]
  }
}
```

- 都带 `--skip-if-missing`：没接 SOP 的项目直接放行，不改别人的工作流。
- **默认不自动安装**：hook 会改变每次会话/每次提交的行为，属于使用者的选择；SKILL 只提供片段。
- 本 skill 自身不持有强制力（无守护进程、无后台）。装了 hook 才有强制力，没装就只有纪律。

## 验收标准（DoD）

- [ ] 按模式产出齐全：light→plan；standard→+NOTES；full→+`.experiments.jsonl` 且 `sop.py check` 通过
- [ ] `python sop.py gates` 五道门全绿（entry 未配置时该门显示跳过，不算红）
- [ ] `ai/AGENTS.md` 反映当前项目状态，版本号已更新
- [ ] 关键边界已对齐；如使用直接执行例外，`plan.md` 的 `assumptions` 已记录关键假设
- [ ] 决策点均有 NOTES 记录，可追溯
- [ ] 关键决策点已按 §0.1.1 做对抗自查（或已【直接执行】豁免）
- [ ] 未重复已知失败的尝试；遇阻找到对策后已沉淀「教训」条目（dedup/lessons 已核对）
- [ ] 本次满足升级门槛的教训已补入维度 9 并同步 AGENTS.md 摘要（如有）

## 参考文档（按需加载，勿常驻）

- `reference/failure-modes.md` — 失败模式与恢复
- `reference/edge-cases.md` — 边缘情况处理
- `reference/tool-guide.md` — 工具使用指导
- `reference/project-lessons.md` — **维度 9 项目级教训机制**（升级门槛/格式/维护）
- `reference/migration-v3-to-v4.md` — **v3 → v4 迁移指南**（分步操作/验收清单/已知坑，配合 `sop.py migrate`）
- `reference/process-flow-convergence.md` — **流程收敛（聚合思维）**：仅流程梳理/再造类任务读取；流程要素筛选判定表 + 流程梳理表模板
- `reference/repo-entry-file.md` — **仓库入口文件（AGENTS.md 等）通用规范**：三种入口形态选型 / `entry`+`entryHint` 启用步骤 / 生成区与手写区分工 / 5 条 lint / 常见反模式 / 迁移检查表（仅做入口文件时读）
- `reference/task-ledger.md` — **任务台账机制**：字段词表（status/parent/depends/relates/supersedes）/ 校验规则 / 开任务与收工流程 / **存量项目接入（`tasks --backfill`）** / 与 AGENTS.md 手写表的分工 / 常见反模式（开任务、收工、接管老项目时读）
- `reference/changelog.md` — **版本历史详述**：每条变更的问题、修法、用例数与边界说明（本文只留主题；查"这个版本为什么这么改"时读）
- `templates/` — 文件模板（`sop.py init` 自动引用）

## 版本历史

> 主文档只留主题；每条的细节（问题、修法、用例数）见 `reference/changelog.md`。

| 版本 | 日期 | 主题 |
|------|------|------|
| 1.0.0 | 2026-08-20 | 初始 5 步流程、8 维度、3 模板 |
| 2.0.0 | 2026-08-21 | 模块化上下文、双轨实验、增量刷新 |
| 3.0.0 | 2026-08-23 | 决策门 light/standard/full + 真实 CLI |
| 3.1.0 | 2026-08-23 | 教训沉淀与 `lessons` 检索 |
| 3.2.0 | 2026-10-27 | 启动前边界对齐门 |
| 3.3.0 | 2026-10-28 | 维度 9「项目级教训」常驻 |
| 4.0.0 | 2026-10-28 | manifest 驱动维度体系 |
| 4.1.0 | 2026-10-28 | `migrate` v3→v4 迁移 |
| 4.2.0 | 2026-10-29 | 维度归属表 + `audit` |
| 4.3.0 | 2026-10-29 | 对抗自查清单 |
| 4.4.0 | 2026-10-29 | attribution 归属动态化 |
| 4.5.0 | 2026-10-29 | 流程收敛参考文档 |
| 4.5.1 | 2026-11-15 | 锚点抽样修复 + `refresh --only` |
| 4.5.2 | 2026-11-15 | 锚点扫描性能修复 |
| 4.6.0 | 2026-11-15 | `entry` 仓库入口文件生成 |
| 4.7.0 | 2026-11-15 | 任务台账 `tasks` |
| 4.7.1 | 2026-11-16 | 入口落点决策门 |
| 4.7.2 | 2026-11-16 | 台账存量补录 `--backfill` |
| 4.7.3 | 2026-11-16 | 文档与元数据同步 |
| 4.7.4 | 2026-11-17 | CI 与版本同步守卫 |
| 4.7.5 | 2026-11-17 | Python 3.9 兼容 + CI 矩阵 |
| 4.8.0 | 2026-11-17 | `scan` init 后自动推导 |
| 4.8.1 | 2026-11-17 | 触发面改写（P0-1） |
| 4.8.2 | 2026-11-17 | 安装器分发面（P0-3） |
| 4.8.3 | 2026-11-17 | plugin 双端打包（P0-3） |
| 4.9.0 | 2026-11-17 | `gates` 五道门（P1-1） |
| 4.10.0 | 2026-11-17 | `next` 可执行前沿（P1-2） |
| 4.11.0 | 2026-11-17 | 认领与租约（P1-2/P1-3） |
| 4.12.0 | 2026-11-17 | `tasks --json`（P1-3 收尾） |
| 4.12.1 | 2026-11-17 | `migrate` 补测试修缺陷（P2） |
| 4.12.2 | 2026-11-17 | 主文档瘦身 + token 预算守卫（P2） |
| 4.12.3 | 2026-11-17 | README 叙事重写 + 过时数字修正（P3） |

*Skill authored for DeepSeek Harness (DSH) — compatible with skill catalog v1*
