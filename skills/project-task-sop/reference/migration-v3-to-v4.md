# v3 → v4 迁移指南（migration-v3-to-v4）

> 适用：仍使用 v3.x（维度体系写死在 sop.py）的老项目，升级到 v4.0（manifest 驱动）。
> 配套命令：`sop.py migrate`（半自动，v4.1+）；全手工路径见「第 2 节」。
> 设计依据：jiakao 项目 `ai/sop-v4-manifest-design-2026-10-28.md` §8；本指南是其可执行版。

## 1. 升级原则

- **skill 只换机制**：`sop.py` + `SKILL.md` 一起升级到 v4.x；只换脚本不换文档会踩空。
- **项目只改数据**：维度体系（集合 / 锚点 / 任务映射）全部下沉到 `ai/context.manifest.json`，sop.py 不再内置任何维度。
- **AGENTS.md 分区**：生成区（`<!-- SOP-GEN:BEGIN/END -->` 之间）由 `refresh --mark-fresh` 重写；手工区（身份速览 / 任务记录 / 归档 / 协作约定）sop 不碰。旧 AGENTS.md 里任何"手填"表格内容一律不进生成区。
- **一次性工具定位**：migrate 只做半自动化（盘点 → 生成 manifest → 补摘要行 → AGENTS.md 分区），codeRoot 核对、锚点噪音排除、未登记维度去留仍须人工。

## 2. 兼容范围

| 输入形态 | 支持 | 说明 |
|----------|------|------|
| 旧 sop.py（v3.2 / v3.3 常量结构） | ✅ 自动 | AST 解析 `DIMENSIONS` / `ANCHOR_RULES` / `TASK_TYPE_DIMS` / `RELATED_DIMS`；支持 `list(range(...))` 展开（旧 onboarding 形态） |
| 更早 v3.0/3.1 或改过代码的项目 | ⚠️ 需兜底 | `--dims-json` 提供维度数组（见 §3） |
| 旧 `ai/AGENTS.md` 缺失 | ✅ | 跳过版本/摘要提取（`_index.version=1`），手工区为空 |
| 半迁移状态（旧索引已登记新维度，旧常量没有） | ✅ | 生成 manifest 后提示「未登记维度文件」，由人工决定去留 |

## 3. migrate 命令

### 参数

| 参数 | 说明 |
|------|------|
| `--root <path>` | 项目根（含 `ai/` 的目录）；缺省当前目录 |
| `--old-sop <path>` | 旧版 sop.py 路径；缺省尝试本 skill 目录的 `sop.v33.bak.py` |
| `--code-root <path>` | 代码根目录，锚点相对它解析；**ai 产物目录与代码目录分离时必填**（缺省用 root，输出会打印核对提示） |
| `--dims-json <path>` | 兜底：维度 JSON（数组或 `{"dimensions": [...]}`，元素含 `id/key/title/anchors/tasks/related`） |
| `--dry-run` | 只打印将生成的 manifest 与摘要 / 分区计划，不落盘 |
| `--force` | 已存在 `context.manifest.json` 时覆盖（正常迁移不传，触发保护） |

### 命令做什么

1. **解析旧常量**（AST，安全求值，不执行旧代码）：`DIMENSIONS`、`ANCHOR_RULES`、`TASK_TYPE_DIMS`、`RELATED_DIMS`。
2. **生成 `ai/context.manifest.json`**：
   - 锚点转换：旧 `file` 规则（basename 精确）→ `**/name`；旧 `prefix` 规则（目录前缀）→ `dir/**`；维度 1 无规则时给 `["@tree"]`（沿用 v3 语义）；无锚点维度 → `[]`（手动维护）。
   - 任务映射反聚合：`TASK_TYPE_DIMS` → 各维度的 `tasks.must/optional`；`taskTypes` = 旧键列表。
   - `related` 沿用 `RELATED_DIMS`。
   - `_index`：版本号从旧 AGENTS.md 版本元数据提取（保证与历史连续），完整刷新时间同理；提取失败给占位。
   - `attribution`：写入默认归属表（信息类型→维度 key，见 SKILL.md §1.5；audit 依赖），迁移后按项目实际维度增删调整。
3. **补维度文件头部摘要**：从旧 AGENTS.md 索引表逐行提取摘要，写入各 `context/NN-key.md` 的 `> **摘要**:`（变更锚点行之后；已存在则跳过）。
4. **AGENTS.md 分区改造**：剔除旧文件的生成区段（版本元数据 / 维度索引 / 任务映射表）与头部，保留手工区（身份速览 / 任务记录 / 归档），组装为 v4 结构（生成区 marker 占位，由下一步 refresh 填充）。
5. **提示未登记维度文件**（旧索引有、旧常量没有的维度，如迁移中途新增的），由人工决定在 manifest 补条目或归档。

## 4. 分步操作

> 半自动路径（推荐）：第 3–5 步由 `sop.py migrate` 完成。

| 步骤 | 动作 | 说明 |
|------|------|------|
| 0 | 前置确认 | skill 升级到 v4.x（sop.py + SKILL.md 同步）；确认旧 `ai/AGENTS.md` + `context/` 完整可参照 |
| 1 | 备份 | `ai/` 整目录复制为 `ai_backup_v3x_<日期>/`；旧 sop.py 另存 `sop.v3x.bak.py`（留在 skill 根，也是 migrate 缺省输入） |
| 2 | 盘点 | 半自动：直接跑 migrate；全手工：从旧 sop.py 常量读出 id/key/title/anchors/tasks/related 全量 |
| 3 | 写 manifest | `python sop.py migrate --root <root> --old-sop <旧sop> --code-root <代码根>`（先 `--dry-run` 预览） |
| 4 | 摘要迁移 | migrate 自动完成（从旧索引表提取）；全手工则逐个 `context/NN-key.md` 在变更锚点行后补 `> **摘要**:` |
| 5 | AGENTS.md 分区 | migrate 自动完成；全手工则把生成区段删掉、手工区内容搬到 marker 之外 |
| 6 | 人工核对 | **codeRoot** 是否正确；泛化锚点噪音（`logs`、`uni_modules` 等是否被纳入，必要时改精确路径）；未登记维度文件的去留；**`attribution` 归属与项目维度是否匹配**（增删过维度则同步）；手工区内容是否完整 |
| 7 | 封存与验收 | `sop.py check --context` → `sop.py refresh --mark-fresh`（版本 +1、生成区填充）→ 二次 `refresh`（应全部「最新」）→ `sop.py dims <type>` 抽查与旧映射一致 |
| 8 | 收尾 | 手工区核对（身份速览 / 任务记录 / 归档）；如迁移中途新增过维度，按提示在 manifest 补条目并 refresh |

## 5. 验收清单（DoD）

- [ ] `sop.py check --context`：manifest 结构无误、manifest↔context 一致；「锚点 0 命中」警告逐条核对过（真实无该文件 → 保留并接受；误报 → 调整锚点）
- [ ] `refresh --mark-fresh`：版本号 = 旧版本 + 1（连续），AGENTS.md 生成区已填充（索引表 N 行、映射表正确）
- [ ] 二次 `refresh`：输出「所有维度最新」
- [ ] `dims feature` / `bugfix` / `onboarding` 抽查：与旧索引映射一致；onboarding = 1–N
- [ ] 手工区（身份 / 任务记录 / 归档）原样保留，未被生成区覆盖
- [ ] 旧 AGENTS.md 备份存在（`ai_backup_v3x_<日期>/`），迁移失败可回滚

## 6. 已知坑（实操沉淀）

| 坑 | 现象 | 解法 |
|----|------|------|
| `TASK_TYPE_DIMS` 解析失败 | 迁移后所有维度 `tasks` 为空 | 旧常量 onboarding 用 `list(range(...))`，需 v4.1+（支持 range 展开）；否则用 `--dims-json` 兜底 |
| codeRoot 未设置 | 全部锚点 0 命中，维度恒「过期」 | `--code-root` 指向代码根；锚点相对它解析 |
| 版本号断层 | 迁移后版本从 1 重新计数 | migrate 自动从旧 AGENTS.md 版本元数据提取；半迁移等提取失败时手工改 `_index.version` |
| 泛化 glob 噪音 | `uni_modules/*/package.json`、`backend/logs/**/pom.xml` 计入锚点 | 核对后改精确路径，或依赖内置 IGNORED_DIRS（logs / uni_modules 已内置） |
| 手填内容被覆盖 | refresh 后自定义表格消失 | 手填内容一律放生成区 marker 之外；migrate 的 AGENTS.md 分区只保留 marker 之外内容 |
| 0 命中警告误判为失败 | `check --context` 报「锚点声明失效」 | 该警告 ≠ 迁移失败：核对是"真实无此文件"（保留）还是"锚点写错"（修正）后，仍可 refresh 封存 |
| Windows 删目录报错 | `Remove-Item -Recurse -Force` 报 NonInteractive | 改用 python `shutil.rmtree(path, ignore_errors=True)` |

## 7. 参考

- 设计稿：jiakao 项目 `ai/sop-v4-manifest-design-2026-10-28.md`（§0 六项决策 / §3 manifest 规范 / §8 升级指南）
- 实操案例：jiakao 项目 2026-10-28 迁移（v3.3 → v4.0，含备份 `ai_backup_v33_20261028/`）
- 相关文档：`failure-modes.md`（回滚与恢复）、`tool-guide.md`（命令速查）
