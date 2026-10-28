---
name: project-task-sop
description: 轻量项目任务执行规程：启动前边界对齐、规模决策门（light/standard/full）、模块化上下文索引（manifest 驱动）、JSONL 实验去重记录、教训检索、决策点审计笔记；sop.py 提供 init/refresh/dedup/record/render/check/lessons/tokens/dims
whenToUse:
  - 开始新的项目任务前
  - 需要避免重复已知失败的尝试时
  - 接手陌生代码库需要快速建立上下文时
  - 跨会话长期项目需要共享上下文时
disable-model-invocation: false
---

# Project Task SOP v4.1 — 轻量任务执行规程

> **原则**：先判断该不该用，再决定用多重的流程。文书越少，Token 越省。
> **v3.x 基础**：决策门 light/standard/full；JSONL 单一事实源；教训沉淀 + `sop.py lessons`；上下文 token 账本；启动前边界对齐门；维度 9 项目级教训。
> **v4.0 变更**：⑩ 维度体系下沉为 **`ai/context.manifest.json` 单一事实源**——维度集合/锚点（glob）/任务映射全部由项目声明，sop.py 退化为纯执行器；⑪ AGENTS.md 分区：生成区（`<!-- SOP-GEN:BEGIN/END -->`）由 `refresh --mark-fresh` 重写，手工区（身份/任务记录/归档）不碰；⑫ 维度文件头部新增 `> **摘要**:`（agent 维护，索引表自动聚合）；⑬ `check --context` 校验 manifest 结构/一致性/锚点命中；⑭ 锚点 glob 相对 `codeRoot` 解析（支持 ai 产物目录与代码目录分离）。
> **v4.1 追加**：⑮ **`sop.py migrate`**（v3 → v4 半自动迁移）：AST 解析旧 sop.py 常量 → 生成 manifest + 补维度文件摘要行 + AGENTS.md 分区改造；⑯ 新增 `reference/migration-v3-to-v4.md` 升级手册（分步操作 / 验收清单 / 已知坑）。

## 0. 启动门 — 先判断是否适用，再对齐关键边界，最后决定流程

- 任务 <5 分钟、纯问答、或无多会话价值 → **不适用本 skill，直接干活，不写任何文书。**

### 0.1 边界对齐门（适用本 skill 的任务）

开始产生交付物或改变状态的步骤前，先识别是否缺少会实质影响目标、范围、交付形式、验收标准、关键约束、风险等级或不可逆操作的关键边界信息。

- 可通过项目文件、已有上下文或只读调查确认的，先查证并记录依据，不重复询问。
- 仍无法确认的，集中列出会阻塞执行的用户决策问题，并尽量给出建议选项。
- 关键边界明确前，不自行补全条件，不进入实现或正式产出阶段；只读调查不受限制。
- 执行中新暴露的依赖性问题，可以继续询问。
- 用户明确表示“先按你的理解执行/继续”或作出等价授权时，可解除等待，但须将关键假设记录到 `plan.md` 的 `assumptions`，并在交付时说明。

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
├── context.manifest.json   # 单一事实源：维度体系声明（id/key/title/anchors/tasks/related/codeRoot）
├── AGENTS.md                # 主索引：生成区（版本/索引表/映射表，sop 重写）+ 手工区（身份/任务记录）
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
3. 无 `ai/` 或 `ai/context.manifest.json` 缺失 → 在本 skill 根目录执行 `python sop.py init`（写入内置 9 维度建议集 manifest），然后按 manifest 填充维度文件。
4. 检测到相关文件变更 → `python sop.py refresh` 查看过期维度，重分析后 `--mark-fresh` 封存（同时重写 AGENTS.md 生成区）。
5. 新增/调整维度：直接编辑 `ai/context.manifest.json`（增删 dimensions 条目 + 建/删对应 `context/NN-key.md`），再 `python sop.py check --context` 校验、`refresh --mark-fresh` 封存。**无需改 sop.py 代码。**

## 2. 定位任务计划 → plan.md

- 必填：`task.id/title/type`、`objective`、`scope.included`、`acceptance`、`steps`。
- 可选：`assumptions`，仅在用户授权跳过关键边界澄清时记录所采用的关键假设。
- 缺失或字段不全 → 先补全再执行（light 可只写最小计划）。

## 3. 教训与实验（按模式）

- **standard/full**：先读 `NOTES.md` 历史决策与「教训」条目；遇阻先 `python sop.py lessons <task> <关键词>` 检索历史教训，再决定方案。
- **教训升级（维度 9）**：任务收尾复盘时，把 NOTES 中**满足升级门槛**（跨 ≥2 任务 + 与任务无关 + 环境/框架级）的教训补进 `context/09-project-lessons.md`（格式与维护见 `reference/project-lessons.md`），并同步 AGENTS.md 维度 9 摘要。
- **full**：写实验前先 `sop.py dedup` 查重；追加用 `sop.py record`；给人看的报告用 `sop.py render`（按需生成，不落盘存储）。

## 4. 审计笔记（standard/full）

- 只在**决策点**记录：改变方向、遇阻换方案、验收通过。**不记录每个工具调用。**
- 条目：时间戳 + 决策 + 依据 + 关联 plan 步骤/实验哈希。纠错用 CORRECTION 条目，不删历史。

## 5. 执行与收尾

```
循环：执行 plan 步骤 → 决策点追加 NOTES → (full) record 实验 → 更新 plan 状态
收尾：(full) sop.py check 校验记录一致；对照 DoD
```

## sop.py 速查（位于本 skill 根目录）

| 命令 | 作用 |
|------|------|
| `python sop.py init [--root .]` | 初始化 ai/ 结构：manifest 缺失时写入内置 9 维度建议集 + 生成维度骨架 + AGENTS.md |
| `python sop.py refresh [--mark-fresh]` | 按 manifest 锚点检测过期维度；`--mark-fresh` 封存锚点 + 版本递增 + 重写 AGENTS.md 生成区 |
| `python sop.py check --context [--root .]` | 校验 manifest 结构 / manifest↔context 一致性 / 锚点命中率 |
| `python sop.py dedup <task> -m <method> -p '<json>'` | 实验查重（`-p` 也可用 `-p k=v` 重复多次，规避 shell 引号问题） |
| `python sop.py record <task> -m <method> -p '<json>' -r <result>` | 追加实验记录（同上） |
| `python sop.py render <task> [--out f.md]` | 从 JSONL 渲染人读报告 |
| `python sop.py check <task>` | 校验 JSONL 完整性（哈希/序号） |
| `python sop.py lessons <task> [关键词]` | 检索 NOTES.md 教训条目（按关键词过滤） |
| `python sop.py tokens <file...>` | 估算 token 量（中 ~1.5 字/token）；`refresh --mark-fresh` 自动写入 AGENTS.md 上下文估算 |
| `python sop.py dims <task_type> [--root .]` | 从 manifest 聚合查询任务类型必读/可选维度（免读索引） |
| `python sop.py migrate [--root .] [--old-sop path] [--code-root path] [--dims-json path] [--dry-run] [--force]` | v3 → v4 半自动迁移：解析旧 sop.py 常量生成 manifest + 补维度文件摘要行 + AGENTS.md 分区改造（详见 `reference/migration-v3-to-v4.md`） |

> `python sop.py --help` 查看全部参数。所有产出 UTF-8、LF。变更检测基于文件哈希（工作区感知），不依赖 git。锚点 glob 相对 manifest 的 `codeRoot` 解析（缺省为 root；ai 产物目录与代码目录分离时务必设置）。

## 验收标准（DoD）

- [ ] 按模式产出齐全：light→plan；standard→+NOTES；full→+`.experiments.jsonl` 且 `sop.py check` 通过
- [ ] `ai/AGENTS.md` 反映当前项目状态，版本号已更新
- [ ] 关键边界已对齐；如使用直接执行例外，`plan.md` 的 `assumptions` 已记录关键假设
- [ ] 决策点均有 NOTES 记录，可追溯
- [ ] 未重复已知失败的尝试；遇阻找到对策后已沉淀「教训」条目（dedup/lessons 已核对）
- [ ] 本次满足升级门槛的教训已补入维度 9 并同步 AGENTS.md 摘要（如有）

## 参考文档（按需加载，勿常驻）

- `reference/failure-modes.md` — 失败模式与恢复
- `reference/edge-cases.md` — 边缘情况处理
- `reference/tool-guide.md` — 工具使用指导
- `reference/project-lessons.md` — **维度 9 项目级教训机制**（升级门槛/格式/维护）
- `reference/migration-v3-to-v4.md` — **v3 → v4 迁移指南**（分步操作/验收清单/已知坑，配合 `sop.py migrate`）
- `templates/` — 文件模板（`sop.py init` 自动引用）

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-08-20 | 初始 5 步流程、8 维度、3 模板 |
| 2.0.0 | 2026-08-21 | 模块化上下文、双轨实验索引、增量刷新 |
| 3.0.0 | 2026-08-23 | 决策门 light/standard/full；JSONL 单一事实源；真实 CLI sop.py；主文档瘦身；锚点哈希修复增量检测 |
| 3.1.0 | 2026-08-23 | 教训沉淀条目 + sop.py lessons 检索；mark-fresh 自动记录上下文 token 估算 |
| 3.2.0 | 2026-10-27 | 新增启动前边界对齐门；允许只读调查；直接执行例外须记录关键假设 |
| 3.3.0 | 2026-10-28 | 维度 9「项目级教训」：跨任务重复教训升级为常驻维度（所有任务必读）；sop.py/模板/DIMS 同步；新增 reference/project-lessons.md |
| 4.0.0 | 2026-10-28 | **manifest 驱动**：维度体系（集合/锚点 glob/任务映射）下沉为 ai/context.manifest.json；sop.py 纯执行化（init/refresh/check --context/dims）；AGENTS.md 分区生成（marker 生成区 + 手工区）；维度文件头部「摘要」字段；锚点相对 codeRoot 解析。设计稿：jiakao 项目 ai/sop-v4-manifest-design-2026-10-28.md |
| 4.1.0 | 2026-10-28 | **migrate 子命令**（v3 → v4 半自动迁移：AST 解析旧常量 → manifest + 摘要行 + AGENTS.md 分区）；新增 reference/migration-v3-to-v4.md |

*Skill authored for DeepSeek Harness (DSH) — compatible with skill catalog v1*
