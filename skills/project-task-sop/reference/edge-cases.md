# 边缘情况处理（v3.0）

| 情况 | 处理策略 |
|------|----------|
| 首次运行，无 `ai/` 目录 | `python sop.py init` 自动创建 |
| 非 Git 仓库 | 维度 3 标 `N/A`；锚点检测基于文件哈希不依赖 git，仍可用 |
| 单文件/极简项目 | 维度 7 标 `single-file`；通常直接走 light 模式，跳过全量 init |
| 私有/离线环境 | 维度 6 仅基于 lock 文件，跳过在线审计，标记 `offline` |
| monorepo | `init --root` 指向工作区根；glob 深度限制；锚点按工作区配置文件 |
| 多任务并行 | 每任务独立 `{task}/` 目录；共享 `ai/context/` 只读；实验记录各自独立 |
| 旧版单文件 AGENTS.md | 归档为 `AGENTS.md.bak`，重新 `sop.py init` |
| 团队模板 | `ai/templates/` 优先于 skill 内置 `templates/`（`sop.py` 自动按此顺序读取） |
| Windows 环境 | 编码统一 UTF-8 no-BOM；sop.py 为标准库实现，`python` 直接可用 |

## 注意事项

- `ai/` 目录不参与目录拓扑锚点（避免任务目录创建触发维度 1 误报）。
- `.experiments.jsonl` 是唯一事实源：**不要**另存一份 Markdown 版实验日志，人读视图用 `sop.py render` 按需生成。
- 维度文件头部锚点行是机器维护的：手工分析内容可以改，锚点行交给 `refresh --mark-fresh` 更新。
