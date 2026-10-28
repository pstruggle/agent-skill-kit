# 项目级教训维度（Dimension 9）— 机制说明

> 配套 `SKILL.md` 的维度 9（`ai/context/09-project-lessons.md`）。解决"同一个坑跨任务反复出现"的问题：
> 任务级教训（NOTES.md `## 教训:` 条目）只有 `sop.py lessons <task>` 指定任务名才能检索到，
> 而维度 9 是**会话启动即注入**的项目级常驻上下文——每次任务读 AGENTS.md 索引即可命中。

## 1. 定位与分工

| 层级 | 载体 | 检索方式 | 生命周期 |
|------|------|----------|----------|
| 任务级教训 | 任务 `NOTES.md` 的 `## 教训:` 条目 | `sop.py lessons <task> [关键词]` | 随任务目录存在 |
| 项目级教训（本维度） | `ai/context/09-project-lessons.md` | 会话启动读 AGENTS.md 索引即见（维度 9） | 常驻，随项目演进 |

## 2. 升级门槛（任务 NOTES → 维度 9）

满足 **全部三条** 才升级，避免全局文件膨胀：

1. **跨 ≥2 个任务重复出现**（同一坑在多个任务里踩过或预计会再踩）；
2. **与具体任务无关**（不是某次接口签名、某份数据格式等一次性细节）；
3. **环境/框架级事实**（构建工具路径、框架 API 变迁、平台约束、通用鉴权规则等）。

只出现 1 次的任务特有坑 → 留在任务 NOTES，用 `sop.py lessons` 检索。

## 3. 条目格式（极简，防 token 膨胀）

```markdown
- **<触发场景一句话>** → <正确做法一句话>（出处任务1 / 出处任务2）
```

示例：
```markdown
- **命令行编译后端报 JDK 版本错** → 先设 JAVA_HOME=C:\Users\...\.jdks\ms-21.0.12；默认 JDK8 必失败（payment-refactor / mini-virtual-pay / channel-promotion）
- **带 token 的接口调用返回 401 "客户端ID与Token不匹配"** → RuoYi-Vue-Plus 校验 header/param 的 clientid 与 token 一致，请求必须带 clientid 头（channel-promotion）
```

- 每条 ≤ 两行，落"触发场景 → 正确做法"，不写排查过程。
- 按主题分组（如：环境与构建 / 后端框架 / 前端 / 数据库 / 本地环境事实）。
- 同一主题条目合并同类项；新教训与已有条目重复时不重复追加。

## 4. 写入与维护流程

1. **任务收尾复盘时**（对照 DoD），把本次 NOTES 中已满足升级门槛的教训补进维度 9；
2. 同步更新 `ai/AGENTS.md` 中维度 9 的"摘要"单元格（一行，列新增主题）；
3. 版本递增：`python sop.py refresh --mark-fresh`（会 +1 版本并重算 token 预算）；
4. 维度 9 **无文件锚点**（经验性内容，不受代码文件变更触发）；refresh 对它的判定恒为"最新"，由人工复盘驱动更新。

## 5. 接线（模板已内置）

- `templates/AGENTS.index.md.template` 已含维度 9 行，且**所有任务类型的必读维度均含 9**（onboarding 为 1–9）；
- `sop.py` 的 `DIMENSIONS` / `TASK_TYPE_DIMS` / `RELATED_DIMS` / 内置兜底模板均已同步；
- 新项目 `python sop.py init` 会自动生成维度 9 文件骨架。

## 6. 纪律

- **宁缺毋滥**：升级门槛是硬条件；全局文件每多一条，每个会话就多一份注入成本。
- **token 预算**：维度 9 建议控制在 1–2 KB 内（约 700–1300 tokens）；超限时把最稳定的条目压缩合并，或拆分到子主题文件。
- **来源可追溯**：每条保留出处任务名，便于回查上下文（`sop.py lessons <出处任务> <关键词>`）。
