#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
project-task-sop CLI v3.3 — 轻量任务执行规程的机械部分。

职责边界：
- 本脚本只做"机械"工作：脚手架、锚点哈希、实验查重、记录、渲染、校验、token 估算。
- "语义"分析（架构判断、技术栈解读等）由 AI agent 完成；脚本输出清单引导 agent 填充维度文件。

命令：
  init               初始化 ai/ 结构与 9 个维度文件（含变更锚点；#9 为项目级教训维度）
  refresh            按锚点哈希检测过期维度 [--mark-fresh 封存]
  dedup <task>       实验查重（精确哈希）
  record <task>      追加实验记录到 .experiments.jsonl
  render <task>      从 JSONL 渲染人读 Markdown 报告（按需生成，不落盘存储）
  check <task>       校验 JSONL 完整性（哈希 / 序号）
  lessons <task>     检索 NOTES.md 中的教训条目（按关键词过滤）
  tokens <file...>   token 量估算（中文 ~1.5 字/token，英文 ~4 字/token）
  dims <task_type>   查询任务类型的必读/可选维度（免读索引）

与 v2 的关键差异：
- 变更检测基于"文件哈希锚点"（工作区感知，不依赖 git diff HEAD~1），修复 v2 检测不到未提交改动的问题。
- 删除死模式（.git/ 前缀、endswith("docs/") 等）。
- 实验记录收敛为单一 .experiments.jsonl 事实源，人读视图由 render 按需生成。
- v3.3 新增维度 9（project-lessons）：项目级教训沉淀，跨任务复用，会话启动即注入。

兼容：Python 3.8+，标准库，跨平台（Windows/macOS/Linux）。
"""
from __future__ import annotations

import argparse
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
# 常量定义
# ---------------------------------------------------------------------------
DIMENSIONS = [
    (1, "topology", "目录拓扑与关键文件"),
    (2, "tech-stack", "技术栈识别"),
    (3, "git-history", "Git 历史与协作模式"),
    (4, "docs-assets", "文档与知识资产"),
    (5, "quality-system", "测试与质量体系"),
    (6, "dependencies", "依赖与供应链"),
    (7, "architecture", "架构模式与模块划分"),
    (8, "dev-workflow", "开发工作流与环境"),
    (9, "project-lessons", "项目级教训沉淀"),
]
DIM_FILE = {num: "{:02d}-{}.md".format(num, key) for num, key, _ in DIMENSIONS}

# 变更锚点规则: (kind, pattern, [dims])
#   kind="file"  : 精确匹配 basename
#   kind="prefix": 路径前缀匹配（目录）
ANCHOR_RULES = [
    ("file", "package.json", [2, 6, 8]),
    ("file", "pnpm-lock.yaml", [2, 6]),
    ("file", "yarn.lock", [2, 6]),
    ("file", "Cargo.toml", [2, 6]),
    ("file", "Cargo.lock", [2, 6]),
    ("file", "pyproject.toml", [2, 6]),
    ("file", "poetry.lock", [2, 6]),
    ("file", "go.mod", [2, 6]),
    ("file", "go.sum", [2, 6]),
    ("file", "pom.xml", [2, 6]),
    ("file", "build.gradle", [2, 6]),
    ("file", "build.gradle.kts", [2, 6]),
    ("file", "composer.json", [2, 6]),
    ("file", "composer.lock", [2, 6]),
    ("file", ".gitlab-ci.yml", [5]),
    ("prefix", ".github/workflows/", [5]),
    ("prefix", ".circleci/", [5]),
    ("file", "pnpm-workspace.yaml", [7]),
    ("file", "go.work", [7]),
    ("file", "Makefile", [8]),
    ("file", "justfile", [8]),
    ("file", "Taskfile.yml", [8]),
    ("file", "README.md", [4]),
    ("file", "CHANGELOG.md", [4]),
    ("prefix", "docs/", [4]),
    ("prefix", "adr/", [4]),
    ("prefix", "architecture/", [4]),
]

TASK_TYPE_DIMS = {
    "feature": ([1, 2, 7, 9], [5, 8]),
    "bugfix": ([1, 2, 3, 7, 9], [5]),
    "refactor": ([1, 2, 3, 7, 9], [5, 6]),
    "deps": ([2, 6, 5, 9], [1]),
    "ci-cd": ([5, 8, 2, 9], [1, 6]),
    "docs": ([4, 1, 9], [2]),
    "perf": ([2, 7, 6, 5, 9], [1, 8]),
    "onboarding": (list(range(1, 10)), []),
}

RELATED_DIMS = {
    1: [(2, "依赖"), (7, "结构")],
    2: [(6, "依赖"), (7, "架构"), (8, "工作流")],
    3: [(7, "模块演化")],
    4: [(1, "文件分布")],
    5: [(8, "CI 流程"), (2, "测试框架")],
    6: [(2, "技术栈"), (5, "质量")],
    7: [(1, "拓扑"), (2, "技术栈")],
    8: [(5, "质量"), (2, "脚本语言")],
    9: [(2, "技术栈"), (8, "工作流")],
}

# ai/ 为 skill 自身产物，不参与目录拓扑锚点
IGNORED_DIRS = {"node_modules", ".git", "dist", "build", "target", ".venv",
                "__pycache__", ".dsh", ".idea", ".vscode", "ai"}

ANCHOR_LINE_RE = re.compile(r"^>\s*\*\*变更锚点\*\*:\s*(\{.*\})", re.M)

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
    """解析参数：单个 JSON 对象，或逗号分隔/重复 -p 的 k=v 列表（数值自动转 int/float）。

    兼容不同 shell 的引号行为：PowerShell 5.1 会剥离参数内嵌双引号，
    因此除 JSON 外同时支持 -p r=16 -p lr=1e-4 的键值形式。
    """
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


def related_dims_md(num: int) -> str:
    rows = ["| 维度 | 关系 | 说明 |", "|------|------|------|"]
    for other, reason in RELATED_DIMS.get(num, []):
        rows.append("| {} | 相关 | {} |".format(other, reason))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# 锚点：变更检测（工作区感知，基于文件哈希，不依赖 git diff）
# ---------------------------------------------------------------------------


def prefix_hits(root: Path, pattern: str) -> list:
    parts = [p for p in pattern.rstrip("/").split("/") if p]
    node = root
    for part in parts:
        node = node / part
        if not node.exists():
            return []
    if node.is_dir():
        return [f for f in node.rglob("*") if f.is_file()]
    return [node] if node.is_file() else []


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


def anchor_sources(root: Path, dim_num: int) -> dict:
    """当前锚点: {relpath: "sha256:..."}；维度 1 使用 tree 锚点。"""
    anchors = {}
    if dim_num == 1:
        anchors["tree"] = "sha256:" + tree_anchor(root)
        return anchors
    for kind, pattern, dims in ANCHOR_RULES:
        if dim_num not in dims:
            continue
        if kind == "file":
            p = root / pattern
            if p.is_file():
                anchors[pattern] = "sha256:" + file_sha256(p)
        else:
            for h in prefix_hits(root, pattern)[:20]:
                anchors[h.relative_to(root).as_posix()] = "sha256:" + file_sha256(h)
    return anchors


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


def bump_index(idx_path: Path) -> None:
    """AGENTS.md 版本递增 + 更新最近增量刷新时间 + 刷新上下文 token 估算。"""
    if not idx_path.is_file():
        return
    text = idx_path.read_text(encoding="utf-8")
    m = re.search(r"\| \*\*版本\*\* \| (\d+) \|", text)
    if m:
        text = text.replace(m.group(0), "| **版本** | {} |".format(int(m.group(1)) + 1), 1)
    text = re.sub(r"\| \*\*最近增量刷新\*\* \| [^|]* \|",
                  "| **最近增量刷新** | {} |".format(now_iso()), text, count=1)
    idx_path.write_text(text, encoding="utf-8")
    update_context_tokens(idx_path)


def context_tokens(ai_dir: Path) -> int:
    """AGENTS.md + 全部维度文件的 token 估算合计（#8 token 账本）。"""
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


def update_context_tokens(idx_path: Path) -> None:
    """更新 AGENTS.md 版本元数据中的『上下文估算』字段。"""
    if not idx_path.is_file():
        return
    text = idx_path.read_text(encoding="utf-8")
    if re.search(r"\| \*\*上下文估算\*\* \|", text):
        total = context_tokens(idx_path.parent)
        text = re.sub(r"\| \*\*上下文估算\*\* \| [^|]* \|",
                      "| **上下文估算** | ~{} tokens |".format(total), text, count=1)
        idx_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 实验记录（单一事实源 .experiments.jsonl）
# ---------------------------------------------------------------------------


def task_dir(root: Path, task: str, create: bool = True) -> Path:
    candidates = [Path(task), Path(root) / task, Path(root) / "ai" / task]
    for c in candidates:
        if c.is_dir():
            return c
    d = Path(root) / "ai" / task
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_task(root: Path, task: str) -> Path:
    """只读命令用：定位任务目录但不创建。不存在时打印提示并返回 None。"""
    d = task_dir(root, task, create=False)
    if not d.is_dir():
        print("! 任务目录不存在: {}（请先创建或检查任务名）".format(d))
        return None
    return d


def load_records(task: Path) -> list:
    p = task / ".experiments.jsonl"
    records = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print("! JSONL 解析失败: {}".format(e))
    return records


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    ctx_dir = root / "ai" / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    project_name = args.project or root.name
    now = now_iso()

    idx_path = root / "ai" / "AGENTS.md"
    if idx_path.exists() and not args.force:
        print("! ai/AGENTS.md 已存在，跳过（--force 覆盖）")
    else:
        tmpl = read_template("AGENTS.index.md.template", root)
        if tmpl is None:
            tmpl = BUILTIN_INDEX_TEMPLATE
        values = {
            "projectName": project_name, "timestamp": now,
            "contextVersion": 1, "lastFullRefresh": now, "lastIncrementalRefresh": "-",
            "name": project_name, "primaryLanguage": "待分析", "packageManager": "待分析",
            "archStyle": "待分析", "branchingModel": "待分析", "taskRunner": "待分析",
        }
        for num, _key, _title in DIMENSIONS:
            values["dim{}Summary".format(num)] = "待分析"
        values["contextTokens"] = 0
        idx_path.write_text(render_template(tmpl, values), encoding="utf-8")
        print("  + ai/AGENTS.md")

    for num, key, title in DIMENSIONS:
        dim_path = ctx_dir / DIM_FILE[num]
        if dim_path.exists() and not args.force:
            print("! {} 已存在，跳过".format(dim_path.name))
            continue
        tmpl = read_template("AGENTS.dimension.md.template", root)
        if tmpl is None:
            tmpl = BUILTIN_DIM_TEMPLATE
        anchors = anchor_sources(root, num)
        values = {
            "dimensionNumber": num, "dimensionTitle": title, "timestamp": now,
            "confidence": "pending",
            "anchorsJson": json.dumps(anchors, sort_keys=True, ensure_ascii=False),
            "dimensionContent": "", "relatedDimensions": related_dims_md(num),
        }
        dim_path.write_text(render_template(tmpl, values), encoding="utf-8")
        print("  + ai/context/{}".format(dim_path.name))

    update_context_tokens(idx_path)

    print("\n✅ init 完成。下一步（agent）:")
    print("  1. 按 ai/AGENTS.md 的维度映射表读取源文件，填充各维度文件『维度内容』段")
    print("  2. 回填 ai/AGENTS.md 的维度摘要与项目身份字段")
    print("  3. 分析完成后运行: python sop.py refresh --mark-fresh 封存锚点")
    return 0


def cmd_refresh(args) -> int:
    root = Path(args.root).resolve()
    ctx_dir = root / "ai" / "context"
    if not ctx_dir.is_dir():
        print("! 未找到 ai/context，请先运行 init")
        return 1

    stale = []
    for num, _key, title in DIMENSIONS:
        dim_path = ctx_dir / DIM_FILE[num]
        if not dim_path.exists():
            print("- dim {} {}: 缺失（请 init 或手动创建）".format(num, title))
            stale.append(num)
            continue
        stored = parse_anchors(dim_path)
        current = anchor_sources(root, num)
        diffs = diff_anchors(stored, current)
        if diffs:
            print("- dim {} {}: 过期 ({} 处锚点变化)".format(num, title, len(diffs)))
            for d in diffs[:10]:
                print("    {}".format(d))
            stale.append(num)
        else:
            print("+ dim {} {}: 最新".format(num, title))

    if args.mark_fresh:
        for num, _key, _title in DIMENSIONS:
            dim_path = ctx_dir / DIM_FILE[num]
            if dim_path.exists():
                write_anchors(dim_path, anchor_sources(root, num))
        bump_index(root / "ai" / "AGENTS.md")
        print("\n✅ 锚点已封存，AGENTS.md 版本已递增")
    elif stale:
        print("\n提示: 重分析过期维度后运行 `python sop.py refresh --mark-fresh` 封存")

    if not stale:
        print("\n✅ 所有维度最新")
    return 0


def cmd_dedup(args) -> int:
    task = resolve_task(Path(args.root), args.task)
    if task is None:
        return 1
    method = args.method
    key_params = parse_params(args.params)
    full = compute_experiment_hash(method, key_params)
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
    method = args.method
    key_params = parse_params(args.params)
    full = compute_experiment_hash(method, key_params)
    records = load_records(task)
    index = max((r.get("index", 0) for r in records), default=0) + 1
    rec = {
        "index": index,
        "method": method,
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
    p = task / ".experiments.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("+ 已记录 #{}: {} [{}]".format(index, method, rec["status"]))
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


def cmd_check(args) -> int:
    task = resolve_task(Path(args.root), args.task)
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


LESSON_HEAD_RE = re.compile(r"^## 教训:\s*(.+)$")


def cmd_lessons(args) -> int:
    """检索任务 NOTES.md 中的教训沉淀条目（## 教训: <签名>）。"""
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


def cmd_dims(args) -> int:
    """按任务类型查询必读/可选维度（免读 AGENTS.md 索引，省 token）。"""
    config = TASK_TYPE_DIMS.get(args.task_type)
    if config is None:
        print("未知任务类型: {}（可选：{}）".format(args.task_type, ", ".join(sorted(TASK_TYPE_DIMS))))
        return 1
    req, opt = config
    print("任务类型 {}:".format(args.task_type))
    print("  必读: {}".format(", ".join(str(d) for d in req)))
    print("  可选: {}".format(", ".join(str(d) for d in opt) or "-"))
    return 0


# ---------------------------------------------------------------------------
# 内置兜底模板（templates/ 缺失时使用）
# ---------------------------------------------------------------------------
BUILTIN_INDEX_TEMPLATE = """# Project Context: {{projectName}}

> project-task-sop v3.3 | 生成于 {{timestamp}} | 版本 {{contextVersion}}

## 维度文件索引

| # | 维度 | 文件 |
|---|------|------|
| 1 | 目录拓扑 | context/01-topology.md |
| 2 | 技术栈 | context/02-tech-stack.md |
| 3 | Git 历史 | context/03-git-history.md |
| 4 | 文档资产 | context/04-docs-assets.md |
| 5 | 质量体系 | context/05-quality-system.md |
| 6 | 依赖供应链 | context/06-dependencies.md |
| 7 | 架构模式 | context/07-architecture.md |
| 8 | 开发工作流 | context/08-dev-workflow.md |
| 9 | 项目级教训 | context/09-project-lessons.md |
"""

BUILTIN_DIM_TEMPLATE = """# Dimension {{dimensionNumber}}: {{dimensionTitle}}

> 生成于 {{timestamp}} | 置信度 {{confidence}}
> **变更锚点**: {{anchorsJson}}

## 维度内容

{{dimensionContent}}
"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sop.py", description="project-task-sop CLI v3.3")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("init", help="初始化 ai/ 结构 + 9 个维度文件（含锚点）")
    pi.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    pi.add_argument("--project", default=None, help="项目名（默认取目录名）")
    pi.add_argument("--force", action="store_true", help="覆盖已有文件")

    pr = sub.add_parser("refresh", help="按锚点哈希检测过期维度")
    pr.add_argument("--root", default=".")
    pr.add_argument("--mark-fresh", action="store_true",
                    help="重分析后封存锚点并递增 AGENTS.md 版本")

    pd = sub.add_parser("dedup", help="实验查重（精确哈希）")
    pd.add_argument("task", help="任务目录（如 feat-user-login）")
    pd.add_argument("-m", "--method", required=True)
    pd.add_argument("-p", "--params", action="append", default=[],
                    help="关键参数：JSON 对象（-p '{\"r\":16}'）或 k=v（-p r=16 -p lr=1e-4，可重复）")
    pd.add_argument("--root", default=".")

    prec = sub.add_parser("record", help="追加实验记录")
    prec.add_argument("task")
    prec.add_argument("-m", "--method", required=True)
    prec.add_argument("-p", "--params", action="append", default=[],
                      help="关键参数：JSON 对象或 k=v（-p r=16 -p lr=1e-4，可重复）")
    prec.add_argument("-r", "--result", default="")
    prec.add_argument("--status", default="unknown",
                      choices=["success", "partial", "failed", "running", "unknown"])
    prec.add_argument("--duration", type=int, default=0, help="耗时（分钟）")
    prec.add_argument("--pitfalls", default="", help="坑点，分号分隔")
    prec.add_argument("--plan-step", type=int, default=0)
    prec.add_argument("--notes-ref", default="", help="关联笔记引用，如 NOTES.md#L10")
    prec.add_argument("--author", default="ai-agent")
    prec.add_argument("--root", default=".")

    pren = sub.add_parser("render", help="从 JSONL 渲染人读 Markdown 报告")
    pren.add_argument("task")
    pren.add_argument("--out", default=None, help="输出文件（默认打印到 stdout）")
    pren.add_argument("--root", default=".")

    pc = sub.add_parser("check", help="校验 JSONL 完整性（哈希 / 序号）")
    pc.add_argument("task")
    pc.add_argument("--root", default=".")

    pl = sub.add_parser("lessons", help="检索 NOTES.md 中的教训条目")
    pl.add_argument("task")
    pl.add_argument("keyword", nargs="?", default="", help="按关键词过滤（可选）")
    pl.add_argument("--root", default=".")

    pt = sub.add_parser("tokens", help="估算文件 token 量")
    pt.add_argument("files", nargs="+")

    pdim = sub.add_parser("dims", help="按任务类型查询必读/可选维度")
    pdim.add_argument("task_type", choices=sorted(TASK_TYPE_DIMS))

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
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
