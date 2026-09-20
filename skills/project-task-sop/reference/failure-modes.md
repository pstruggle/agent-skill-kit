# 失败模式与恢复（v3.0）

> 仅在遇到问题或复盘时按需加载。诊断步骤从最便宜的开始。

| 失败模式 | 症状 | 诊断 | 恢复动作 |
|----------|------|------|----------|
| init 超时 | 维度分析 > 5min | bash/pwsh 超时；大仓库 glob 耗时 | `sop.py init` 只做脚手架，语义分析可拆分给 subagent 并行；提高工具超时 |
| plan.md 缺失 | 步骤 2 阻断 | 确认任务目录 | 用 `templates/plan.md.template` 建最小计划（light 模式） |
| JSONL 损坏 | `check` 报解析失败 | 逐行定位 | 手工修复该行；缺字段可从 NOTES 补全后重新 `check` |
| 哈希去重失效 | 重复无效尝试 | 核对 keyParams 是否含不稳定字段 | 确认排除了 seed/路径/时间戳/run_id；参数表达统一为同型（数值 vs 字符串） |
| 增量刷新遗漏 | 上下文陈旧导致决策失误 | `sop.py refresh` 对比锚点变化 | 重分析过期维度；`--mark-fresh` 封存；怀疑失真时手动全量重分析 8 个维度 |
| 编码/换行问题 | 乱码、解析失败 | 检查 UTF-8/BOM/LF | 统一 UTF-8 no-BOM + LF 重写（sop.py 产出天然符合） |
| 模板变量未替换 | 文件残留 `{{var}}` | 检查模板占位符 | `sop.py init --force` 重新渲染；内置兜底模板兜底 |
| 并发写入冲突 | JSONL 丢行 | 多任务同时 record | 单任务串行写入；多 agent 协作时每个任务目录只有一个写者 |
| 锚点误报 | 无关变更触发刷新 | 查看锚点差异明细 | 调整 ANCHOR_RULES 或手动更新维度文件锚点行（不误伤内容） |

## 常规检查序列（每周/复盘时）

```bash
python sop.py refresh --root .          # 上下文是否过期
python sop.py check <task>              # 实验记录完整性
python sop.py tokens ai/AGENTS.md ai/context/*.md   # 上下文体积是否膨胀
```
