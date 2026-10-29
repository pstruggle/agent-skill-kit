"""
project-task-sop CLI v4.0 — 轻量任务执行规程的机械部分（Manifest 驱动）。

v4 核心变更（2026-10-28，不兼容 v3.x）：
- 维度体系（集合/锚点/任务映射）全部下沉为项目数据：ai/context.manifest.json 单一事实源。
- sop.py 退化为纯执行器：读 manifest、算哈希、渲染 AGENTS.md 生成区；不再内置 DIMENSIONS/ANCHOR_RULES/TASK_TYPE_DIMS。
- AGENTS.md 分区：生成区（<!-- SOP-GEN:BEGIN/END --> 之间）由 refresh --mark-fresh 重写；手工区（身份/任务记录/归档）sop 不碰。
- 维度文件头部新增 `> **摘要**:`（agent 维护），索引表由 sop 读取聚合。
- check 新增 --context：manifest 结构 + manifest↔context 一致性 + 锚点命中率校验。
- v4.4：维度归属判定表下沉为 manifest.attribution（信息类型→维度 key，init 写入、项目可覆盖）；audit 的合法维度集合改从 attribution 读取，不再硬编码编号；check --context 校验 attribution 引用的 key 存在；migrate 生成默认 attribution。

职责边界（沿用 v3）：
- 本脚本只做"机械"工作：脚手架、锚点哈希、实验查重、记录、渲染、校验、token 估算。
- "语义"分析（架构判断、技术栈解读等）由 AI agent 完成；脚本输出清单引导 agent 填充维度文件。

命令：
  init               初始化 ai/ 结构与维度文件（manifest 缺失时写入内置 9 维度建议集）
  refresh            按 manifest 锚点检测过期维度 [--mark-fresh 封存 + 重写 AGENTS.md 生成区]
  check --context    manifest 结构 + 一致性 + 锚点命中校验（check <task> 仍为 JSONL 校验）
  dedup <task>       实验查重（精确哈希）
  record <task>      追加实验记录到 .experiments.jsonl
  render <task>      从 JSONL 渲染人读 Markdown 报告（按需生成，不落盘存储）
  check <task>       校验 JSONL 完整性（哈希 / 序号）
  lessons <task>     检索任务 NOTES.md 中的教训沉淀条目
  tokens <file...>   token 量估算（中文 ~1.5 字/token，英文 ~4 字/token）
  dims <task_type>   从 manifest 聚合查询任务类型的必读/可选维度
  audit             维度归属审计：扫环境事实误入/跨维度重复/交叉引用失效（合法归属从 manifest attribution 读取；可疑点报告，判定按 §1.5 归属表）
  migrate            v3 → v4 半自动迁移：解析旧 sop.py 常量生成 manifest + 补维度摘要行

兼容：Python 3.8+，标准库，跨平台（Windows/macOS/Linux）。
"""
from __future__ import annotations

import argparse
import ast
import glob as globmod
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = SKILL_ROOT / "templates"

# ---------------------------------------------------------------------------
# 常量与内置建议集
# ---------------------------------------------------------------------------

# ai/ 为 skill 自身产物，不参与锚点与目录拓扑；logs/uni_modules 为常见噪音目录
IGNORED_DIRS = {"node_modules", ".git", "dist", "build", "target", ".venv",
                "__pycache__", ".dsh", ".idea", ".vscode", "ai", "logs", "uni_modules"}

ANCHOR_LINE_RE = re.compile(r"^>\s*\*\*变更锚点\*\*:\s*(\{.*\})", re.M)
SUMMARY_RE = re.compile(r"^>\s*\*\*摘要\*\*:\s*(.+)$", re.M)

GEN_BEGIN = "<!-- SOP-GEN:BEGIN -->"
GEN_END = "<!-- SOP-GEN:END -->"

MANIFEST_NAME = "context.manifest.json"

# init 时写入的内置建议集（9 维度，新项目可在此基础上增删）
DEFAULT_MANIFEST = {
    "_comment": "sop.py v4 维度体系声明（单一事实源）。id 决定编号与文件名 NN-key.md；anchors 为相对仓库根的 glob（[] = 手动维护，[\"@tree\"] = 目录树哈希）；tasks.must 含 \"*\" = 所有任务类型必读；onboarding 恒为全部维度；attribution 为信息类型→维度 key 的归属表（§1.5，audit 读取；缺省按此表兜底）。由 agent 维护，sop.py 只读。",
    "version": 2,
    "taskTypes": ["feature", "bugfix", "refactor", "deps", "ci-cd", "docs", "perf", "onboarding"],
    # 归属表：信息类型 → 维度 key（§1.5 默认值；项目增删维度时同步调整。
    # ui-conventions 内置建议集无对应维度，需要时由项目自定义并加入映射）。
    "attribution": {
        "env-facts": ["topology", "docs-assets", "dependencies", "dev-workflow"],
        "tech-usage": ["tech-stack"],
        "deps": ["dependencies"],
        "topology": ["topology"],
        "git": ["git-history"],
        "docs": ["docs-assets"],
        "quality": ["quality-system"],
        "architecture": ["architecture"],
        "ui-conventions": [],
        "lessons": ["project-lessons"],
    },
    "dimensions": [
        {"id": 1, "key": "topology", "title": "目录拓扑与关键文件",
         "anchors": ["@tree"],
         "tasks": {"must": ["*"], "optional": []},
         "related": [{"id": 2, "note": "依赖"}, {"id": 7, "note": "结构"}]},
        {"id": 2, "key": "tech-stack", "title": "技术栈识别",
         "anchors": ["**/package.json", "**/pom.xml", "**/pnpm-lock.yaml", "**/yarn.lock",
                     "**/Cargo.toml", "**/pyproject.toml", "**/go.mod"],
         "tasks": {"must": ["feature", "bugfix", "refactor", "deps", "ci-cd", "perf"],
                   "optional": ["docs"]},
         "related": [{"id": 6, "note": "依赖"}, {"id": 7, "note": "架构"}, {"id": 8, "note": "工作流"}]},
        {"id": 3, "key": "git-history", "title": "Git 历史与协作模式",
         "anchors": [],
         "tasks": {"must": ["bugfix", "refactor"], "optional": []},
         "related": [{"id": 7, "note": "模块演化"}]},
        {"id": 4, "key": "docs-assets", "title": "文档与知识资产",
         "anchors": ["README.md", "README.en.md", "docs/**", "adr/**", "architecture/**", "CHANGELOG.md"],
         "tasks": {"must": ["docs"], "optional": []},
         "related": [{"id": 1, "note": "文件分布"}]},
        {"id": 5, "key": "quality-system", "title": "测试与质量体系",
         "anchors": [".github/workflows/**", ".circleci/**"],
         "tasks": {"must": ["deps", "ci-cd", "perf"], "optional": ["feature", "bugfix", "refactor"]},
         "related": [{"id": 8, "note": "CI 流程"}, {"id": 2, "note": "测试框架"}]},
        {"id": 6, "key": "dependencies", "title": "依赖与供应链",
         "anchors": ["**/package.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/Cargo.lock",
                     "**/go.sum", "**/poetry.lock", "**/pom.xml"],
         "tasks": {"must": ["deps", "perf"], "optional": ["refactor", "ci-cd"]},
         "related": [{"id": 2, "note": "技术栈"}, {"id": 5, "note": "质量"}]},
        {"id": 7, "key": "architecture", "title": "架构模式与模块划分",
         "anchors": ["**/pom.xml", "pnpm-workspace.yaml", "go.work"],
         "tasks": {"must": ["feature", "bugfix", "refactor", "perf"], "optional": []},
         "related": [{"id": 1, "note": "拓扑"}, {"id": 2, "note": "技术栈"}]},
        {"id": 8, "key": "dev-workflow", "title": "开发工作流与环境",
         "anchors": ["Makefile", "justfile", "Taskfile.yml"],
         "tasks": {"must": ["ci-cd"], "optional": ["feature", "perf"]},
         "related": [{"id": 5, "note": "质量"}, {"id": 2, "note": "脚本语言"}]},
        {"id": 9, "key": "project-lessons", "title": "项目级教训沉淀",
         "anchors": [],
         "tasks": {"must": ["*"], "optional": []},
         "related": [{"id": 2, "note": "技术栈"}, {"id": 8, "note": "工作流"}]},
    ],
}

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def compute_experiment_hash(method: str, key_params: dict) -> str:
    """稳定、可复现的去重哈希。key_params 仅含影响结果的关键超参数
    （排除 seed、路径、时间戳、run_id 等不稳定字段）。"""
    normalized = {k: v for k, v in sorted(key_params.items()) if v is not None}
    canonical = method + "|" + json.dumps(normalized, sort_keys=True,
                                          separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def short_hash(full_hash: str, length: int = 16) -> str:
    return full_hash[:length]


def estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4)


def param_summary(params: dict) -> str:
    alias = {"learning_rate": "lr", "batch_size": "bs", "epochs": "ep",
             "lora_r": "r", "lora_alpha": "alpha"}
    parts = []
    for k, v in sorted(params.items()):
        parts.append("{}={}".format(alias.get(k, k), v))
    return ", ".join(parts)


def parse_params(values) -> dict:
    """解析参数：单个 JSON 对象，或逗号分隔/重复 -p 的 k=v 列表（数值自动转 int/float）。"""
    parts = [v for v in (values or []) if v]
    if not parts:
        return {}
    joined = ",".join(parts).strip()
    if not joined:
        return {}
    if joined.startswith("{"):
        try:
            obj = json.loads(joined)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            raise ValueError("参数 JSON 解析失败: {}".format(joined))
    result = {}
    for chunk in joined.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError("参数格式错误（应为 JSON 或 k=v）: {}".format(chunk))
        k, v = chunk.split("=", 1)
        k, v = k.strip(), v.strip()
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
        result[k] = v
    return result


def render_template(template: str, values: dict) -> str:
    def _sub(m):
        return str(values.get(m.group(1), m.group(0)))
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", _sub, template)


def read_template(name: str, root: Path):
    """优先团队模板 ai/templates/，其次 skill 内置 templates/。找不到返回 None。"""
    for base in (root / "ai" / "templates", TEMPLATES_DIR):
        p = base / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return None


def tree_anchor(root: Path, max_depth: int = 3) -> str:
    """目录树哈希（深度限制，忽略构建产物与 ai/）。"""
    rel_dirs = []
    for dirpath, dirnames, _ in os.walk(str(root)):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        rel = os.path.relpath(dirpath, str(root))
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames[:] = []
            continue
        rel_dirs.append(rel.replace(os.sep, "/"))
    rel_dirs.sort()
    return sha256_text("\n".join(rel_dirs))


# ---------------------------------------------------------------------------
# Manifest 加载与校验
# ---------------------------------------------------------------------------


def manifest_path(root: Path) -> Path:
    return root / "ai" / MANIFEST_NAME


def load_manifest(root: Path) -> dict:
    p = manifest_path(root)
    if not p.is_file():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit("! {} JSON 解析失败: {}".format(p, e))
    if not isinstance(m, dict) or "dimensions" not in m:
        raise SystemExit("! {} 缺少 dimensions 字段".format(p))
    return m


def save_manifest(root: Path, manifest: dict) -> None:
    manifest_path(root).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dim_file_name(dim: dict) -> str:
    return "{:02d}-{}.md".format(int(dim["id"]), dim["key"])


def dim_file_path(root: Path, dim: dict) -> Path:
    return root / "ai" / "context" / dim_file_name(dim)


# ---------------------------------------------------------------------------
# 锚点：变更检测（manifest 驱动，glob 展开，不依赖 git diff）
# ---------------------------------------------------------------------------


def glob_hits(root: Path, pattern: str) -> list:
    """展开相对仓库根的 glob，过滤 IGNORED_DIRS，返回相对路径 posix 串列表（已排序）。"""
    full = root / pattern
    hits = []
    for raw in globmod.glob(str(full), recursive=True):
        f = Path(raw)
        if not f.is_file():
            continue
        try:
            rel = f.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        hits.append(rel.as_posix())
    return sorted(hits)


def anchor_sources(code_root: Path, dim: dict) -> dict:
    """当前锚点: {relpath: \"sha256:...\"}，路径相对代码根 code_root。
    维度 anchors=[\"@tree\"] 用目录树哈希（扫代码根）；[] 返回空。"""
    anchors = dim.get("anchors") or []
    if anchors == ["@tree"]:
        return {"tree": "sha256:" + tree_anchor(code_root)}
    out = {}
    count = 0
    for pat in anchors:
        for rel in glob_hits(code_root, pat):
            if count >= 20:
                return out
            out[rel] = "sha256:" + file_sha256(code_root / rel)
            count += 1
    return out


def parse_anchors(dim_file: Path) -> dict:
    if not dim_file.is_file():
        return {}
    try:
        text = dim_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = ANCHOR_LINE_RE.search(text)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def write_anchors(dim_file: Path, anchors: dict) -> None:
    line = "> **变更锚点**: " + json.dumps(anchors, sort_keys=True, ensure_ascii=False)
    try:
        text = dim_file.read_text(encoding="utf-8")
    except OSError:
        return
    if ANCHOR_LINE_RE.search(text):
        text = ANCHOR_LINE_RE.sub(lambda _m: line, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + line + "\n"
    dim_file.write_text(text, encoding="utf-8")


def diff_anchors(stored: dict, current: dict) -> list:
    diffs = []
    for k, v in current.items():
        if k not in stored:
            diffs.append("新增: {} ({})".format(k, v[:20]))
        elif stored[k] != v:
            diffs.append("变更: {} ({} → {})".format(k, stored[k][:12], v[:12]))
    for k in stored:
        if k not in current:
            diffs.append("移除: {}".format(k))
    return diffs


def read_summary(dim_file: Path) -> str:
    if not dim_file.is_file():
        return "（缺失）"
    m = SUMMARY_RE.search(dim_file.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else "待分析"


# ---------------------------------------------------------------------------
# 任务映射聚合与生成区渲染
# ---------------------------------------------------------------------------


def aggregate_task_map(dims: list, task_types: list) -> dict:
    """从 manifest 聚合 taskType -> (must_ids, optional_ids)。onboarding 恒为全部维度。"""
    result = {}
    all_ids = [d["id"] for d in dims]
    for t in task_types:
        must = [d["id"] for d in dims
                if "*" in d["tasks"]["must"] or t in d["tasks"]["must"]]
        opt = [d["id"] for d in dims
               if t in d["tasks"]["optional"] and d["id"] not in must]
        result[t] = (must, opt)
    result["onboarding"] = (all_ids, [])
    return result


def tasks_label(dim: dict, task_types: list) -> str:
    must = dim["tasks"]["must"]
    opt = dim["tasks"]["optional"]
    if "*" in must:
        return "所有"
    order = [t for t in task_types if (t in must or t in opt) and t != "onboarding"]
    return "/".join(order)


def context_tokens(ai_dir: Path) -> int:
    """AGENTS.md + 全部维度文件的 token 估算合计。"""
    total = 0
    idx = ai_dir / "AGENTS.md"
    if idx.is_file():
        total += estimate_tokens(idx.read_text(encoding="utf-8"))
    ctx = ai_dir / "context"
    if ctx.is_dir():
        for f in sorted(ctx.glob("*.md")):
            try:
                total += estimate_tokens(f.read_text(encoding="utf-8"))
            except OSError:
                pass
    return total


def render_generated(manifest: dict, dims: list, task_types: list, ai_dir: Path) -> str:
    idx = manifest.get("_index", {})
    task_map = aggregate_task_map(dims, task_types)
    tokens = context_tokens(ai_dir)

    lines = []
    lines.append("## 版本元数据")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("|------|-----|")
    lines.append("| **版本** | {} |".format(idx.get("version", 1)))
    lines.append("| **完整刷新** | {} |".format(idx.get("lastFullRefresh", "-")))
    lines.append("| **最近增量刷新** | {} |".format(idx.get("lastIncrementalRefresh", "-")))
    lines.append("| **上下文估算** | ~{} tokens |".format(tokens))
    lines.append("")
    lines.append("## 维度文件索引")
    lines.append("")
    lines.append("| # | 维度 | 文件 | 摘要 | 适用任务 |")
    lines.append("|---|------|------|------|----------|")
    for d in dims:
        f = ai_dir / "context" / dim_file_name(d)
        lines.append("| {} | {} | `{}` | {} | {} |".format(
            d["id"], d["title"], "context/" + dim_file_name(d),
            read_summary(f), tasks_label(d, task_types)))
    lines.append("")
    lines.append("## 任务类型 → 维度映射")
    lines.append("")
    lines.append("| 任务类型 | 必读 | 可选 |")
    lines.append("|----------|------|------|")
    for t in task_types:
        must, opt = task_map[t]
        lines.append("| {} | {} | {} |".format(
            t, ", ".join(str(i) for i in must) or "-",
            ", ".join(str(i) for i in opt) or "-"))
    lines.append("")
    lines.append("*由 project-task-sop v4.0 生成（manifest 驱动）；维度摘要由 agent 在维度文件头部维护；上下文估算为 AGENTS.md + 全部维度文件合计。*")
    return "\n".join(lines)


def rewrite_generated_section(idx_path: Path, new_section: str) -> None:
    text = idx_path.read_text(encoding="utf-8")
    if GEN_BEGIN not in text or GEN_END not in text:
        raise SystemExit("! AGENTS.md 缺少生成区 marker（{} ... {}），请先 init 或手工补 marker".format(
            GEN_BEGIN, GEN_END))
    head, rest = text.split(GEN_BEGIN, 1)
    _, tail = rest.split(GEN_END, 1)
    idx_path.write_text(head + GEN_BEGIN + "\n" + new_section + "\n" + GEN_END + tail,
                        encoding="utf-8")


# ---------------------------------------------------------------------------
# 命令：init
# ---------------------------------------------------------------------------

BUILTIN_INDEX_TEMPLATE = """# Project Context: {{projectName}}

> project-task-sop v4.0 | manifest 驱动（ai/{{manifestName}}）| 生成于 {{timestamp}}
> 维护命令：`python sop.py init` / `python sop.py refresh [--mark-fresh]`

{{genSection}}

## 项目身份速览

| 字段 | 值 |
|------|-----|
| 项目名称 | {{projectName}} |
| 主语言 | 待分析 |
| 包管理器 | 待分析 |
| 架构风格 | 待分析 |
| 分支模型 | 待分析 |
| 任务运行器 | 待分析 |

## 任务记录

| 任务 | 目录 | 类型 | 状态 |
|------|------|------|------|
"""

BUILTIN_DIM_TEMPLATE = """# Dimension {{dimensionNumber}}: {{dimensionTitle}}

> **维度元数据**：生成于 {{timestamp}} | 置信度 {{confidence}} | 技能 v4.0
> **变更锚点**: {{anchorsJson}}
> **摘要**: 待分析

## 维度内容

<!-- AGENT: 基于项目实际填充语义分析。锚点对应文件变化时会提示本维度过期。 -->

{{dimensionContent}}

## 相关维度

| 维度 | 关系 | 说明 |
|------|------|------|
{{relatedRows}}
"""


def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    ctx_dir = root / "ai" / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    project_name = args.project or root.name
    now = now_iso()

    mf = load_manifest(root)
    if mf is None:
        mf = json.loads(json.dumps(DEFAULT_MANIFEST))  # 深拷贝
        mf["_index"] = {"version": 1, "lastFullRefresh": now, "lastIncrementalRefresh": "-"}
        save_manifest(root, mf)
        print("+ ai/{}（内置 9 维度建议集）".format(MANIFEST_NAME))
    else:
        print("! ai/{} 已存在，跳过".format(MANIFEST_NAME))
    code_root = Path(mf.get("codeRoot", root))

    for dim in mf["dimensions"]:
        dim_path = dim_file_path(root, dim)
        if dim_path.exists() and not args.force:
            print("! {} 已存在，跳过".format(dim_path.name))
            continue
        tmpl = read_template("AGENTS.dimension.md.template", root)
        if tmpl is None:
            tmpl = BUILTIN_DIM_TEMPLATE
        anchors = anchor_sources(code_root, dim)
        related_rows = "\n".join(
            "| {} | 相关 | {} |".format(r["id"], r["note"]) for r in dim.get("related", [])) or "-"
        values = {
            "dimensionNumber": dim["id"], "dimensionTitle": dim["title"],
            "timestamp": now, "confidence": "pending",
            "anchorsJson": json.dumps(anchors, sort_keys=True, ensure_ascii=False),
            "dimensionContent": "", "relatedRows": related_rows,
        }
        dim_path.write_text(render_template(tmpl, values), encoding="utf-8")
        print("  + {}".format(dim_path.name))

    idx_path = root / "ai" / "AGENTS.md"
    if idx_path.exists() and not args.force:
        print("! ai/AGENTS.md 已存在，跳过（--force 重建生成区）")
    else:
        gen = render_generated(mf, mf["dimensions"], mf["taskTypes"], root / "ai")
        tmpl = read_template("AGENTS.index.md.template", root)
        if tmpl is None:
            tmpl = BUILTIN_INDEX_TEMPLATE
        values = {
            "projectName": project_name, "timestamp": now, "manifestName": MANIFEST_NAME,
            "genSection": GEN_BEGIN + "\n" + gen + "\n" + GEN_END,
        }
        idx_path.write_text(render_template(tmpl, values), encoding="utf-8")
        print("  + ai/AGENTS.md")
    return 0


# ---------------------------------------------------------------------------
# 命令：refresh
# ---------------------------------------------------------------------------


def cmd_refresh(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    code_root = Path(mf.get("codeRoot", root))
    ctx_dir = root / "ai" / "context"
    stale = []
    for dim in mf["dimensions"]:
        dim_path = dim_file_path(root, dim)
        if not dim_path.exists():
            print("- dim {} {}: 缺失（请 init 或手动创建）".format(dim["id"], dim["title"]))
            stale.append(dim)
            continue
        stored = parse_anchors(dim_path)
        current = anchor_sources(code_root, dim)
        diffs = diff_anchors(stored, current)
        if diffs:
            print("- dim {} {}: 过期 ({} 处锚点变化)".format(dim["id"], dim["title"], len(diffs)))
            for d in diffs[:10]:
                print("    {}".format(d))
            stale.append(dim)
        else:
            print("+ dim {} {}: 最新".format(dim["id"], dim["title"]))

    if args.mark_fresh:
        mf.setdefault("_index", {})
        mf["_index"]["version"] = int(mf["_index"].get("version", 1)) + 1
        mf["_index"]["lastIncrementalRefresh"] = now_iso()
        save_manifest(root, mf)
        for dim in mf["dimensions"]:
            dim_path = dim_file_path(root, dim)
            if dim_path.exists():
                write_anchors(dim_path, anchor_sources(code_root, dim))
        gen = render_generated(mf, mf["dimensions"], mf["taskTypes"], root / "ai")
        rewrite_generated_section(root / "ai" / "AGENTS.md", gen)
        print("\n✅ 锚点已封存，AGENTS.md 生成区已重写（版本 {}）".format(mf["_index"]["version"]))
    elif stale:
        print("\n提示: 重分析过期维度后运行 `python sop.py refresh --mark-fresh` 封存")
    else:
        print("\n✅ 所有维度最新")
    return 0


# ---------------------------------------------------------------------------
# 命令：check（--context 或 JSONL）
# ---------------------------------------------------------------------------


def cmd_context_check(root: Path) -> int:
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    ok = True
    ctx_dir = root / "ai" / "context"
    code_root = Path(mf.get("codeRoot", root))

    # 结构校验
    ids, keys = [], []
    for dim in mf["dimensions"]:
        d = dim["id"]
        if not isinstance(d, int) or d <= 0:
            print("! 维度 id 非法: {}".format(d)); ok = False
        if d in ids:
            print("! 维度 id 重复: {}".format(d)); ok = False
        ids.append(d)
        k = dim.get("key", "")
        if not k or not re.fullmatch(r"[a-z0-9-]+", k):
            print("! 维度 {} key 非法: {}".format(d, k)); ok = False
        if k in keys:
            print("! 维度 key 重复: {}".format(k)); ok = False
        keys.append(k)
        for slot in ("must", "optional"):
            for t in dim.get("tasks", {}).get(slot, []):
                if t != "*" and t not in mf["taskTypes"]:
                    print("! 维度 {} tasks.{} 引用未知任务类型: {}".format(d, slot, t)); ok = False

    # attribution 校验（v4.4+）：信息类型引用的维度 key 必须存在于 dimensions
    attr = mf.get("attribution")
    if attr is None:
        print("! manifest 缺少 attribution（v4.4+ 字段：信息类型→维度 key 归属表，audit 依赖；建议按 §1.5 默认表补充）")
        ok = False
    elif not isinstance(attr, dict):
        print("! attribution 必须是对象（信息类型 → 维度 key 列表）"); ok = False
    else:
        for info_type, dim_keys in attr.items():
            if not isinstance(dim_keys, list) or not all(isinstance(x, str) for x in dim_keys):
                print("! attribution.{} 必须是维度 key 字符串列表".format(info_type)); ok = False
                continue
            for k in dim_keys:
                if k not in keys:
                    print("! attribution.{} 引用未知维度 key: {}（须在 dimensions 声明）".format(info_type, k)); ok = False

    # 一致性：manifest 声明 ↔ 实际文件
    declared = {dim_file_name(d): d for d in mf["dimensions"]}
    for name in declared:
        if not (ctx_dir / name).is_file():
            print("! 声明缺失: context/{}（manifest 有声明但文件不存在）".format(name)); ok = False
    if ctx_dir.is_dir():
        for f in sorted(ctx_dir.glob("*.md")):
            if f.name not in declared:
                print("! 未登记: context/{}（文件存在但 manifest 未声明）".format(f.name))

    # 锚点命中率
    for dim in mf["dimensions"]:
        anchors = dim.get("anchors") or []
        if not anchors or anchors == ["@tree"]:
            continue
        hits = 0
        for pat in anchors:
            hits += len(glob_hits(code_root, pat))
        if hits == 0:
            print("! 锚点声明失效: dim {} {}（{} 全部 0 命中）".format(
                dim["id"], dim["title"], ", ".join(anchors)))
            ok = False

    print("manifest: {} 维度 / {} 任务类型".format(len(ids), len(mf["taskTypes"])))
    print("✅ 上下文校验通过" if ok else "❌ 存在问题（见上）")
    return 0 if ok else 1


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    if args.context:
        return cmd_context_check(root)
    task = resolve_task(root, args.task)
    if task is None:
        return 1
    p = task / ".experiments.jsonl"
    if not p.is_file():
        print("（无 .experiments.jsonl）")
        return 0
    ok = True
    count = 0
    for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        count += 1
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            print("! 第 {} 行 JSON 解析失败".format(line_no))
            ok = False
            continue
        calc = compute_experiment_hash(r.get("method", ""), r.get("keyParams", {}))
        if calc != r.get("hash"):
            print("! 第 {} 行 哈希不匹配 (index {})".format(line_no, r.get("index")))
            ok = False
    indexes = sorted(r.get("index") for r in load_records(task))
    if indexes and indexes != list(range(1, len(indexes) + 1)):
        print("! 序号不连续: {}".format(indexes))
        ok = False
    print("{} 条记录".format(count))
    print("✅ 校验通过" if ok else "❌ 存在问题")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 命令：dims
# ---------------------------------------------------------------------------


def cmd_dims(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    if args.task_type not in mf["taskTypes"]:
        print("未知任务类型: {}（可选：{}）".format(
            args.task_type, ", ".join(sorted(mf["taskTypes"]))))
        return 1
    task_map = aggregate_task_map(mf["dimensions"], mf["taskTypes"])
    req, opt = task_map[args.task_type]
    print("任务类型 {}:".format(args.task_type))
    print("  必读: {}".format(", ".join(str(d) for d in req)))
    print("  可选: {}".format(", ".join(str(d) for d in opt) or "-"))
    return 0


# ---------------------------------------------------------------------------
# 命令：audit（维度归属审计 — 可疑点报告，非自动判定）
# ---------------------------------------------------------------------------

# 元组：(标签, 正则) —— 合法维度集合由 manifest attribution["env-facts"] 决定（缺省按内置表兜底）。
# 内置默认：环境事实可合法出现于 dev-workflow（工作流与环境）/dependencies（依赖）/topology/docs-assets（列项目路径属本职）。
# 降噪：盘符路径只认反斜杠（避免把 https:// 的 "s:/" 当盘符）；IP 要求带端口（避免把 4 段版本号当 IP）。
ENV_PATTERNS = [
    ("盘符路径", re.compile(r"\b[A-Za-z]:\\")),
    ("远程地址(IP:端口)", re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}")),
    ("端口", re.compile(r":\d{2,5}\b")),
    ("凭据词", re.compile(r"clientid|secret|密码|凭据", re.I)),
    ("工具/产物路径", re.compile(r"\.jdks|\.m2|nvm|BtSoft|ruoyi-admin\.jar|\.env")),
]


def env_facts_legal_ids(mf: dict) -> set:
    """env-facts 信息类型的合法维度 id 集合：按 manifest attribution 解析（key→id），缺省按内置表兜底。"""
    attr = mf.get("attribution") or {}
    keys = attr.get("env-facts")
    if keys is None:
        keys = DEFAULT_MANIFEST["attribution"]["env-facts"]
    key_set = set(keys)
    return {d["id"] for d in mf["dimensions"] if d.get("key") in key_set}

# 提取"具体环境值"用于跨维度重复检测（IP 同样要求带端口，避免版本号当重复值）
VALUE_RE = [
    re.compile(r"(clientid|secret)\s*=\s*(\S+)", re.I),
    re.compile(r"([A-Za-z]:\\[^\s,，；;|]+)"),
    re.compile(r"((?:\d{1,3}\.){3}\d{1,3}:\d{2,5})"),
]


def cmd_audit(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! {} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    dim_text = {}
    for dim in mf["dimensions"]:
        f = dim_file_path(root, dim)
        dim_text[dim["id"]] = f.read_text(encoding="utf-8") if f.is_file() else ""
    issues = []

    # 已裁决豁免（ai/.sop-audit-ignore.json）：命中但确认合理者（如教训内嵌必要事实），仅提示不入退出码
    ignored_env = set()
    ign = root / "ai" / ".sop-audit-ignore.json"
    if ign.is_file():
        try:
            ignored_env = set(json.loads(ign.read_text(encoding="utf-8")).get("envFacts", []))
        except (json.JSONDecodeError, OSError):
            pass

    # 1) 环境事实误入非法维度（合法集合来自 attribution；跳过头部元数据行 > ** ...）
    legal_env = env_facts_legal_ids(mf)
    for dim in mf["dimensions"]:
        body = "\n".join(l for l in dim_text[dim["id"]].splitlines()
                         if not l.lstrip().startswith("> **"))
        hit_kinds = [name for name, pat in ENV_PATTERNS
                     if dim["id"] not in legal_env and pat.search(body)]
        if hit_kinds:
            msg = "[环境事实→应归env-facts维度] dim {} {}：命中 {}".format(
                dim["id"], dim["title"], ", ".join(hit_kinds))
            if dim["id"] in ignored_env:
                print("- " + msg + " 【已裁决豁免，见 ai/.sop-audit-ignore.json】")
            else:
                issues.append(msg)

    # 2) 交叉引用失效（引用不存在的维度 id）
    ref_re = re.compile(r"见\s*0?(\d{1,2})\s*维度|维度\s*0?(\d{1,2})")
    valid_ids = {d["id"] for d in mf["dimensions"]}
    for dim in mf["dimensions"]:
        for m in ref_re.finditer(dim_text[dim["id"]]):
            rid = int(next(g for g in m.groups() if g))
            if rid not in valid_ids:
                issues.append("[引用→维度{}不存在] dim {}：{}".format(rid, dim["id"], m.group(0)))

    # 3) 跨维度重复的具体环境值
    seen = {}
    for dim in mf["dimensions"]:
        for re_ in VALUE_RE:
            for m in re_.finditer(dim_text[dim["id"]]):
                v = (m.group(2) if m.lastindex == 2 else m.group(1)).rstrip("，,；;。")
                if v:
                    seen.setdefault(v, set()).add(dim["id"])
    for v, dims in sorted(seen.items()):
        if len(dims) > 1:
            issues.append("[跨维度重复] {} 出现于 dim {}".format(
                v, ",".join(str(d) for d in sorted(dims))))

    if not issues:
        print("✅ 未发现维度归属可疑点（{} 维度）".format(len(mf["dimensions"])))
        return 0
    for it in issues:
        print("! " + it)
    print("⚠ {} 处可疑点（按 SKILL.md §1.5 判定表人工裁决，`--check` 时发现问题退出码 1）".format(len(issues)))
    return 1 if args.check else 0


# ---------------------------------------------------------------------------
# 命令：migrate（v3 → v4 半自动迁移，一次性工具）
# ---------------------------------------------------------------------------


MIGRATE_COMMENT = (
    "sop.py v4 维度体系声明（单一事实源）。id 决定编号与文件名 NN-key.md；anchors 为相对 codeRoot 的 glob"
    "（[] = 手动维护，[\"@tree\"] = 目录树哈希）；tasks.must 含 \"*\" = 所有任务类型必读；onboarding 恒为全部维度；"
    "attribution 为信息类型→维度 key 归属表（audit 读取）。由 agent 维护，sop.py 只读。本文件由 `sop.py migrate`"
    " 从旧版常量生成（attribution 为默认表），请人工核对 codeRoot、锚点噪音与归属后再 refresh --mark-fresh。"
)


def _eval_v3_value(node):
    """安全求值 v3 常量节点：标准字面量 + list(range(...)) 等展开（旧 TASK_TYPE_DIMS 的 onboarding 形态）。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "range":
            return list(range(*(ast.literal_eval(a) for a in node.args)))
        if node.func.id in ("list", "set", "tuple") and len(node.args) == 1:
            val = _eval_v3_value(node.args[0])
            return {"list": list, "set": set, "tuple": tuple}[node.func.id](val)
    if isinstance(node, ast.List):
        return [_eval_v3_value(el) for el in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_v3_value(el) for el in node.elts)
    if isinstance(node, ast.Dict):
        return {ast.literal_eval(k): _eval_v3_value(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Set):
        return {_eval_v3_value(el) for el in node.elts}
    return ast.literal_eval(node)


def extract_v3_constants(old_sop: Path) -> dict:
    """AST 解析 v3.x sop.py 的 DIMENSIONS/ANCHOR_RULES/TASK_TYPE_DIMS/RELATED_DIMS。
    返回 {常量名: 值}；未解析到置 None。兼容 v3.2/v3.3 形态；其他形态请用 --dims-json 兜底。"""
    tree = ast.parse(old_sop.read_text(encoding="utf-8"), filename=str(old_sop))
    names = ("DIMENSIONS", "ANCHOR_RULES", "TASK_TYPE_DIMS", "RELATED_DIMS")
    found = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in names):
            try:
                found[node.targets[0].id] = _eval_v3_value(node.value)
            except (ValueError, SyntaxError, TypeError):
                found[node.targets[0].id] = None
    return found


def _v3_anchor_to_glob(kind: str, pattern: str) -> str:
    """v3 锚点规则 → v4 glob：file（basename 精确）→ **/name；prefix（路径前缀）→ dir/**。"""
    if kind == "prefix":
        return pattern.rstrip("/") + "/**"
    return "**/" + pattern


def read_v3_index(ai_dir: Path):
    """尽力从旧 AGENTS.md 提取版本/完整刷新时间与索引表摘要。
    返回 (meta, summaries)：meta=dict；summaries={dim_id: {"file": str, "summary": str}}。"""
    meta = {}
    summaries = {}
    idx = ai_dir / "AGENTS.md"
    if not idx.is_file():
        return meta, summaries
    text = idx.read_text(encoding="utf-8")
    m = re.search(r"\*\*版本\*\*\s*\|\s*(\d+)", text)
    if m:
        meta["version"] = int(m.group(1))
    m = re.search(r"\*\*完整刷新\*\*\s*\|\s*([^|\n]+?)\s*\|", text)
    if m:
        meta["lastFullRefresh"] = m.group(1).strip()
    for line in text.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*[^|]*\|\s*`(context/[^`]+)`\s*\|\s*(.+?)\s*\|", line)
        if m:
            summaries[int(m.group(1))] = {"file": m.group(2), "summary": m.group(3).strip()}
    return meta, summaries


def build_manifest_from_v3(v3: dict, code_root: str, ai_dir: Path) -> dict:
    """旧常量 + 旧索引 → v4 manifest（含 _index）。"""
    dims_raw = v3.get("DIMENSIONS") or []
    anchor_rules = v3.get("ANCHOR_RULES") or []
    task_map = v3.get("TASK_TYPE_DIMS") or {}
    related = v3.get("RELATED_DIMS") or {}

    task_types = list(task_map.keys())
    if not task_types:
        task_types = ["feature", "bugfix", "refactor", "deps", "ci-cd", "docs", "perf", "onboarding"]

    per_dim_anchors = {num: [] for num, _k, _t in dims_raw}
    for kind, pattern, dim_ids in anchor_rules:
        glob_pat = _v3_anchor_to_glob(kind, pattern)
        for d in dim_ids:
            per_dim_anchors.setdefault(d, []).append(glob_pat)

    dimensions = []
    for num, key, title in dims_raw:
        must = [t for t in task_types if num in (task_map.get(t, ([], []))[0])]
        optional = [t for t in task_types if num in (task_map.get(t, ([], []))[1])]
        anchors = per_dim_anchors.get(num, [])
        if num == 1 and not anchors:
            anchors = ["@tree"]  # v3 语义：维度 1 用目录树哈希锚点
        dimensions.append({
            "id": num, "key": key, "title": title,
            "anchors": anchors,
            "tasks": {"must": must, "optional": optional},
            "related": [{"id": r, "note": n} for r, n in related.get(num, [])],
        })

    meta, _ = read_v3_index(ai_dir)
    return {
        "_comment": MIGRATE_COMMENT,
        "version": 2,
        "codeRoot": code_root,
        "taskTypes": task_types,
        # 默认归属表（v4.4+）：audit 依赖；项目增删维度后按需调整
        "attribution": json.loads(json.dumps(DEFAULT_MANIFEST["attribution"])),
        "dimensions": dimensions,
        "_index": {
            "version": meta.get("version", 1),
            "lastFullRefresh": meta.get("lastFullRefresh", "-"),
            "lastIncrementalRefresh": now_iso(),
        },
    }


def inject_summary(dim_file: Path, summary: str) -> bool:
    """维度文件头部补 `> **摘要**:` 行（变更锚点行后；无锚点行则插到首个 # 标题后）。已存在返回 False。"""
    text = dim_file.read_text(encoding="utf-8")
    if SUMMARY_RE.search(text):
        return False
    lines = text.splitlines(keepends=True)
    m = ANCHOR_LINE_RE.search(text)
    if m:
        nl = text.find("\n", m.end())
        inject_at = (nl + 1) if nl != -1 else len(text)
    else:
        inject_at = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                for j in range(i + 1, len(lines)):
                    if not lines[j].strip():
                        inject_at = sum(len(x) for x in lines[:j + 1])
                        break
                break
    new_text = text[:inject_at] + "> **摘要**: {}\n".format(summary) + text[inject_at:]
    dim_file.write_text(new_text, encoding="utf-8")
    return True


GEN_SECTION_TITLES = {"## 版本元数据", "## 维度文件索引",
                      "## 任务类型 → 维度映射", "## 任务类型 → 维度映射表"}


def extract_manual_from_v3_index(text: str) -> str:
    """从 v3 AGENTS.md 提取手工区内容：剔除生成区段（版本元数据/维度索引/任务映射）与头部。
    返回剩余文本（可为空）。"""
    lines = text.splitlines()
    n = len(lines)
    gen_ranges = []
    for i, line in enumerate(lines):
        if line.startswith("## ") and line.strip() in GEN_SECTION_TITLES:
            end = n
            for j in range(i + 1, n):
                if lines[j].startswith("## "):
                    end = j
                    break
            gen_ranges.append((i, end))
    first_h = next((i for i, l in enumerate(lines) if l.startswith("## ")), None)
    head_end = first_h if first_h is not None else 0
    keep = [l for i, l in enumerate(lines)
            if i >= head_end and not any(s <= i < e for s, e in gen_ranges)]
    return "\n".join(keep).strip()


def rebuild_agents_index(root: Path, project_name: str, manual_text: str) -> str:
    """新 AGENTS.md：v4 头部 + 生成区 marker 占位（refresh --mark-fresh 填充）+ 保留的手工区。"""
    return (
        "# Project Context: {}\n\n"
        "> project-task-sop v4.x | manifest 驱动（ai/{}）| 生成于 {}\n"
        "> 维护命令：`python sop.py init` / `python sop.py refresh [--mark-fresh]`\n\n"
        "<!-- SOP-GEN:BEGIN -->\n<!-- SOP-GEN:END -->\n\n{}"
    ).format(project_name, MANIFEST_NAME, now_iso(), manual_text)


def cmd_migrate(args) -> int:
    root = Path(args.root).resolve()
    if manifest_path(root).exists() and not args.force:
        print("! ai/{} 已存在，跳过（--force 覆盖）".format(MANIFEST_NAME))
        return 1

    old_sop = Path(args.old_sop) if args.old_sop else (SKILL_ROOT / "sop.v33.bak.py")
    if args.dims_json:
        try:
            data = json.loads(Path(args.dims_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print("! --dims-json 读取失败：{}".format(e))
            return 1
        dims = data["dimensions"] if isinstance(data, dict) else data
        v3 = {
            "DIMENSIONS": [(d["id"], d["key"], d["title"]) for d in dims],
            "ANCHOR_RULES": [],
            "TASK_TYPE_DIMS": {},
            "RELATED_DIMS": {},
        }
    elif old_sop.is_file():
        v3 = extract_v3_constants(old_sop)
        if not v3.get("DIMENSIONS"):
            print("! 旧 sop.py 未解析到 DIMENSIONS（兼容 v3.2/v3.3 形态）：{}".format(old_sop))
            print("  请改用 --dims-json 提供维度数组（[{id,key,title,anchors,tasks,related}...]）。")
            return 1
    else:
        print("! 未找到旧版 sop.py（--old-sop <path>，或本 skill 目录的 sop.v33.bak.py）")
        return 1

    code_root = args.code_root or str(root)
    manifest = build_manifest_from_v3(v3, code_root, root / "ai")
    _, summaries = read_v3_index(root / "ai")

    if args.dry_run:
        print("# --dry-run：将写入 ai/{} 的 manifest（{} 维度 / {} 任务类型 / codeRoot={}）".format(
            MANIFEST_NAME, len(manifest["dimensions"]), len(manifest["taskTypes"]),
            manifest["codeRoot"]))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        if summaries:
            print("# 摘要注入计划：{} 个维度文件（{}）".format(
                len(summaries),
                ", ".join(sorted("{}={}".format(k, v["file"]) for k, v in summaries.items()))))
        idx_path = root / "ai" / "AGENTS.md"
        if idx_path.exists():
            manual = extract_manual_from_v3_index(idx_path.read_text(encoding="utf-8"))
            print("# AGENTS.md 分区改造：手工区保留 {} 个段（{}）".format(
                manual.count("## "), ", ".join(
                    l.strip() for l in manual.splitlines() if l.startswith("## "))))
        return 0

    (root / "ai").mkdir(parents=True, exist_ok=True)
    save_manifest(root, manifest)
    print("+ ai/{}（{} 维度 / {} 任务类型 / codeRoot={} / _index.version={}）".format(
        MANIFEST_NAME, len(manifest["dimensions"]), len(manifest["taskTypes"]),
        manifest["codeRoot"], manifest["_index"].get("version")))

    ctx_dir = root / "ai" / "context"
    manifest_ids = {d["id"] for d in manifest["dimensions"]}
    injected, unregistered = [], []
    if ctx_dir.is_dir():
        for num, info in summaries.items():
            f = root / "ai" / info["file"]  # info["file"] 形如 context/01-topology.md
            if not f.exists():
                continue
            if inject_summary(f, info["summary"]):
                injected.append(f.name)
            if num not in manifest_ids:
                unregistered.append(info["file"])
    if injected:
        print("+ 已补摘要行：{}".format(", ".join(sorted(injected))))
    else:
        print("- 无待补摘要行（均已有摘要，或旧索引未提取到）")
    if unregistered:
        print("! 以下维度文件在旧常量中不存在（迁移后未登记）：{}".format(", ".join(unregistered)))
        print("  如需保留：在 manifest.dimensions 追加对应条目（id/key/title/anchors/tasks/related）")

    # AGENTS.md 分区改造：v4 头部 + 生成区占位 + 保留手工区
    idx_path = root / "ai" / "AGENTS.md"
    project_name = root.name
    manual_text = ""
    if idx_path.exists():
        old_text = idx_path.read_text(encoding="utf-8")
        m = re.match(r"^# Project Context:\s*(.+)$", old_text, re.M)
        if m:
            project_name = m.group(1).strip()
        manual_text = extract_manual_from_v3_index(old_text)
    idx_path.write_text(rebuild_agents_index(root, project_name, manual_text), encoding="utf-8")
    print("+ ai/AGENTS.md（分区改造：生成区占位 + 手工区保留 {} 个段）".format(
        manual_text.count("## ") if manual_text else 0))

    print("\n迁移完成。后续步骤：")
    print("1. 人工核对 manifest：codeRoot、锚点噪音（logs/uni_modules 等）")
    print("2. python sop.py check --context --root {}".format(root))
    print("3. python sop.py refresh --mark-fresh --root {}（封存锚点 + 填充 AGENTS.md 生成区）".format(root))
    print("4. 检查手工区（身份速览/任务记录/归档）是否完整，以及未登记维度文件的去留")
    return 0


# ---------------------------------------------------------------------------
# 实验记录（v3 保留）
# ---------------------------------------------------------------------------


def task_dir(root: Path, task: str) -> Path:
    return root / "ai" / task


def resolve_task(root: Path, task: str):
    t = task_dir(root, task)
    if not t.is_dir():
        print("! 任务目录不存在: {}".format(t))
        return None
    return t


def load_records(task: Path) -> list:
    p = task / ".experiments.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def cmd_dedup(args) -> int:
    task = resolve_task(Path(args.root), args.task)
    if task is None:
        return 1
    full = compute_experiment_hash(args.method, parse_params(args.params))
    hits = [r for r in load_records(task) if r.get("hash") == full]
    if hits:
        for r in hits:
            print("! 重复: #{} {} [{}] — {}".format(
                r.get("index"), r.get("method"), r.get("status"), r.get("resultSummary", "")))
    else:
        print("无重复（{}）".format(short_hash(full)))
    return 0


def cmd_record(args) -> int:
    task = task_dir(Path(args.root), args.task)
    task.mkdir(parents=True, exist_ok=True)
    key_params = parse_params(args.params)
    full = compute_experiment_hash(args.method, key_params)
    records = load_records(task)
    index = max((r.get("index", 0) for r in records), default=0) + 1
    rec = {
        "index": index,
        "method": args.method,
        "hash": full,
        "shortHash": short_hash(full),
        "keyParams": key_params,
        "paramSummary": param_summary(key_params),
        "resultSummary": args.result or "",
        "durationMinutes": args.duration or 0,
        "pitfalls": [p.strip() for p in (args.pitfalls or "").split(";") if p.strip()],
        "status": args.status or "unknown",
        "timestamp": now_iso(),
        "notesRef": args.notes_ref or "",
        "planStep": args.plan_step or 0,
        "author": args.author or "ai-agent",
    }
    with (task / ".experiments.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("+ 已记录 #{}: {} [{}]".format(index, rec["method"], rec["status"]))
    print("  hash: {}".format(short_hash(full)))
    return 0


def cmd_render(args) -> int:
    task = resolve_task(Path(args.root), args.task)
    if task is None:
        return 1
    records = load_records(task)
    if not records:
        print("（无实验记录）")
        return 0
    lines = ["# Experiments Report: {}".format(task.name), "",
             "> 由 sop.py render 生成 — 数据源 .experiments.jsonl（单一事实源）", "",
             "## 索引表（最新在前）", "",
             "| # | 方法 | 哈希 | 参数 | 结果 | 耗时(min) | 状态 | 时间 | 关联 |",
             "|---|------|------|------|------|-----------|------|------|------|"]
    for r in sorted(records, key=lambda x: x.get("index", 0), reverse=True):
        lines.append("| {} | {} | `{}` | {} | {} | {} | {} | {} | {} |".format(
            r.get("index"), r.get("method"), r.get("shortHash", ""),
            r.get("paramSummary", ""), r.get("resultSummary", ""),
            r.get("durationMinutes", 0), r.get("status"), r.get("timestamp", ""),
            r.get("notesRef", "")))
    lines += ["", "## 详细记录", ""]
    for r in sorted(records, key=lambda x: x.get("index", 0)):
        lines += ["### #{} {}".format(r.get("index"), r.get("method")), "",
                  "- **方法**: {}".format(r.get("method")),
                  "- **关键参数**: `{}`".format(json.dumps(r.get("keyParams", {}), ensure_ascii=False, sort_keys=True)),
                  "- **结果**: {}".format(r.get("resultSummary", "")),
                  "- **耗时**: {} min".format(r.get("durationMinutes", 0)),
                  "- **坑点**: {}".format("; ".join(r.get("pitfalls", [])) or "-"),
                  "- **状态**: {}".format(r.get("status")),
                  "- **时间**: {}".format(r.get("timestamp")),
                  "- **关联**: {} | plan step {}".format(r.get("notesRef", "-"), r.get("planStep", 0)),
                  "- **哈希**: `{}`".format(r.get("hash", "")), ""]
    report = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print("+ 已写入 {}".format(args.out))
    else:
        print(report)
    return 0


LESSON_HEAD_RE = re.compile(r"^## 教训:\s*(.+)$")


def cmd_lessons(args) -> int:
    task = resolve_task(Path(args.root), args.task)
    if task is None:
        return 1
    notes = task / "NOTES.md"
    if not notes.is_file():
        print("（无 NOTES.md，暂无教训记录）")
        return 0
    lines = notes.read_text(encoding="utf-8").splitlines()
    sections = []
    buf = None
    for line in lines:
        if LESSON_HEAD_RE.match(line):
            if buf is not None:
                sections.append("\n".join(buf))
            buf = [line]
        elif buf is not None:
            if line.startswith("## "):
                sections.append("\n".join(buf))
                buf = None
            else:
                buf.append(line)
    if buf is not None:
        sections.append("\n".join(buf))
    kw = (args.keyword or "").lower()
    hits = [s for s in sections if not kw or kw in s.lower()]
    if not hits:
        print("无匹配教训" + ("（关键词：{}）".format(args.keyword) if args.keyword else ""))
        return 0
    for s in hits:
        print(s)
        print("---")
    return 0


def cmd_tokens(args) -> int:
    total = 0
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print("! 不存在: {}".format(f))
            continue
        n = estimate_tokens(p.read_text(encoding="utf-8"))
        total += n
        print("{}: ~{} tokens".format(f, n))
    if len(args.files) > 1:
        print("合计: ~{} tokens".format(total))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sop.py", description="project-task-sop CLI v4.0（manifest 驱动）")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("init", help="初始化 ai/ 结构 + manifest + 维度文件")
    pi.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    pi.add_argument("--project", default=None, help="项目名（默认取目录名）")
    pi.add_argument("--force", action="store_true", help="重建 AGENTS.md 生成区与缺失维度文件")

    pr = sub.add_parser("refresh", help="按 manifest 锚点检测过期维度")
    pr.add_argument("--root", default=".")
    pr.add_argument("--mark-fresh", action="store_true",
                    help="封存锚点 + 递增版本 + 重写 AGENTS.md 生成区")

    pd = sub.add_parser("dedup", help="实验查重（精确哈希）")
    pd.add_argument("task")
    pd.add_argument("-m", "--method", required=True)
    pd.add_argument("-p", "--params", action="append", default=[],
                    help="关键参数：JSON 对象（-p '{\"r\":16}'）或 k=v（-p r=16 -p lr=1e-4，可重复）")
    pd.add_argument("--root", default=".")

    prec = sub.add_parser("record", help="追加实验记录")
    prec.add_argument("task")
    prec.add_argument("-m", "--method", required=True)
    prec.add_argument("-p", "--params", action="append", default=[])
    prec.add_argument("-r", "--result", default="")
    prec.add_argument("--status", default="unknown",
                      choices=["success", "partial", "failed", "running", "unknown"])
    prec.add_argument("--duration", type=int, default=0)
    prec.add_argument("--pitfalls", default="")
    prec.add_argument("--plan-step", type=int, default=0)
    prec.add_argument("--notes-ref", default="")
    prec.add_argument("--author", default="ai-agent")
    prec.add_argument("--root", default=".")

    pren = sub.add_parser("render", help="从 JSONL 渲染人读 Markdown 报告")
    pren.add_argument("task")
    pren.add_argument("--out", default=None)
    pren.add_argument("--root", default=".")

    pc = sub.add_parser("check", help="上下文校验（--context）或 JSONL 校验（<task>）")
    pc.add_argument("task", nargs="?", default=None)
    pc.add_argument("--context", action="store_true", help="校验 manifest 结构/一致性/锚点命中")
    pc.add_argument("--root", default=".")

    pl = sub.add_parser("lessons", help="检索 NOTES.md 中的教训条目")
    pl.add_argument("task")
    pl.add_argument("keyword", nargs="?", default="")
    pl.add_argument("--root", default=".")

    pt = sub.add_parser("tokens", help="估算文件 token 量")
    pt.add_argument("files", nargs="+")

    pdim = sub.add_parser("dims", help="从 manifest 查询任务类型必读/可选维度")
    pdim.add_argument("task_type")
    pdim.add_argument("--root", default=".")

    pau = sub.add_parser("audit", help="维度归属审计：扫环境事实误入/跨维度重复/交叉引用失效")
    pau.add_argument("--root", default=".")
    pau.add_argument("--check", action="store_true", help="发现问题时退出码非 0")

    pmig = sub.add_parser("migrate", help="v3 → v4 半自动迁移：解析旧 sop.py 常量生成 manifest + 补维度摘要行")
    pmig.add_argument("--root", default=".")
    pmig.add_argument("--old-sop", default=None,
                      help="旧版 sop.py 路径（缺省尝试本 skill 目录的 sop.v33.bak.py）")
    pmig.add_argument("--code-root", default=None,
                      help="代码根目录，锚点相对它解析（缺省用 root；ai 与代码目录分离时必填）")
    pmig.add_argument("--dims-json", default=None,
                      help="兜底：维度 JSON 文件（数组或 {\"dimensions\": [...]}，元素含 id/key/title/anchors/tasks/related）")
    pmig.add_argument("--dry-run", action="store_true",
                      help="只打印将生成的 manifest 与摘要计划，不落盘")
    pmig.add_argument("--force", action="store_true", help="已存在 manifest 时覆盖")

    return p


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 0
    handlers = {
        "init": cmd_init, "refresh": cmd_refresh, "dedup": cmd_dedup,
        "record": cmd_record, "render": cmd_render, "check": cmd_check,
        "lessons": cmd_lessons, "tokens": cmd_tokens, "dims": cmd_dims,
        "audit": cmd_audit, "migrate": cmd_migrate,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
