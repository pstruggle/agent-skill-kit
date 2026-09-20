---
name: project-task-sop
description: 轻量项目任务执行规程：规模决策门（light/standard/full）、模块化上下文索引、JSONL 实验去重记录、教训检索、决策点审计笔记；sop.py 提供 init/refresh/dedup/record/render/check/lessons/tokens
whenToUse:
  - 开始新的项目任务前
  - 需要避免重复已知失败的尝试时
  - 接手陌生代码库需要快速建立上下文时
  - 跨会话长期项目需要共享上下文时
disable-model-invocation: false
---

# Project Task SOP v3.1 — 轻量任务执行规程

> **原则**：先判断该不该用，再决定用多重的流程。文书越少，Token 越省。
> **v3.0 变更**：① 决策门 light/standard/full；② 实验记录收敛为单一 JSONL 事实源，人读视图按需渲染；③ 真实 CLI `sop.py` 取代虚构命令；④ 主文档瘦身，细节移至 `reference/`。
> **v3.1 追加**：⑤ 教训沉淀条目（`## 教训:` + `sop.py lessons` 检索）；⑥ 上下文 token 账本（`mark-fresh` 自动记录估算）。

## 0. 决策门 — 每次任务先走这里

| 判断 | 模式 | 产出 | 适用 |
|------|------|------|------|
| 一次性小改动（<30min、<5 文件、不重复） | **light** | 仅 `plan.md` | 常规修 bug、小功能 |
| 多步迭代或会重复 | **standard** | `plan.md` + `NOTES.md`（决策点） | 功能开发、重构 |
| 长周期 / 实验性 / 需审计复盘 | **full** | `plan.md` + `NOTES.md` + `.experiments.jsonl` | 调参、探索、跨会话任务 |

- **light**：跳过步骤 3–4，直接执行。
- **standard**：记录决策点，不做实验索引。
- **full**：额外用 `sop.py record/dedup/render/check` 维护实验记录。
- 任务 <5 分钟、纯问答、或无多会话价值 → **不适用本 skill，直接干活，不写任何文书。**

## 目录结构

```
ai/
├── AGENTS.md                # 主索引：身份速览 + 维度摘要表 + 版本元数据
├── context/                 # 8 个维度文件，头部含变更锚点（供 refresh 检测）
├── {task}/
│   ├── plan.md              # 任务计划（必填字段见模板）
│   ├── NOTES.md             # 决策点审计笔记（standard/full）
│   └── .experiments.jsonl   # 实验记录唯一事实源（full）
└── templates/               # 可选：团队模板（优先于 skill 内置）
```

## 1. 读取/初始化上下文

1. `read("ai/AGENTS.md")` — 索引（~500 tokens）。
2. 按任务类型只读所需维度（索引内映射表：feature→1,2,7；bugfix→1,2,3,7；…）。
3. 无 `ai/` → 在本 skill 根目录执行 `python sop.py init`，然后按输出清单并行填充 8 个维度文件。
4. 检测到相关文件变更 → `python sop.py refresh` 查看过期维度，重分析后 `--mark-fresh` 封存。

## 2. 定位任务计划 → plan.md

- 必填：`task.id/title/type`、`objective`、`scope.included`、`acceptance`、`steps`。
- 缺失或字段不全 → 先补全再执行（light 可只写最小计划）。

## 3. 教训与实验（按模式）

- **standard/full**：先读 `NOTES.md` 历史决策与「教训」条目；遇阻先 `python sop.py lessons <task> <关键词>` 检索历史教训，再决定方案。
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
| `python sop.py init [--root .]` | 初始化 ai/ 结构 + 8 维度文件 + AGENTS.md（含锚点） |
| `python sop.py refresh [--mark-fresh]` | 按锚点哈希检测过期维度；重分析后 `--mark-fresh` 封存 |
| `python sop.py dedup <task> -m <method> -p '<json>'` | 实验查重（`-p` 也可用 `-p k=v` 重复多次，规避 shell 引号问题） |
| `python sop.py record <task> -m <method> -p '<json>' -r <result>` | 追加实验记录（同上） |
| `python sop.py render <task> [--out f.md]` | 从 JSONL 渲染人读报告 |
| `python sop.py check <task>` | 校验 JSONL 完整性（哈希/序号） |
| `python sop.py lessons <task> [关键词]` | 检索 NOTES.md 教训条目（按关键词过滤） |
| `python sop.py tokens <file...>` | 估算 token 量（中 ~1.5 字/token）；`refresh --mark-fresh` 自动写入 AGENTS.md 上下文估算 |
| `python sop.py dims <task_type>` | 查询任务类型必读/可选维度（免读索引） |

> `python sop.py --help` 查看全部参数。所有产出 UTF-8、LF。变更检测基于文件哈希（工作区感知），不依赖 git。

## 验收标准（DoD）

- [ ] 按模式产出齐全：light→plan；standard→+NOTES；full→+`.experiments.jsonl` 且 `sop.py check` 通过
- [ ] `ai/AGENTS.md` 反映当前项目状态，版本号已更新
- [ ] 决策点均有 NOTES 记录，可追溯
- [ ] 未重复已知失败的尝试；遇阻找到对策后已沉淀「教训」条目（dedup/lessons 已核对）

## 参考文档（按需加载，勿常驻）

- `reference/failure-modes.md` — 失败模式与恢复
- `reference/edge-cases.md` — 边缘情况处理
- `reference/tool-guide.md` — 工具使用指导
- `templates/` — 文件模板（`sop.py init` 自动引用）

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-08-20 | 初始 5 步流程、8 维度、3 模板 |
| 2.0.0 | 2026-08-21 | 模块化上下文、双轨实验索引、增量刷新 |
| 3.0.0 | 2026-08-23 | 决策门 light/standard/full；JSONL 单一事实源；真实 CLI sop.py；主文档瘦身；锚点哈希修复增量检测 |
| 3.1.0 | 2026-08-23 | 教训沉淀条目 + sop.py lessons 检索；mark-fresh 自动记录上下文 token 估算 |

*Skill authored for DeepSeek Harness (DSH) — compatible with skill catalog v1*
