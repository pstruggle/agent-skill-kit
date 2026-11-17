# 任务台账（ai/TASKS.md）

> 解决什么：任务之间没有关联、完成状态靠手抄、跨会话接不上。「哪个任务做完了」「这个改动是哪个任务的后续」在上下文里必须能查。

## 设计原则

- **单一事实源在任务自己目录**：`ai/<task>/plan.md` 顶部字段表。台账是**生成物**，禁止手改。
- **零手工**：`sop.py tasks` 扫 `ai/*/plan.md` 聚合；`refresh --mark-fresh` 顺带同步；`check --context` 顺带告警。
- **确定性输出**：同一批 plan.md 渲染出的台账逐字节相同，因此 `tasks --check` 能判断「台账是否过期」。
- **只记结构与状态，不记流水**：进度叙述留在 `NOTES.md`，台账一行一任务。

## 字段词表（写在 plan.md 顶部字段表）

| 字段 | 取值 | 作用 |
|------|------|------|
| `status` | `doing` / `pending` / `blocked` / `done` / `dropped` | 完成状态；别名（`in_progress`/`wip`/`completed`/`cancelled`…）自动归一 |
| `parent` | 任务 id 或 `-` | 拆分来源（子任务指向父任务） |
| `depends` | `[a, b]` 或 `-` | **阻塞前置**：对方没 `done` 不算真完成 |
| `relates` | `[a, b]` 或 `-` | 相关但**不阻塞**（同一主题、后续、互相引用） |
| `supersedes` | 任务 id 或 `-` | 本任务取代哪个旧任务 |
| `updated` | `YYYY-MM-DD` | 最近一次状态变更日期（人写，供台账排序/排查） |

> 判据：**要让对方挡住我 → `depends`；只是同一件事的不同侧面 → `relates`**。乱用 `depends` 会造出假阻塞。
> `relates` 是**无向**关系：双方都写也只在台账里出现一条（渲染时按 id 排序去重），不必担心重复登记。
> `depends` / `parent` / `supersedes` 是有向的：`depends` 一边写即可（谁被挡谁写），反向的「被依赖」列由工具自动算出。

## 校验规则（`sop.py tasks --check`）

| 级别 | 规则 |
|------|------|
| ❌ 错误（退出码 1） | `status` 不在枚举；任务 id 重复；`parent`/`depends`/`relates`/`supersedes` 指向不存在的任务；指向自己；`depends` 成环；台账与 plan.md 不同步 |
| ⚠️ 告警（不阻塞） | 缺 `status`（按 pending 处理）；缺 `task.title`；标 `done` 但 `depends` 里的前置还没 `done` |

## 工作流

```sh
# 开任务：从模板复制字段表，status 写 doing/pending，填 depends/relates
# 收工：把 plan.md 的 status 改 done、updated 改今天
python sop.py tasks          # 重生成 ai/TASKS.md
python sop.py tasks --check  # 结构 + 同步校验（可进 CI）
```

- `refresh --mark-fresh` 会自动重生成台账，但**不会**替你改状态：状态永远由人在 plan.md 里改。
- `check --context` 会顺带打印台账告警（不阻塞上下文校验）。

## 可执行前沿（`sop.py next`）

台账是表，`next` 才是队列：给定 `status` + `depends`，它算"现在能做什么、被谁挡着"。

```sh
python sop.py next          # 第 1 波（现在可做）/ 第 2..N 波（等前序）/ 阻塞原因 / 无法排序
python sop.py next --json   # 机器可读：ready / blocked / unsorted / waves
```

判定规则（都是机械的，不含判断）：

| 情形 | 归到哪 |
|------|--------|
| `status` 为 `doing` / `pending`，且 `depends` 里没有未完成项（`done` / `dropped` / 不存在都算已了结） | 第 1 波（现在可做） |
| 有未完成的 `depends` | 后续波次，行内标明「等 `x`(pending)」 |
| `depends` 已满足但 `status=blocked` | 外部阻塞（不是依赖问题，去查那个任务本身为什么 blocked） |
| `depends` 指向的任务不存在，或 `depends` 成环 | 无法排序（退出码仍是 0，不阻塞流程） |
| `status` 为 `done` / `dropped` | 不出现 |

**它不做的事（刻意的）**：不按重要性/紧急度排序、不推工期、不做 stale 告警。这三件都需要业务语义与主观阈值，脚本排了只会让人误以为"这就是该做的"。

### 认领（`next --claim` / `--release`）

`status: doing` 只能说明"有人在做"，不能说明是**谁**、是不是**还活着**。多会话/多机并行时，认领要有归属、时间和租约，否则两个人都在 `doing` 里闷头改同一份东西。

```sh
python sop.py next --claim <task-id>              # 认领：写 owner/claimed + status=doing + 建 .claim 锁
python sop.py next --claim <task-id> --as alice@pc --lease 90
python sop.py next --claim <task-id> --force      # 抢占未到期的认领（确认对方会话已死才用）
python sop.py next --release <task-id>            # 释放：删锁 + status=pending + owner/claimed 清空
```

落盘形态（两处，分工明确）：

| 载体 | 内容 | 谁写 |
|------|------|------|
| `plan.md` 的 `owner` / `claimed` | 人读的归属与认领时间，随台账渲染到 `TASKS.md` 的「负责人」列 | `next --claim`（工具），**别手改** |
| `ai/<task>/.claim`（JSON） | `task` / `owner` / `claimed` / `lease`：租约的权威记录 | `next --claim`（原子创建） |

规则与边界：

- **原子性只在本机保证**：`.claim` 用 `O_CREAT|O_EXCL` 创建，同机两个会话同时认领只有一个成功（失败方会看到"刚被另一个会话抢到"）。
- **跨机靠租约与版本库兜底**：`ai/` 进库的项目里，`.claim` 随提交同步；两台机器同时认领会产生一次 git 冲突，`tasks --check` 也会报 owner 与锁不一致。它**不是分布式锁**，是"冲突可见 + 超时可接管"。
- **租约默认 120 分钟**（`--lease` 可调）。到期不会自动改状态，但允许被直接接管（`claim` 会提示「接管（租约已过期）」）；未到期要抢必须显式 `--force`。
- **认领人标识**：`--as` > `$SOP_OWNER` > `用户名@主机名`。多机时别用同一个名字，否则冲突检测失去意义。
- **`tasks --check` 的认领告警**（不阻塞）：认领超时 / 有 owner 但缺锁 / owner 与锁不一致 / 已完成任务仍留锁。
- 只能认领"可执行"的任务：有未完成 `depends`、或已 `done`/`dropped` 都会被拒。

**已知取舍**：认领是**协作信号，不是互斥锁**——`sop.py` 不会阻止别人直接改同一个文件。要更强的保证只能上外部锁服务，那超出这个 skill 的定位（无依赖、纯本地）。

## 存量项目接入（已有任务没有台账字段）

老项目 `ai/*/plan.md` 通常只有 `task.id/task.title/task.type/objective`，没有 `status/depends/relates/updated`。**不要手抄**——逐份手改会漏字段、写错日期，且无法幂等重跑。

```sh
python sop.py tasks --backfill --dry-run   # ① 预览：哪些任务缺哪些字段、会写成什么
python sop.py tasks --backfill             # ② 落盘：补行（幂等，重跑只报「已齐全」）
# ③ 按「需人工确认」清单逐个改 status（doing/done/blocked/dropped）与 relates
python sop.py tasks && python sop.py tasks --check
```

补录规则（工具只做机械部分，不替你判断）：

| 字段 | 补什么 | 依据 |
|------|--------|------|
| `status` | **有证据才推断**：`NOTES.md` 含「验收通过 / 收工 / `[x] 验收` / 状态：done」→ `done`；否则写 `--status`（缺省 `pending`）并列入「需人工确认」 | 台账是状态事实源，不许猜 |
| `parent` / `depends` / `relates` | `-` | 关系必须人判断 |
| `updated` | `plan.md` 的**文件修改日期** | 客观时间，比手填可靠 |

- **关系建议不落盘**：`--backfill` 会扫每个任务的 `plan.md`/`NOTES.md`，若其中出现别的任务 id 或目录名，打印 `a → b（出现在 a/NOTES.md）` 作为 `relates` 候选；确认后才写进 plan.md（`--no-suggest` 可关掉）。
- **找不到字段表就跳过**（报 `未找到字段表`），不改写成别的格式：字段表是台账的解析契约，缺了要先按模板补表头。
- 顺序建议：**先补录，再按新流程干活**。把历史任务（多数已是 `done` 或 `dropped`）一次性归位、人工确认状态，台账才有可信基线；之后新任务走正常流程即可。

## 与 `ai/AGENTS.md` 的分工

- `ai/AGENTS.md` 手工区**不再维护任务表**：只留一句「台账见 `ai/TASKS.md` 及重生成命令」。
- 之前那张四列手抄表（任务 / 目录 / 类型 / 状态）是第二事实源，已删除——两张表必然漂。

## 常见反模式

1. **手改 `ai/TASKS.md`** → 下次 `tasks` 覆盖；`--check` 会报「不同步」。
2. **拿 `depends` 表达「顺序上想做在后面」** → 制造假阻塞，`tasks check` 会一直告警「done 但前置未完成」。
3. **一个任务一个目录却没有 `task.id`** → 台账用目录名兜底，但目录改名即变 id，跨任务引用会断。显式写 `task.id`。
4. **把台账当进度日志** → 台账只存当前状态，历史在 `NOTES.md` 与 git。
5. **只在一个会话里记** → 台账落盘在 `ai/`，跨会话/跨 agent 才有效；不落盘等于没记。
