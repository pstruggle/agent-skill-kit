"""
project-task-sop CLI v4.12.2 — 轻量任务执行规程的机械部分（Manifest 驱动）。

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
  scan               init 后自动推导：按 codeRoot 实际文件填 1/2/4/5/6/8 事实初稿 + 修 0 命中锚点（--dry-run/--force/--only）
  refresh            按 manifest 锚点检测过期维度 [--mark-fresh 封存 + 重写 AGENTS.md 生成区]
  entry              生成/校验仓库入口文件（生成区=纪律索引+任务路由，手写区不动；--check 5 条 lint）
  tasks              任务台账：扫各任务 plan.md 字段表聚合 ai/TASKS.md [--check 校验 / --backfill 存量补录]
  next               可执行前沿：按 status + depends 算「现在能做什么、被谁挡着、哪几波」，不排优先级（--json）
  check --context    manifest 结构 + 一致性 + 锚点命中校验（check <task> 仍为 JSONL 校验）
  dedup <task>       实验查重（精确哈希）
  record <task>      追加实验记录到 .experiments.jsonl
  render <task>      从 JSONL 渲染人读 Markdown 报告（按需生成，不落盘存储）
  check <task>       校验 JSONL 完整性（哈希 / 序号）
  lessons <task>     检索任务 NOTES.md 中的教训沉淀条目
  tokens <file...>   token 量估算（中文 ~1.5 字/token，英文 ~4 字/token）
  dims <task_type>   从 manifest 聚合查询任务类型的必读/可选维度
  audit             维度归属审计：扫环境事实误入/跨维度重复/交叉引用失效（合法归属从 manifest attribution 读取；可疑点报告，判定按 §1.5 归属表）
  gates             五道门一次跑完（context / dims / audit / entry / tasks），任一失败退出码 1（--json / --skip-if-missing，供 pre-commit 与 hook 引用）
  migrate            v3 → v4 半自动迁移：解析旧 sop.py 常量生成 manifest + 补维度摘要行

兼容：Python 3.9+（用到 Path.is_relative_to），纯标准库，跨平台（Windows/macOS/Linux）。
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
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
    # 仓库入口文件（可选，通用能力）：file 为空 = 不启用。
    # 启用后 `sop.py entry` 在 codeRoot/file 下维护生成区（纪律索引 + 任务路由 + 维护契约），
    # 手写区（架构概览/易踩空点）不受影响；`entry --check` 校验行数/权威声明/凭据/时效状态。
    "entry": {"file": "", "maxLines": 45, "title": ""},
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


def write_text_lf(path: Path, text: str) -> None:
    """按 UTF-8 + LF 落盘（父目录不存在则创建）。

    不用 `Path.write_text` 的 newline 关键字：那是 Python 3.10+ 才有的签名，
    3.9 上会直接抛 TypeError（CI 的 windows-3.9 job 抓到过，台账/计划文件全部没写出来）。
    显式 open 才能跨版本一致，也保证跨平台落盘只有 LF。
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


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
    write_text_lf(manifest_path(root), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def dim_file_name(dim: dict) -> str:
    return "{:02d}-{}.md".format(int(dim["id"]), dim["key"])


def dim_file_path(root: Path, dim: dict) -> Path:
    return root / "ai" / "context" / dim_file_name(dim)


# ---------------------------------------------------------------------------
# 锚点：变更检测（manifest 驱动，glob 展开，不依赖 git diff）
# ---------------------------------------------------------------------------


def _walk_files(root: Path) -> list:
    """剪枝后的完整文件列表（相对 posix 串，已排序）；按代码根缓存，供全部维度锚点共用一次遍历。"""
    key = str(root.resolve())
    cached = _WALK_CACHE.get(key)
    if cached is not None:
        return cached
    root_s = str(root.resolve())
    files = []
    for dirpath, dirnames, filenames in os.walk(root_s):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root_s).replace(os.sep, "/")
            files.append(rel)
    files.sort()
    _WALK_CACHE[key] = files
    return files


def _match_glob(rel: str, pattern: str) -> bool:
    """用 fnmatch 语义匹配 `**` 风格 glob（`*` 跨目录）；`**/x` 同时匹配根级 x。"""
    pat = pattern.replace("\\", "/")
    candidates = [pat]
    if pat.startswith("**/"):
        candidates.append(pat[3:])
    return any(fnmatch.fnmatchcase(rel, p) for p in candidates)


def glob_hits(root: Path, pattern: str) -> list:
    """展开相对仓库根的 glob，过滤 IGNORED_DIRS，返回相对路径 posix 串列表（已排序）。"""
    return [rel for rel in _walk_files(root) if _match_glob(rel, pattern)]


DEFAULT_ANCHOR_LIMIT = 20  # 每维度锚点文件上限（维度可用 anchorLimit 覆盖，0 = 不设上限）
_ANCHOR_WARNED = set()
_WALK_CACHE = {}  # codeRoot -> 文件相对路径列表（单次剪枝遍历，跨维度复用）


def anchor_sources(code_root: Path, dim: dict) -> dict:
    """当前锚点: {relpath: \"sha256:...\"}，路径相对代码根 code_root。
    维度 anchors=[\"@tree\"] 用目录树哈希（扫代码根）；[] 返回空。"""
    anchors = dim.get("anchors") or []
    if anchors == ["@tree"]:
        return {"tree": "sha256:" + tree_anchor(code_root)}
    out = {}
    rels = []
    seen = set()
    for pat in anchors:
        for rel in glob_hits(code_root, pat):
            if rel in seen:
                continue
            seen.add(rel)
            rels.append(rel)
    try:
        limit = int(dim.get("anchorLimit", DEFAULT_ANCHOR_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_ANCHOR_LIMIT
    if limit > 0 and len(rels) > limit:
        total = len(rels)
        step = total / limit
        sampled = []
        for i in range(limit):
            idx = int(i * step)
            if idx >= total:
                idx = total - 1
            if rels[idx] not in sampled:
                sampled.append(rels[idx])
        key = (dim.get("id"), dim.get("key"), total, limit)
        if key not in _ANCHOR_WARNED:
            _ANCHOR_WARNED.add(key)
            print("! dim {} {}: 锚点命中 {} 个文件，超过上限 {}，已均匀抽样 {} 个；"
                  "未抽到的文件不参与过期检测（可在 manifest 该维度设置 anchorLimit）".format(
                      dim.get("id"), dim.get("title"), total, limit, len(sampled)))
        rels = sampled
    for rel in rels:
        out[rel] = "sha256:" + file_sha256(code_root / rel)
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
    write_text_lf(dim_file, text)


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
    write_text_lf(idx_path, head + GEN_BEGIN + "\n" + new_section + "\n" + GEN_END + tail)


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

> **维度元数据**：生成于 {{timestamp}} | 置信度 {{confidence}} | 技能 v4.8
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
        write_text_lf(dim_path, render_template(tmpl, values))
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
        write_text_lf(idx_path, render_template(tmpl, values))
        print("  + ai/AGENTS.md")
    print("\n下一步：python sop.py scan（按项目实际自动推导维度初稿）")
    return 0


# ---------------------------------------------------------------------------
# 命令：refresh
# ---------------------------------------------------------------------------


def collect_stale(root: Path, mf: dict) -> list:
    """返回过期/缺失维度列表 [(dim, diffs, missing)]。

    refresh 与 gates 共用这一处判定：两条路径各算一遍迟早会漂（一边看缺失、一边不看）。
    """
    code_root = Path(mf.get("codeRoot", root))
    stale = []
    for dim in mf["dimensions"]:
        dim_path = dim_file_path(root, dim)
        if not dim_path.exists():
            stale.append((dim, [], True))
            continue
        diffs = diff_anchors(parse_anchors(dim_path), anchor_sources(code_root, dim))
        if diffs:
            stale.append((dim, diffs, False))
    return stale


def cmd_refresh(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    code_root = Path(mf.get("codeRoot", root))
    ctx_dir = root / "ai" / "context"
    stale_dims = collect_stale(root, mf)
    stale_map = {str(d["id"]): (diffs, missing) for d, diffs, missing in stale_dims}
    stale = []
    for dim in mf["dimensions"]:
        diffs, missing = stale_map.get(str(dim["id"]), (None, False))
        if missing:
            print("- dim {} {}: 缺失（请 init 或手动创建）".format(dim["id"], dim["title"]))
            stale.append(dim)
            continue
        if diffs is None:
            print("+ dim {} {}: 最新".format(dim["id"], dim["title"]))
            continue
        print("- dim {} {}: 过期 ({} 处锚点变化)".format(dim["id"], dim["title"], len(diffs)))
        for d in diffs[:10]:
            print("    {}".format(d))
        stale.append(dim)

    if args.mark_fresh:
        only = (getattr(args, "only", None) or "").replace(",", " ").split()
        target_ids = None
        if only:
            target_ids = set()
            for dim in mf["dimensions"]:
                if str(dim.get("id")) in only or dim.get("key") in only:
                    target_ids.add(str(dim.get("id")))
            if not target_ids:
                print("! --only 未匹配任何维度: {}".format(" ".join(only)))
                return 1
        mf.setdefault("_index", {})
        mf["_index"]["version"] = int(mf["_index"].get("version", 1)) + 1
        mf["_index"]["lastIncrementalRefresh"] = now_iso()
        save_manifest(root, mf)
        sealed = []
        for dim in mf["dimensions"]:
            if target_ids is not None and str(dim.get("id")) not in target_ids:
                continue
            dim_path = dim_file_path(root, dim)
            if dim_path.exists():
                write_anchors(dim_path, anchor_sources(code_root, dim))
                sealed.append("{} {}".format(dim["id"], dim["key"]))
        gen = render_generated(mf, mf["dimensions"], mf["taskTypes"], root / "ai")
        rewrite_generated_section(root / "ai" / "AGENTS.md", gen)
        n_tasks = write_tasks(root, quiet=True)
        if n_tasks:
            print("✅ 任务台账已同步（{} 个任务）".format(n_tasks))
        if target_ids is None:
            print("\n✅ 全部维度锚点已封存，AGENTS.md 生成区已重写（版本 {}）".format(mf["_index"]["version"]))
        else:
            print("\n✅ 已封存 {} 个维度（{}），其余维度保持旧锚点；AGENTS.md 生成区已重写（版本 {}）".format(
                len(sealed), "、".join(sealed), mf["_index"]["version"]))
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

    # 仓库入口文件（可选）：启用时做 lint 告警，不阻塞主校验
    ecfg = entry_config(mf)
    if ecfg:
        epath = code_root / ecfg["file"]
        if not epath.is_file():
            print("! entry: 已配置但文件不存在 {}（运行 `python sop.py entry` 生成）".format(epath))
        else:
            etext = epath.read_text(encoding="utf-8")
            egen = ENTRY_GEN_BEGIN + "\n" + render_entry_gen(mf) + "\n" + ENTRY_GEN_END
            eissues = entry_lint(etext, ecfg)
            ecur = extract_block(etext, ENTRY_GEN_BEGIN, ENTRY_GEN_END)
            if not ecur:
                eissues.append("缺少 ENTRY-GEN 标记（未由 `python sop.py entry` 维护）")
            elif ecur.strip() != egen[len(ENTRY_GEN_BEGIN):-len(ENTRY_GEN_END)].strip():
                eissues.append("生成区与 manifest 不同步（重跑 `python sop.py entry`）")
            for it in eissues:
                print("! entry {}: {}".format(ecfg["file"], it))

    # 任务台账（可选）：存在 ai/*/plan.md 时做结构告警，不阻塞主校验
    tasks = collect_tasks(root / "ai")
    if tasks:
        for lv, msg in tasks_lint(tasks):
            print("{} tasks: {}".format("!" if lv == "err" else "~", msg))

    print("manifest: {} 维度 / {} 任务类型".format(len(ids), len(mf["taskTypes"])))
    print("✅ 上下文校验通过" if ok else "❌ 存在问题（见上）")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 命令：scan（init 后自动推导维度初稿 + 修复 0 命中锚点）
# ---------------------------------------------------------------------------

SCAN_KEYS = ("topology", "tech-stack", "docs-assets", "quality-system",
             "dependencies", "dev-workflow")
DIM_TEMPLATE_VERSION = "4.8"
AUTO_MARK = "自动推导"

EXT_LANGS = (
    (".py", "Python"), (".pyi", "Python"), (".js", "JavaScript"), (".mjs", "JavaScript"),
    (".cjs", "JavaScript"), (".jsx", "JavaScript"), (".ts", "TypeScript"),
    (".tsx", "TypeScript"), (".vue", "Vue"), (".svelte", "Svelte"), (".java", "Java"),
    (".kt", "Kotlin"), (".go", "Go"), (".rs", "Rust"), (".php", "PHP"), (".rb", "Ruby"),
    (".cs", "C#"), (".swift", "Swift"), (".dart", "Dart"), (".cpp", "C++"),
    (".cc", "C++"), (".c", "C"), (".scala", "Scala"), (".ex", "Elixir"),
    (".exs", "Elixir"), (".lua", "Lua"), (".sh", "Shell"), (".ps1", "PowerShell"),
)

BUILD_FILES = ("package.json", "pyproject.toml", "setup.py", "setup.cfg",
               "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
               "build.gradle", "build.gradle.kts", "composer.json", "Gemfile",
               "pubspec.yaml", "CMakeLists.txt", "mix.exs")

LOCK_FILES = ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb",
              "bun.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "requirements.txt",
              "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock")

DEV_FILES = ("Makefile", "makefile", "GNUmakefile", "justfile", "Taskfile.yml",
             "Taskfile.yaml", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
             "Dockerfile", ".nvmrc", ".python-version", ".tool-versions", ".editorconfig")

DOC_BASENAMES = ("README.md", "README.en.md", "README.rst", "README",
                 "CHANGELOG.md", "CHANGELOG", "CONTRIBUTING.md", "ARCHITECTURE.md",
                 "SECURITY.md", "AGENTS.md", "LICENSE")

TEST_DIR_RE = re.compile(r"^(tests?|__tests__|spec)/")

TEST_FILE_RES = (
    re.compile(r"^test_.*\.py$"), re.compile(r".*_test\.py$"),
    re.compile(r".*_test\.go$"), re.compile(r".*\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)$"),
    re.compile(r".*Test\.java$"), re.compile(r".*\.(spec|test)\.rb$"),
)

NODE_FRAME_MAP = {
    "react": "React", "react-dom": "React", "next": "Next.js", "vue": "Vue",
    "nuxt": "Nuxt", "vite": "Vite", "svelte": "Svelte", "express": "Express",
    "koa": "Koa", "fastify": "Fastify", "@angular/core": "Angular",
    "@nestjs/core": "NestJS", "uni-app": "uni-app", "@dcloudio/uni-app": "uni-app",
}
PY_FRAME_MAP = {
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy", "celery": "Celery", "torch": "PyTorch",
    "tensorflow": "TensorFlow", "pytest": "pytest", "ruff": "ruff", "mypy": "mypy",
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _read_json(path: Path) -> dict:
    text = _read_text(path)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _basename(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def _dedup(items) -> list:
    out = []
    for it in items:
        if it and it not in out:
            out.append(it)
    return out


def _git_facts(code_root: Path) -> dict:
    """只读 .git/HEAD 与 config（不调用 git 命令，避免引入 subprocess 依赖）。"""
    out = {"branch": "", "remote": ""}
    head = _read_text(code_root / ".git" / "HEAD").strip()
    if head.startswith("ref:"):
        out["branch"] = head.split("/")[-1].strip()
    cfg = _read_text(code_root / ".git" / "config")
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg, re.S)
    if m:
        out["remote"] = m.group(1).strip()
    return out


def detect_facts(code_root: Path) -> dict:
    """扫 codeRoot，只产出"事实"（文件、清单、命令），不做架构判断。"""
    files = _walk_files(code_root)
    facts = {
        "files_count": len(files), "languages": [], "manifests": [], "lockfiles": [],
        "test_dirs": [], "test_files": [], "ci": [], "docs": [], "dev_files": [],
        "top_dirs": [], "top_files": [], "node": {}, "python": {"deps": []},
        "frameworks": [], "test_framework": "", "package_manager": "",
        "runner": "", "scripts": {},
    }
    ext_counts = {}
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        for e, lang in EXT_LANGS:
            if ext == e:
                ext_counts[lang] = ext_counts.get(lang, 0) + 1
                break
    facts["languages"] = sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]

    names = [_basename(r) for r in files]
    facts["manifests"] = [r for r in files if _basename(r) in BUILD_FILES][:6]
    facts["lockfiles"] = [r for r in files if _basename(r) in LOCK_FILES][:6]
    facts["ci"] = [r for r in files
                   if r.startswith(".github/workflows/")
                   or r in (".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml",
                            ".circleci/config.yml")][:6]
    facts["docs"] = [r for r in files
                     if _basename(r) in DOC_BASENAMES
                     or r.startswith(("docs/", "adr/", "architecture/"))][:8]
    facts["dev_files"] = [r for r in files if _basename(r) in DEV_FILES][:8]

    for rel in files:
        m = TEST_DIR_RE.match(rel)
        if m:
            facts["test_dirs"].append(m.group(1) + "/")
        if any(rx.match(_basename(rel)) for rx in TEST_FILE_RES):
            facts["test_files"].append(rel)
    facts["test_dirs"] = _dedup(facts["test_dirs"])
    facts["test_files"] = facts["test_files"][:60]

    for rel in files:
        if "/" not in rel:
            facts["top_files"].append(rel)
        else:
            d = rel.split("/", 1)[0]
            cur = next((x for x in facts["top_dirs"] if x[0] == d), None)
            if cur is None:
                facts["top_dirs"].append([d, 1])
            else:
                cur[1] += 1
    facts["top_dirs"] = [(d, n) for d, n in sorted(facts["top_dirs"], key=lambda x: (-x[1], x[0]))][:10]

    pkg = _read_json(code_root / "package.json")
    if pkg:
        deps = {}
        for k in ("dependencies", "devDependencies"):
            if isinstance(pkg.get(k), dict):
                deps.update(pkg[k])
        facts["node"] = {
            "name": str(pkg.get("name") or ""),
            "scripts": pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {},
            "deps": pkg.get("dependencies") if isinstance(pkg.get("dependencies"), dict) else {},
            "dev_deps": pkg.get("devDependencies") if isinstance(pkg.get("devDependencies"), dict) else {},
            "all_deps": deps,
        }
        facts["scripts"] = dict(facts["node"]["scripts"])
        facts["frameworks"] += [v for k, v in NODE_FRAME_MAP.items() if k in deps]

    py_text = "\n".join(_read_text(code_root / n) for n in
                        ("pyproject.toml", "setup.cfg", "requirements.txt", "setup.py"))
    py_lower = py_text.lower()
    py_deps = []
    m = re.search(r"dependencies\s*=\s*\[(.*?)\]", py_text, re.S)
    if m:
        py_deps = re.findall(r"[\"']([A-Za-z0-9_.\-]+)", m.group(1))
    for line in _read_text(code_root / "requirements.txt").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            py_deps.append(re.split(r"[<>=!~\[]", line)[0].strip())
    facts["python"]["deps"] = _dedup([d for d in py_deps if d])[:20]
    facts["frameworks"] += [v for k, v in PY_FRAME_MAP.items() if k in py_lower]
    facts["frameworks"] = _dedup(facts["frameworks"])

    pom = _read_text(code_root / "pom.xml").lower()
    if "spring-boot" in pom:
        facts["frameworks"].append("Spring Boot")

    dev_deps = facts["node"].get("dev_deps", {})
    if "jest" in dev_deps:
        facts["test_framework"] = "Jest"
    elif "vitest" in dev_deps:
        facts["test_framework"] = "Vitest"
    elif "mocha" in dev_deps:
        facts["test_framework"] = "Mocha"
    elif "pytest" in py_lower:
        facts["test_framework"] = "pytest"
    elif "junit" in pom:
        facts["test_framework"] = "JUnit"
    elif "go.mod" in [_basename(x) for x in facts["manifests"]]:
        facts["test_framework"] = "go test"

    locks = [_basename(x) for x in facts["lockfiles"]]
    pm_map = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
              ("bun.lock", "bun"), ("package-lock.json", "npm"), ("poetry.lock", "poetry"),
              ("uv.lock", "uv"), ("Pipfile.lock", "pipenv"), ("requirements.txt", "pip"),
              ("composer.lock", "composer"), ("Gemfile.lock", "bundler"))
    for name, pm in pm_map:
        if name in locks:
            facts["package_manager"] = pm
            break

    dev_names = [_basename(x) for x in facts["dev_files"]]
    if "Makefile" in dev_names or "makefile" in dev_names or "GNUmakefile" in dev_names:
        facts["runner"] = "make"
    elif "justfile" in dev_names:
        facts["runner"] = "just"
    elif "Taskfile.yml" in dev_names or "Taskfile.yaml" in dev_names:
        facts["runner"] = "task"
    elif facts["scripts"]:
        facts["runner"] = "{} scripts".format(facts["package_manager"] or "package.json")

    mk = _read_text(code_root / "Makefile")
    mk_targets = re.findall(r"(?m)^([A-Za-z0-9_.\-]+)\s*:(?!=)", mk)
    facts["make_targets"] = [t for t in _dedup(mk_targets) if not t.startswith(".")][:10]

    facts.update(_git_facts(code_root))
    return facts


def _lang_label(facts: dict) -> str:
    if not facts["languages"]:
        return "未识别主语言"
    return "、".join("{}（{} 个文件）".format(k, v) for k, v in facts["languages"][:2])


def _anchor_candidates(key: str, facts: dict) -> list:
    if key == "tech-stack":
        return facts["manifests"][:3]
    if key == "docs-assets":
        out = [r for r in facts["docs"] if "/" not in r][:4]
        if any(r.startswith("docs/") for r in facts["docs"]):
            out.append("docs/**")
        return _dedup(out)
    if key == "quality-system":
        out = [d + "**" for d in facts["test_dirs"][:3]]
        return _dedup(out + facts["ci"][:3])
    if key == "dependencies":
        return _dedup(facts["lockfiles"][:4] + facts["manifests"][:3])
    if key == "architecture":
        mono = [r for r in facts["top_files"]
                if r in ("pnpm-workspace.yaml", "lerna.json", "go.work",
                         "settings.gradle", "settings.gradle.kts")]
        return _dedup(mono + facts["manifests"][:2])
    if key == "dev-workflow":
        return _dedup(facts["dev_files"][:4] + facts["ci"][:3])
    return []


def _dim_content(key: str, facts: dict) -> tuple:
    """返回 (内容 markdown, 一行摘要)；只写事实，不下结论。"""
    lang = _lang_label(facts)
    if key == "topology":
        rows = ["| `{}` | {} | 顶层目录 |".format(d, n) for d, n in facts["top_dirs"]]
        files = ["- `{}`".format(r) for r in facts["top_files"][:12]]
        content = "\n".join([
            "### 目录概览（自动推导）", "",
            "| 目录 | 文件数 | 说明 |", "|------|------|------|",
            "\n".join(rows) if rows else "| - | 0 | 未检测到子目录 |", "",
            "### 顶层关键文件", "",
            "\n".join(files) if files else "- 未检测到顶层文件",
        ])
        summary = "{}；顶层 {} 个目录 / 共 {} 个文件".format(
            lang, len(facts["top_dirs"]), facts["files_count"])
        return content, summary

    if key == "tech-stack":
        rows = [
            ("主语言", lang),
            ("构建/清单", "、".join("`{}`".format(r) for r in facts["manifests"][:3]) or "未检测到"),
            ("包管理器", facts["package_manager"] or "未检测到锁文件"),
            ("框架/库", "、".join(facts["frameworks"][:6]) or "未识别"),
            ("测试框架", facts["test_framework"] or "未识别"),
        ]
        content = "\n".join([
            "### 技术栈快照（自动推导）", "",
            "| 项 | 值 |", "|------|------|",
            "\n".join("| {} | {} |".format(k, v.replace("|", "/")) for k, v in rows),
        ])
        summary = "{}；{}；{}".format(
            lang, facts["package_manager"] or "无锁文件", "、".join(facts["frameworks"][:3]) or "未识别框架")
        return content, summary

    if key == "docs-assets":
        rows = ["| `{}` | 文档/说明 |".format(r) for r in facts["docs"]]
        content = "\n".join([
            "### 文档清单（自动推导）", "",
            "| 文件 | 类型 |", "|------|------|",
            "\n".join(rows) if rows else "| - | 未检测到 README / docs |",
        ])
        summary = "{} 份文档{}".format(len(facts["docs"]), "（{}）".format(facts["docs"][0]) if facts["docs"] else "")
        return content, summary

    if key == "quality-system":
        rows = [
            ("测试目录", "、".join("`{}`".format(d) for d in facts["test_dirs"][:4]) or "未检测到"),
            ("测试文件", "{} 个{}".format(len(facts["test_files"]),
                                       "（{}）".format(facts["test_files"][0]) if facts["test_files"] else "")),
            ("测试框架", facts["test_framework"] or "未识别"),
            ("CI", "、".join("`{}`".format(c) for c in facts["ci"][:3]) or "未检测到"),
        ]
        content = "\n".join([
            "### 质量体系快照（自动推导）", "",
            "| 项 | 值 |", "|------|------|",
            "\n".join("| {} | {} |".format(k, v.replace("|", "/")) for k, v in rows),
        ])
        summary = "{} 个测试文件；CI {}".format(
            len(facts["test_files"]), "、".join(facts["ci"][:2]) if facts["ci"] else "未检测到")
        return content, summary

    if key == "dependencies":
        runtime = len(facts["node"].get("deps", {}))
        dev = len(facts["node"].get("dev_deps", {}))
        py_n = len(facts["python"]["deps"])
        rows = [
            ("清单/锁文件", "、".join("`{}`".format(r) for r in
                                  _dedup(facts["lockfiles"][:4] + facts["manifests"][:3])) or "未检测到"),
            ("Node 依赖", "runtime {} / dev {}".format(runtime, dev) if facts["node"] else "不适用"),
            ("Python 依赖", "{} 个{}".format(py_n, "（{}）".format("、".join(facts["python"]["deps"][:3]))
                                          if py_n else "") if py_n or facts["python"]["deps"] else "不适用"),
            ("第三方运行时依赖", "见上清单" if (runtime or py_n) else "未发现（可能为零依赖项目）"),
        ]
        content = "\n".join([
            "### 依赖与供应链快照（自动推导）", "",
            "| 项 | 值 |", "|------|------|",
            "\n".join("| {} | {} |".format(k, v.replace("|", "/")) for k, v in rows),
        ])
        summary = "{} 个锁文件；第三方运行时依赖 {}".format(
            len(facts["lockfiles"]), runtime + py_n)
        return content, summary

    if key == "dev-workflow":
        cmds = []
        for name in list(facts["scripts"])[:6]:
            cmds.append("{}{} {}".format(
                facts["package_manager"] or "npm", " run", name) if name not in ("start", "test") else
                "{} {}".format(facts["package_manager"] or "npm", name))
        for t in facts["make_targets"][:6]:
            cmds.append("make {}".format(t))
        rows = [
            ("任务运行器", facts["runner"] or "未检测到"),
            ("脚本", "、".join("`{}` → `{}`".format(k, v)
                             for k, v in list(facts["scripts"].items())[:6]) or "未检测到"),
            ("常用命令", "、".join("`{}`".format(c) for c in cmds[:8]) or "未检测到"),
            ("环境文件", "、".join("`{}`".format(r) for r in facts["dev_files"][:5]) or "未检测到"),
            ("分支", facts["branch"] or "未检测到（非 git 仓库或无 HEAD）"),
        ]
        content = "\n".join([
            "### 工作流与环境快照（自动推导）", "",
            "| 项 | 值 |", "|------|------|",
            "\n".join("| {} | {} |".format(k, v.replace("|", "/")) for k, v in rows),
            "",
            "> 环境事实（本机路径、凭据、登录态）不在此维度自动写入，请人工按需补充。",
        ])
        summary = "运行器 {}；命令 {}；分支 {}".format(
            facts["runner"] or "未检测到", "、".join(cmds[:3]) or "未检测到", facts["branch"] or "-")
        return content, summary

    return "", ""


def _section_bounds(text: str):
    start = text.find("## 维度内容")
    if start < 0:
        return None
    start = text.find("\n", start)
    if start < 0:
        return None
    start += 1
    end = text.find("## 相关维度", start)
    if end < 0:
        end = len(text)
    return start, end


def _has_manual_body(body: str) -> bool:
    cleaned = re.sub(r"(?s)<!--.*?-->", "", body)
    cleaned = cleaned.replace("{{dimensionContent}}", "")
    if AUTO_MARK in cleaned:
        return False
    return bool(cleaned.strip())


def _fill_dimension(dim_path: Path, content: str, summary: str, force: bool,
                    dry_run: bool = False) -> str:
    """把自动推导内容写进维度文件；返回 written / skipped / nofile。"""
    if not dim_path.is_file():
        return "nofile"
    text = dim_path.read_text(encoding="utf-8")
    bounds = _section_bounds(text)
    if bounds is None:
        return "skipped"
    start, end = bounds
    body = text[start:end]
    if _has_manual_body(body) and not force:
        return "skipped"
    if dry_run:
        return "written"

    new_body = "\n\n<!-- AGENT: 以下为 sop.py scan 自动推导的事实清单（非结论）；架构/取舍/教训等语义判断请人工补充。 -->\n\n{}\n\n".format(content.strip())
    text = text[:start] + new_body + text[end:]

    if re.search(r"(?m)^>\s*\*\*摘要\*\*:", text):
        text = re.sub(r"(?m)^(>\s*\*\*摘要\*\*:\s*).*$",
                      lambda m: m.group(1) + summary, text, count=1)
    text = re.sub(r"置信度\s*[^|]*\|", "置信度 中（{}，待复核） |".format(AUTO_MARK), text, count=1)
    text = re.sub(r"技能 v\d+(?:\.\d+)*", "技能 v{}".format(DIM_TEMPLATE_VERSION), text, count=1)
    write_text_lf(dim_path, text)
    return "written"


def _fill_identity(agents_path: Path, facts: dict, dry_run: bool) -> list:
    """只替换仍为「待分析」的身份表格子，不碰人工已填的值。"""
    if not agents_path.is_file():
        return []
    text = agents_path.read_text(encoding="utf-8")
    lang = facts["languages"][0] if facts["languages"] else None
    values = {
        "主语言": "{}（{} 个文件）".format(lang[0], lang[1]) if lang else "",
        "包管理器": facts["package_manager"] or "",
        "分支模型": facts["branch"] or "",
        "任务运行器": facts["runner"] or "",
    }
    changed = []
    for label, val in values.items():
        if not val:
            continue
        pat = re.compile(r"(?m)^(\|\s*" + label + r"\s*\|\s*)待分析(\s*\|)$")
        new, n = pat.subn(lambda m: m.group(1) + val.replace("|", "/") + m.group(2), text, count=1)
        if n:
            text = new
            changed.append("{} → {}".format(label, val))
    if changed and not dry_run:
        write_text_lf(agents_path, text)
    return changed


def cmd_scan(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    code_root = Path(mf.get("codeRoot", root))
    if not code_root.is_absolute():
        code_root = (Path.cwd() / code_root).resolve()
    facts = detect_facts(code_root)
    dry = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))

    only = (getattr(args, "only", None) or "").replace(",", " ").split()
    target_keys = None
    if only:
        target_keys = set()
        for dim in mf["dimensions"]:
            if str(dim.get("id")) in only or dim.get("key") in only:
                target_keys.add(dim.get("key"))
        if not target_keys:
            print("! --only 未匹配任何维度: {}".format(" ".join(only)))
            return 1

    print("扫描 {}（codeRoot={}）".format(root, code_root))
    if not facts["files_count"]:
        print("! 未检测到可识别文件：维度将写入占位说明，锚点改为手动维护")
    print("  {}".format(_lang_label(facts)))
    print("  清单: {}".format("、".join(facts["manifests"][:4]) or "无"))
    print("  测试: {} 个目录 / {} 个文件；CI: {}".format(
        len(facts["test_dirs"]), len(facts["test_files"]),
        "、".join(facts["ci"][:2]) or "无"))

    written, skipped, repaired = [], [], []
    for dim in mf["dimensions"]:
        key = dim.get("key")
        dim_path = dim_file_path(root, dim)
        if key in SCAN_KEYS and (target_keys is None or key in target_keys):
            content, summary = _dim_content(key, facts)
            if not facts["files_count"] and key != "topology":
                content, summary = "未检测到可识别文件（自动推导）。请人工补充本维度。", "未检测到可识别文件"
            if dim_path.is_file() and content:
                status = _fill_dimension(dim_path, content, summary, force, dry_run=dry)
                if status == "written":
                    written.append("{} {}".format(dim["id"], key))
                    print("{} dim {} {}: {}（{}）".format(
                        "预览" if dry else "+", dim["id"], key,
                        "将写入自动推导内容" if dry else "写入自动推导内容", summary))
                elif status == "skipped":
                    skipped.append("{} {}".format(dim["id"], key))
                    print("~ dim {} {}: 已有手工内容，跳过（--force 可覆盖）".format(dim["id"], key))
                else:
                    print("- dim {} {}: 文件缺失，跳过".format(dim["id"], key))

        anchors = dim.get("anchors") or []
        if not anchors or anchors == ["@tree"]:
            continue
        if target_keys is not None and key not in target_keys:
            continue
        if sum(len(glob_hits(code_root, p)) for p in anchors) > 0:
            continue
        candidates = _anchor_candidates(key, facts)
        dim["anchors"] = candidates
        repaired.append("dim {} {}: {} → {}".format(
            dim["id"], key, "、".join(anchors), "、".join(candidates) or "[]（改为手动维护）"))
        print("- 锚点修复 dim {} {}: {} → {}".format(
            dim["id"], key, "、".join(anchors), "、".join(candidates) or "[]（改为手动维护）"))

    identity = _fill_identity(root / "ai" / "AGENTS.md", facts, dry)
    for line in identity:
        print("+ 身份表: {}".format(line))

    if repaired and not dry:
        mf.setdefault("_index", {})
        mf["_index"]["lastIncrementalRefresh"] = mf["_index"].get("lastIncrementalRefresh", "-")
        save_manifest(root, mf)
        print("+ 已写回 manifest 锚点（{} 处）".format(len(repaired)))

    if dry:
        print("\n预览模式（--dry-run）：未写入任何文件")
    review = [d.get("key") for d in mf["dimensions"] if d.get("key") not in SCAN_KEYS]
    print("\n自动填充 {} 个维度，跳过 {} 个；建议人工补充: {}".format(
        len(written), len(skipped), "、".join(review)))
    if not dry:
        print("下一步：`python sop.py refresh --mark-fresh`（封存锚点 + 重写 AGENTS.md 生成区）")
    return 0


# ---------------------------------------------------------------------------
# 命令：entry（生成/校验仓库入口文件，通用无项目信息）
# ---------------------------------------------------------------------------

ENTRY_GEN_BEGIN = "<!-- ENTRY-GEN:BEGIN -->"
ENTRY_GEN_END = "<!-- ENTRY-GEN:END -->"
ENTRY_MANUAL_BEGIN = "<!-- ENTRY-MANUAL:BEGIN -->"
ENTRY_MANUAL_END = "<!-- ENTRY-MANUAL:END -->"

ENTRY_FORBIDDEN = [
    (re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)"), "疑似凭据关键词"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), "疑似 token 串"),
    (re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"), "私钥块"),
    (re.compile(r"(?i)([A-Z]:\\Users\\|/Users/|/home/[a-z]+/)"), "绝对家目录路径（应改用相对路径或说明性文字）"),
    (re.compile(r"(最近变更|状态补充|最新进展|本周|上周|昨天|今日更新)"), "时效性状态（应落 ai/status/ 归档，入口文件不记流水）"),
]


def entry_config(mf: dict) -> dict:
    """读取 manifest 的可选 entry 配置；file 为空表示未启用。"""
    cfg = mf.get("entry")
    if not isinstance(cfg, dict):
        return {}
    if not str(cfg.get("file") or "").strip():
        return {}
    out = dict(cfg)
    out["file"] = str(cfg["file"]).strip()
    try:
        out["maxLines"] = int(cfg.get("maxLines", 45))
    except (TypeError, ValueError):
        out["maxLines"] = 45
    return out


def render_entry_gen(mf: dict) -> str:
    """生成区正文：纪律索引（来自各维度 entryHint）+ 任务→维度路由表 + 维护契约。"""
    dims = mf.get("dimensions") or []
    blocks = []
    blocks.append("## 纪律索引（正文在各维度，本文件只做指向）")
    blocks.append("")
    hints = [(d, str(d.get("entryHint") or "").strip()) for d in dims]
    hints = [(d, h) for d, h in hints if h]
    if hints:
        for d, h in hints:
            blocks.append("- {}（→ 维度 {} {}）".format(h, d.get("id"), d.get("title")))
    else:
        blocks.append("- （尚未声明 entryHint：在 manifest 的维度条目上补 `entryHint`（一条 ≤1 行的纪律摘要），"
                      "再运行 `python sop.py entry` 生成）")
    blocks.append("")
    blocks.append("## 按任务读维度")
    blocks.append("")
    blocks.append("| 任务类型 | 必读维度 | 可选维度 |")
    blocks.append("|----------|----------|----------|")
    for t in mf.get("taskTypes") or []:
        must, opt = [], []
        if t == "onboarding":
            # onboarding 恒为全部维度（与 render_generated 用的 aggregate_task_map 语义一致）
            must = ["{} {}".format(d.get("id"), d.get("key")) for d in dims]
        else:
            for d in dims:
                tasks = d.get("tasks") or {}
                m = tasks.get("must") or []
                o = tasks.get("optional") or []
                tag = "{} {}".format(d.get("id"), d.get("key"))
                if t in m or "*" in m:
                    must.append(tag)
                elif t in o:
                    opt.append(tag)
        blocks.append("| {} | {} | {} |".format(t, "、".join(must) or "-", "、".join(opt) or "-"))
    blocks.append("")
    blocks.append("## 维护契约")
    blocks.append("")
    blocks.append("- 本生成区（`ENTRY-GEN` 标记之间）**禁止手改**：改 manifest 的 `entry` 或维度 `entryHint` 后，运行 `python sop.py entry` 重生成。")
    blocks.append("- 维度正文变更后：`python sop.py check --context` → `python sop.py refresh --mark-fresh`。")
    blocks.append("- 手写区（`ENTRY-MANUAL` 标记之间）归项目：放架构概览、易踩空点、上游资产边界等只能人判断的内容。")
    blocks.append("- **本文件不做第二事实源**：任何规则/环境事实/凭据都应写在维度体系（`ai/context/`）里，入口只保留指向。")
    return "\n".join(blocks)


def entry_lint(text: str, cfg: dict) -> list:
    """入口文件 lint：行数、权威声明、凭据、时效性状态、超长行。返回问题列表。"""
    issues = []
    lines = text.splitlines()
    if len(lines) > cfg["maxLines"]:
        issues.append("行数 {} 超过上限 {}（入口文件应做薄，细则放维度）".format(len(lines), cfg["maxLines"]))
    if "ai/context" not in text:
        issues.append("缺少「权威在 ai/context 维度体系」声明")
    for rx, why in ENTRY_FORBIDDEN:
        m = rx.search(text)
        if m:
            issues.append("{}：{}".format(why, m.group(0)[:40]))
    for i, ln in enumerate(lines, 1):
        if len(ln) > 400:
            issues.append("第 {} 行超长（{} 字符），疑似把维度正文抄进入口".format(i, len(ln)))
    return issues


def extract_block(text: str, begin: str, end: str) -> str:
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0 or j <= i:
        return ""
    return text[i + len(begin):j]


def cmd_entry(args) -> int:
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1
    cfg = entry_config(mf)
    if not cfg:
        print("! manifest 未启用 entry（可选配置：\"entry\": {\"file\": \"AGENTS.md\", \"maxLines\": 45, \"title\": \"…\"}）")
        print("  启用后本命令会在 codeRoot 下生成/刷新入口文件生成区，并可用 --check 校验。")
        return 0
    code_root = Path(mf.get("codeRoot", root))
    out_path = Path(args.out).resolve() if getattr(args, "out", None) else (code_root / cfg["file"])
    # 落点告知（不做分配器，只防「默认落仓根」这种静默选择）：
    # 落点应由人决定（仓根 / 与 ai 同级 / 项目更合适处），结论记进 plan.md 的 assumptions。
    try:
        inside_code = out_path.resolve().is_relative_to(Path(code_root).resolve())
    except (OSError, ValueError):
        inside_code = True
    if inside_code:
        note = "仓库内（codeRoot）"
        if Path(root).resolve() != Path(code_root).resolve():
            note += "；ai/ 在仓库外 → 入口指向的 ai/context 对 clone 者不可见，确认过落点再继续"
    else:
        note = "仓库外，与 ai/ 同级或自定义位置"
    print("entry 落点: {} — {}".format(out_path, note))
    gen = ENTRY_GEN_BEGIN + "\n" + render_entry_gen(mf) + "\n" + ENTRY_GEN_END
    manual_placeholder = (ENTRY_MANUAL_BEGIN + "\n"
                          + "<!-- 手写区：架构概览 / 易踩空点 / 上游资产边界。工具不覆盖，也不校验。 -->\n"
                          + ENTRY_MANUAL_END)

    if getattr(args, "check", False):
        if not out_path.is_file():
            print("! 入口文件不存在: {}".format(out_path))
            return 1
        text = out_path.read_text(encoding="utf-8")
        issues = entry_lint(text, cfg)
        current_gen = extract_block(text, ENTRY_GEN_BEGIN, ENTRY_GEN_END)
        if not current_gen:
            issues.append("缺少 ENTRY-GEN 标记（未由 `sop.py entry` 生成）")
        elif current_gen.strip() != gen[len(ENTRY_GEN_BEGIN):-len(ENTRY_GEN_END)].strip():
            issues.append("生成区与 manifest 不同步（重跑 `python sop.py entry`）")
        print("entry: {}（上限 {} 行）".format(out_path, cfg["maxLines"]))
        if issues:
            for it in issues:
                print("! {}".format(it))
            print("❌ 入口文件校验存在问题（{} 项）".format(len(issues)))
            return 1
        print("✅ 入口文件校验通过")
        return 0

    if out_path.is_file() and not getattr(args, "force", False):
        text = out_path.read_text(encoding="utf-8")
        if ENTRY_GEN_BEGIN in text and ENTRY_GEN_END in text:
            i = text.find(ENTRY_GEN_BEGIN)
            j = text.find(ENTRY_GEN_END) + len(ENTRY_GEN_END)
            new_text = text[:i] + gen + text[j:]
        else:
            print("! 已存在入口文件但缺少 ENTRY-GEN 标记，未改动内容。")
            print("  两种做法：① 手工插入 `{}` 与 `{}` 两行后重跑；② 确认可覆盖时加 --force。".format(
                ENTRY_GEN_BEGIN, ENTRY_GEN_END))
            return 1
    else:
        title = str(cfg.get("title") or "").strip() or "仓库入口（AGENTS.md）"
        head = [
            "# " + title,
            "",
            "> 本文件是**入口**，不是规则库：只做「纪律指向 + 按任务读维度 + 维护契约」。",
            "> 权威永远在 `ai/context/` 维度体系（{} 个维度），本文件不复述其正文。".format(len(mf.get("dimensions") or [])),
            "",
        ]
        new_text = "\n".join(head) + gen + "\n\n" + manual_placeholder + "\n"
    if not out_path.parent.is_dir():
        print("! 目标目录不存在: {}".format(out_path.parent))
        return 1
    write_text_lf(out_path, new_text)
    issues = entry_lint(new_text, cfg)
    print("✅ 入口文件已写入: {}".format(out_path))
    for it in issues:
        print("! {}".format(it))
    print("提示：手写区内的人工判断内容（架构概览/易踩空点/上游资产边界）不会被重生成覆盖。")
    return 0


# ---------------------------------------------------------------------------
# 命令：tasks（任务台账：从各任务 plan.md 聚合完成状态与相互关联）
# ---------------------------------------------------------------------------

TASKS_FILE = "TASKS.md"
TASK_SKIP_DIRS = {"context", "templates", "status", "archive"}
TASK_STATUS_ALIASES = {
    "pending": "pending", "todo": "pending", "planned": "pending", "new": "pending",
    "doing": "doing", "in-progress": "doing", "in_progress": "doing", "inprogress": "doing",
    "wip": "doing", "active": "doing", "open": "doing",
    "blocked": "blocked", "stuck": "blocked",
    "done": "done", "complete": "done", "completed": "done", "finished": "done", "closed": "done",
    "dropped": "dropped", "canceled": "dropped", "cancelled": "dropped", "abandoned": "dropped",
}
TASK_STATUS_ORDER = ["doing", "blocked", "pending", "done", "dropped"]
PLAN_LEDGER_FIELDS = {
    "task.id", "task.title", "task.type", "mode", "status", "parent",
    "depends", "relates", "supersedes", "updated", "owner", "claimed",
}
TASK_FIELD_RX = re.compile(r"^\|\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\|\s*(.*?)\s*\|\s*$")


def parse_plan_fields(plan: Path) -> dict:
    """读取 plan.md 顶部字段表（`| key | value |`）；只认台账相关字段，避免误读正文表格。"""
    fields = {}
    try:
        lines = plan.read_text(encoding="utf-8").splitlines()
    except OSError:
        return fields
    for line in lines:
        m = TASK_FIELD_RX.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in PLAN_LEDGER_FIELDS and key not in fields:
            fields[key] = value
    return fields


def field_or_empty(raw) -> str:
    """字段值去空白与反引号；占位符（- / — / 无 / None / null）归一为空串。"""
    v = (raw or "").strip().strip("`").strip()
    return "" if v in ("-", "—", "无", "None", "null") else v


def parse_refs(value: str) -> list:
    """把 `[a, b]` / `a、b` / `a b` 解析为 id 列表；空值与占位符返回 []。"""
    v = (value or "").strip()
    if not v or v.strip("[]") == "" or v in ("-", "—", "无", "None", "null"):
        return []
    v = v.strip("[]")
    out = []
    for part in re.split(r"[,，、;；\s]+", v):
        p = part.strip().strip("`'\"")
        if p and p not in ("-", "—", "无"):
            out.append(p)
    return out


def normalize_status(raw: str) -> tuple:
    """返回 (规范化状态, 原始值是否合法)。空值按 pending 处理。"""
    v = (raw or "").strip().strip("`'\"")
    if not v:
        return "pending", True
    key = v.lower().replace(" ", "-")
    if key in TASK_STATUS_ALIASES:
        return TASK_STATUS_ALIASES[key], True
    return v, False


def collect_tasks(ai_dir: Path) -> list:
    """扫描 ai/<task>/plan.md，抽出台账字段（不含正文）。"""
    tasks = []
    if not ai_dir.is_dir():
        return tasks
    for plan in sorted(ai_dir.glob("*/plan.md")):
        d = plan.parent
        if d.name.startswith(".") or d.name in TASK_SKIP_DIRS:
            continue
        f = parse_plan_fields(plan)
        status, status_ok = normalize_status(f.get("status", ""))
        parent = (f.get("parent") or "").strip().strip("`")
        if parent in ("-", "—", "无", "None", "null"):
            parent = ""
        tasks.append({
            "id": (f.get("task.id") or d.name).strip().strip("`"),
            "dir": d.name,
            "title": (f.get("task.title") or "").strip(),
            "type": (f.get("task.type") or "").strip(),
            "mode": (f.get("mode") or "").strip(),
            "status": status,
            "statusRaw": (f.get("status") or "").strip(),
            "statusOk": status_ok,
            "parent": parent,
            "depends": parse_refs(f.get("depends", "")),
            "relates": parse_refs(f.get("relates", "")),
            "supersedes": parse_refs(f.get("supersedes", "")),
            "updated": (f.get("updated") or "").strip(),
            "owner": field_or_empty(f.get("owner")),
            "claimed": field_or_empty(f.get("claimed")),
        })
    return tasks


def tasks_lint(tasks: list) -> list:
    """结构校验：状态枚举 / id 唯一 / 引用存在 / 自引用 / depends 成环 / done 前置未完成。"""
    issues = []
    ids = {}
    for t in tasks:
        if not t["statusOk"]:
            issues.append(("err", "任务 {} 的 status 非法：`{}`（可用 doing/pending/blocked/done/dropped）".format(
                t["id"], t["statusRaw"])))
        if not t["statusRaw"]:
            issues.append(("warn", "任务 {} 缺 status 字段（按 pending 处理）".format(t["id"])))
        if not t["title"]:
            issues.append(("warn", "任务 {} 缺 task.title（台账「目标」列为空）".format(t["id"])))
        if t["id"] in ids:
            issues.append(("err", "任务 id 重复：{}（{} 与 {}）".format(
                t["id"], ids[t["id"]]["dir"], t["dir"])))
        ids[t["id"]] = t
    for t in tasks:
        if t["parent"] and t["parent"] not in ids:
            issues.append(("err", "任务 {} 的 parent 指向未知任务：{}".format(t["id"], t["parent"])))
        if t["parent"] == t["id"]:
            issues.append(("err", "任务 {} 的 parent 指向自己".format(t["id"])))
        for field, refs in (("depends", t["depends"]), ("relates", t["relates"]),
                            ("supersedes", t["supersedes"])):
            for ref in refs:
                if ref == t["id"]:
                    issues.append(("err", "任务 {} 的 {} 指向自己".format(t["id"], field)))
                elif ref not in ids:
                    issues.append(("err", "任务 {} 的 {} 指向未知任务：{}".format(t["id"], field, ref)))

    graph = {t["id"]: [r for r in t["depends"] if r in ids] for t in tasks}
    color = {}

    def _visit(node: str, stack: list) -> None:
        color[node] = 1
        for nxt in graph.get(node, []):
            if color.get(nxt) == 1:
                issues.append(("err", "depends 成环：{} → {}（路径 {}）".format(
                    node, nxt, " → ".join(stack + [node, nxt]))))
            elif color.get(nxt) is None:
                _visit(nxt, stack + [node])
        color[node] = 2

    for node in graph:
        if color.get(node) is None:
            _visit(node, [])

    for t in tasks:
        if t["status"] != "done":
            continue
        for ref in t["depends"]:
            dep = ids.get(ref)
            if dep and dep["status"] != "done":
                issues.append(("warn", "任务 {} 标 done，但 depends 的 {} 仍是 {}".format(
                    t["id"], ref, dep["status"])))
    return issues


def task_edges(tasks: list) -> tuple:
    """返回 (关联边列表, 被依赖映射)。relates 视为无向，双向声明只保留一条。"""
    edges = []
    blocked_by = {}
    seen_undirected = set()
    for t in tasks:
        if t["parent"]:
            edges.append(("parent", t["id"], t["parent"]))
        for ref in t["depends"]:
            edges.append(("depends", t["id"], ref))
            blocked_by.setdefault(ref, []).append(t["id"])
        for ref in t["relates"]:
            pair = tuple(sorted((t["id"], ref)))
            if pair in seen_undirected:
                continue
            seen_undirected.add(pair)
            edges.append(("relates", t["id"], ref))
        for ref in t["supersedes"]:
            edges.append(("supersedes", t["id"], ref))
    return edges, blocked_by


def render_tasks(tasks: list, issues: list) -> str:
    """渲染台账正文（确定性输出：同输入同文本，便于 tasks --check 判断是否同步）。"""
    edges, blocked_by = task_edges(tasks)

    counts = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    dist = " / ".join("{} {}".format(s, counts[s]) for s in TASK_STATUS_ORDER if counts.get(s))

    lines = [
        "<!-- 本文件由 `python sop.py tasks` 生成，禁止手改：事实源是各任务 ai/<task>/plan.md 的字段表 -->",
        "",
        "# 任务台账",
        "",
        "| 统计 | 值 |",
        "|------|-----|",
        "| 任务数 | {} |".format(len(tasks)),
        "| 状态分布 | {} |".format(dist or "-"),
        "| 关联边 | {} |".format(len(edges)),
        "",
        "## 任务清单",
        "",
        "| 任务 | 状态 | 负责人 | 类型 | 目标 | 依赖 | 被依赖 | 更新 |",
        "|------|------|--------|------|------|------|--------|------|",
    ]
    order = {s: i for i, s in enumerate(TASK_STATUS_ORDER)}
    for t in sorted(tasks, key=lambda x: (order.get(x["status"], 99), x["id"])):
        lines.append("| `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            t["id"], t["status"], t["owner"] or "-", t["type"] or "-", t["title"] or "-",
            "、".join(t["depends"]) or "-",
            "、".join(blocked_by.get(t["id"], [])) or "-",
            t["updated"] or "-"))
    lines += ["", "## 关联关系", ""]
    if edges:
        lines += ["| 关系 | 从 | 到 |", "|------|----|----|"]
        for kind, src, dst in edges:
            lines.append("| {} | `{}` | `{}` |".format(kind, src, dst))
    else:
        lines.append("（暂无跨任务关联：在 plan.md 字段表写 `depends` / `relates` / `parent`）")
    lines += ["", "## 校验", ""]
    errs = [m for lv, m in issues if lv == "err"]
    warns = [m for lv, m in issues if lv == "warn"]
    if not errs and not warns:
        lines.append("✅ 结构完整：状态合法、id 唯一、引用存在、depends 无环。")
    else:
        for m in errs:
            lines.append("- ❌ {}".format(m))
        for m in warns:
            lines.append("- ⚠️ {}".format(m))
    lines.append("")
    return "\n".join(lines)


def write_tasks(root: Path, out: Path = None, quiet: bool = False) -> int:
    """渲染并写盘 ai/TASKS.md；返回任务数（0 = 无任务，未写）。"""
    ai_dir = root / "ai"
    tasks = collect_tasks(ai_dir)
    if not tasks:
        return 0
    text = render_tasks(tasks, tasks_lint(tasks))
    target = out or (ai_dir / TASKS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(target, text)
    if not quiet:
        print("✅ 任务台账已写入: {}（{} 个任务）".format(target, len(tasks)))
    return len(tasks)


def infer_status(task_dir: Path) -> tuple:
    """从 NOTES.md 找「已收工」的客观证据；找不到返回 (None, "")，交给人工确认。"""
    notes = task_dir / "NOTES.md"
    if not notes.is_file():
        return None, ""
    try:
        text = notes.read_text(encoding="utf-8")
    except OSError:
        return None, ""
    if re.search(r"(验收通过|收工|\[x\]\s*验收|状态[:：]\s*(done|完成))", text):
        return "done", "NOTES 有验收/收工记录"
    return None, ""


def backfill_plan(plan: Path, status_default: str, dry_run: bool) -> dict:
    """给缺台账字段的 plan.md 顶部字段表补行（幂等）；返回补录报告。"""
    try:
        lines = plan.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "reason": "读取失败：{}".format(exc), "added": []}
    existing = set()
    last_row = -1
    for i, ln in enumerate(lines):
        m = TASK_FIELD_RX.match(ln)
        if not m:
            continue
        if m.group(1) in PLAN_LEDGER_FIELDS:
            existing.add(m.group(1))
            last_row = i
    if last_row < 0:
        return {"ok": False, "reason": "未找到字段表（| key | value |）", "added": []}

    inferred, basis = infer_status(plan.parent)
    values = {
        "status": inferred or status_default,
        "parent": "-",
        "depends": "-",
        "relates": "-",
        "updated": datetime.fromtimestamp(plan.stat().st_mtime).strftime("%Y-%m-%d"),
        "owner": "-",
        "claimed": "-",
    }
    need = [k for k in ("status", "parent", "depends", "relates", "updated", "owner", "claimed")
            if k not in existing]
    if not need:
        return {"ok": True, "reason": "已齐全", "added": [], "needs_confirm": False}
    new_rows = ["| {} | {} |".format(k, values[k]) for k in need]
    lines[last_row + 1:last_row + 1] = new_rows
    if not dry_run:
        write_text_lf(plan, "\n".join(lines) + "\n")
    return {
        "ok": True,
        "added": need,
        "values": values,
        "needs_confirm": ("status" in need) and not inferred,
        "basis": basis or "缺省（需人工确认）",
    }


def suggest_relations(ai_dir: Path, tasks: list) -> list:
    """关系建议：若某任务的 plan/NOTES 里出现另一个任务的 id 或目录名，提示 relates 候选（不写盘）。"""
    out, seen = [], set()
    for t in tasks:
        for fname in ("plan.md", "NOTES.md"):
            p = ai_dir / t["dir"] / fname
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            for other in tasks:
                if other["dir"] == t["dir"]:
                    continue
                if other["id"] not in text and other["dir"] not in text:
                    continue
                pair = tuple(sorted((t["id"], other["id"])))
                if pair in seen:
                    continue
                seen.add(pair)
                out.append((t["dir"], t["id"], other["id"], fname))
    return out


def cmd_tasks_backfill(ai_dir: Path, args) -> int:
    tasks = collect_tasks(ai_dir)
    if not tasks:
        print("（ai/ 下暂无任务 plan.md，无需补录）")
        return 0
    status_default, _ = normalize_status(getattr(args, "status", "pending") or "pending")
    dry = bool(getattr(args, "dry_run", False))
    print("tasks 补录：{} 个任务{}".format(len(tasks), "（dry-run，不落盘）" if dry else ""))
    changed, confirm, skipped = 0, [], 0
    for t in tasks:
        rep = backfill_plan(ai_dir / t["dir"] / "plan.md", status_default, dry)
        if not rep["ok"]:
            print("- `{}`：跳过（{}）".format(t["id"], rep["reason"]))
            skipped += 1
            continue
        if not rep["added"]:
            print("- `{}`：已齐全".format(t["id"]))
            continue
        changed += 1
        vals = rep["values"]
        print("- `{}`：补 {}（status={} ← {}）".format(
            t["id"], "、".join(rep["added"]), vals["status"], rep["basis"]))
        if rep["needs_confirm"]:
            confirm.append(t["id"])
    if confirm:
        print("\n需人工确认状态：{}（补录用了缺省 {}，请按实际改成 doing/done/blocked/dropped 后重跑 `python sop.py tasks`）".format(
            "、".join(confirm), status_default))
    if not getattr(args, "no_suggest", False):
        sug = suggest_relations(ai_dir, tasks)
        if sug:
            print("\n建议关系（未写入；确认后填进 plan.md 的 relates）：")
            for src_dir, src_id, dst_id, fname in sug:
                print("  {} → {}（出现在 {}/{}）".format(src_id, dst_id, src_dir, fname))
    print("\n补录 {} 个{}；下一步：`python sop.py tasks` 生成台账 → `python sop.py tasks --check` 校验".format(
        changed, "（dry-run 未落盘）" if dry else ""))
    return 0


def tasks_payload(ai_dir: Path, tasks: list) -> dict:
    """台账的机器可读快照：全量任务 + 派生字段（被依赖/未完成前置/认领租约）+ 统计。"""
    edges, blocked_by = task_edges(tasks)
    by_id = {t["id"]: t for t in tasks}
    items = []
    for t in sorted(tasks, key=lambda x: (x["status"], x["id"])):
        lock = ai_dir / t["dir"] / CLAIM_NAME
        claim = read_claim(lock) if lock.is_file() else {}
        items.append({
            "id": t["id"], "dir": t["dir"], "title": t["title"], "type": t["type"],
            "mode": t["mode"], "status": t["status"], "statusRaw": t["statusRaw"],
            "parent": t["parent"], "supersedes": t["supersedes"],
            "owner": t["owner"], "claimed": t["claimed"],
            "depends": t["depends"], "relates": t["relates"],
            "depends_open": [d for d in t["depends"]
                             if d in by_id and by_id[d]["status"] not in ("done", "dropped")],
            "depends_missing": [d for d in t["depends"] if d not in by_id],
            "blocked_by": blocked_by.get(t["id"], []),
            "updated": t["updated"],
            "claim": ({
                "owner": claim.get("owner"),
                "claimed": claim.get("claimed"),
                "lease": claim.get("lease", DEFAULT_CLAIM_LEASE),
                "minutes_left": claim_minutes_left(claim),
            } if claim else None),
        })
    stats = {}
    for t in tasks:
        stats[t["status"]] = stats.get(t["status"], 0) + 1
    unfinished = sum(1 for t in tasks if t["status"] not in ("done", "dropped"))
    return {
        "tasks": len(tasks),
        "unfinished": unfinished,
        "edges": len(edges),
        "stats": stats,
        "items": items,
    }


def cmd_tasks(args) -> int:
    root = Path(args.root).resolve()
    ai_dir = root / "ai"
    if not ai_dir.is_dir():
        print("! ai/ 不存在，请先运行 `python sop.py init`")
        return 1
    if getattr(args, "backfill", False):
        return cmd_tasks_backfill(ai_dir, args)
    tasks = collect_tasks(ai_dir)
    if not tasks:
        print("（ai/ 下暂无任务 plan.md）")
        return 0
    issues = tasks_lint(tasks) + claim_lint(ai_dir, tasks)
    if getattr(args, "json", False):
        print(json.dumps(tasks_payload(ai_dir, tasks), ensure_ascii=False))
        return 0
    text = render_tasks(tasks, issues)
    target = Path(args.out).resolve() if getattr(args, "out", None) else ai_dir / TASKS_FILE
    edges = len(task_edges(tasks)[0])

    if getattr(args, "check", False):
        print("tasks: {} 个任务 / {} 条关联（{}）".format(len(tasks), edges, target))
        for lv, m in issues:
            print("{} {}".format("!" if lv == "err" else "~", m))
        bad = [m for lv, m in issues if lv == "err"]
        if not target.is_file():
            print("! 台账不存在: {}（运行 `python sop.py tasks`）".format(target))
            print("❌ 任务台账校验存在问题")
            return 1
        if target.read_text(encoding="utf-8").strip() != text.strip():
            print("! 台账与 plan.md 不同步（运行 `python sop.py tasks` 重生成）")
            print("❌ 任务台账校验存在问题")
            return 1
        if bad:
            print("❌ 任务台账校验存在问题")
            return 1
        print("✅ 任务台账校验通过")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(target, text)
    print("✅ 任务台账已写入: {}（{} 个任务 / {} 条关联）".format(target, len(tasks), edges))
    for lv, m in issues:
        print("{} {}".format("!" if lv == "err" else "~", m))
    return 0


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
# 命令：next（台账可执行前沿——只做约束推导，不排优先级）
# ---------------------------------------------------------------------------

CLAIM_NAME = ".claim"
DEFAULT_CLAIM_LEASE = 120  # 分钟；超时后可被接管，避免会话崩了把任务永久锁死


def default_owner() -> str:
    """认领人默认标识：$SOP_OWNER 优先，否则 用户名@主机名（跨机/多会话可分辨）。"""
    env_owner = (os.environ.get("SOP_OWNER") or "").strip()
    if env_owner:
        return env_owner
    user = (os.environ.get("USERNAME") or os.environ.get("USER") or "unknown").strip()
    host = (os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "").strip()
    return "{}@{}".format(user, host) if host else user


def read_claim(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def claim_minutes_left(claim: dict) -> int:
    """剩余租约分钟；记录不可解析时返回 -1（当作已过期/不可信）。"""
    try:
        claimed = datetime.fromisoformat(str(claim.get("claimed", "")))
        lease = int(claim.get("lease", DEFAULT_CLAIM_LEASE))
    except (TypeError, ValueError):
        return -1
    if claimed.tzinfo is None:
        claimed = claimed.astimezone()
    delta = claimed + timedelta(minutes=lease) - datetime.now().astimezone()
    return int(delta.total_seconds() // 60)


def create_claim(path: Path, data: dict) -> bool:
    """原子创建锁文件（O_EXCL）：并发会话里只有一个能成功。"""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


def set_plan_fields(plan: Path, values: dict) -> bool:
    """改写 plan.md 顶部字段表的值；缺行补在字段表末尾。保留正文与 LF。"""
    try:
        lines = plan.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    seen, last_row = set(), -1
    for i, ln in enumerate(lines):
        m = TASK_FIELD_RX.match(ln)
        if not m or m.group(1) not in PLAN_LEDGER_FIELDS:
            continue
        key = m.group(1)
        seen.add(key)
        last_row = i
        if key in values:
            lines[i] = "| {} | {} |".format(key, values[key])
    if last_row < 0:
        return False
    missing = [k for k in values if k not in seen]
    if missing:
        lines[last_row + 1:last_row + 1] = ["| {} | {} |".format(k, values[k]) for k in missing]
    write_text_lf(plan, "\n".join(lines) + "\n")
    return True


def claim_lint(ai_dir: Path, tasks: list) -> list:
    """认领一致性与租约告警（不阻塞）：超时、owner 缺锁、锁与 owner 不一致、完成仍留锁。"""
    issues = []
    for t in tasks:
        lock = ai_dir / t["dir"] / CLAIM_NAME
        claim = read_claim(lock) if lock.is_file() else {}
        left = claim_minutes_left(claim) if claim else None
        if claim and left is not None and left <= 0:
            issues.append(("warn", "任务 {} 的认领已超时（`{}` 于 {} 认领，租约 {} 分钟）——确认后 `next --claim {} --force` 接管或 `next --release {}`".format(
                t["id"], claim.get("owner", "?"), claim.get("claimed", "?"),
                claim.get("lease", DEFAULT_CLAIM_LEASE), t["id"], t["id"])))
        if t["owner"] and not claim and t["status"] not in ("done", "dropped"):
            issues.append(("warn", "任务 {} 有 owner=`{}` 但缺 .claim 锁（可能被手工改，或跨机未同步）".format(
                t["id"], t["owner"])))
        if claim and t["owner"] and claim.get("owner") != t["owner"]:
            issues.append(("warn", "任务 {} 的 owner=`{}` 与锁记录 `{}` 不一致（改字段后没重跑 claim？）".format(
                t["id"], t["owner"], claim.get("owner"))))
        if claim and t["status"] in ("done", "dropped"):
            issues.append(("warn", "任务 {} 已 {} 但仍留有认领锁（用 `next --release {}` 清掉）".format(
                t["id"], t["status"], t["id"])))
    return issues


def _find_task(tasks: list, tid: str):
    for t in tasks:
        if t["id"] == tid or t["dir"] == tid:
            return t
    return None


def claim_task(args, tasks: list, ai_dir: Path, owner: str) -> int:
    task = _find_task(tasks, args.claim)
    if task is None:
        print("! 找不到任务 `{}`（先看 `python sop.py next` 的前沿清单）".format(args.claim))
        return 1
    if task["status"] in ("done", "dropped"):
        print("! 任务 `{}` 已是 {}，不用认领".format(task["id"], task["status"]))
        return 1
    by_id = {t["id"]: t for t in tasks}
    st = next_dep_state(task["depends"], by_id)
    if st["open"] or st["missing"]:
        blockers = "、".join("`{}`({})".format(d, by_id[d]["status"] if d in by_id else "缺失")
                             for d in st["open"] + st["missing"])
        print("! 任务 `{}` 还有未完成前置：{}".format(task["id"], blockers))
        return 1

    lease = max(1, int(getattr(args, "lease", DEFAULT_CLAIM_LEASE)))
    plan_path = ai_dir / task["dir"] / "plan.md"
    lock = ai_dir / task["dir"] / CLAIM_NAME
    claim = {
        "task": task["id"],
        "owner": owner,
        "claimed": datetime.now().astimezone().isoformat(timespec="seconds"),
        "lease": lease,
    }
    old, takeover = {}, ""
    if lock.is_file():
        old = read_claim(lock)
        left = claim_minutes_left(old)
        if left > 0 and not getattr(args, "force", False):
            print("! 任务 `{}` 已被 `{}` 认领（{}，租约 {} 分钟，剩 {} 分钟）".format(
                task["id"], old.get("owner", "?"), old.get("claimed", "?"),
                old.get("lease", DEFAULT_CLAIM_LEASE), left))
            print("  确认那个会话已死再抢占：`python sop.py next --claim {} --as {} --force`".format(
                task["id"], owner))
            return 1
        takeover = "接管（租约已过期）" if left <= 0 else "强制接管（原认领未到期）"
        if not create_claim(lock, claim) and not lock.is_file():
            print("! 无法写认领锁：{}".format(lock))
            return 1
        write_text_lf(lock, json.dumps(claim, ensure_ascii=False, indent=2) + "\n")
    elif not create_claim(lock, claim):
        print("! 认领锁刚被另一个会话抢到：{}（重跑 next 看看是谁）".format(lock))
        return 1

    if not set_plan_fields(plan_path, {
            "status": "doing", "owner": owner, "claimed": claim["claimed"],
            "updated": datetime.now().date().isoformat()}):
        print("! 锁已建，但写 plan.md 字段失败：{}".format(plan_path))
        return 1

    print("✅ 认领 `{}`：owner=`{}`，租约 {} 分钟（{}）".format(
        task["id"], owner, lease, claim["claimed"]))
    print("   锁文件 {}".format(lock))
    if takeover:
        print("   注意：{}（原认领 `{}`）".format(takeover, old.get("owner", "?")))
    cmd_tasks(argparse.Namespace(root=str(ai_dir.parent), check=False,
                                 backfill=False, out=None, **{}))
    return 0


def release_task(args, tasks: list, ai_dir: Path, owner: str) -> int:
    task = _find_task(tasks, args.release)
    if task is None:
        print("! 找不到任务 `{}`".format(args.release))
        return 1
    lock = ai_dir / task["dir"] / CLAIM_NAME
    plan_path = ai_dir / task["dir"] / "plan.md"
    claim = read_claim(lock) if lock.is_file() else {}
    holder = claim.get("owner") or task["owner"]
    force = getattr(args, "force", False)
    if holder and holder != owner and not force:
        print("! `{}` 正被 `{}` 认领，释放别人的任务要加 --force".format(task["id"], holder))
        return 1
    if not lock.is_file() and not task["owner"]:
        print("! `{}` 没有认领记录（已释放，或本是手工 doing）".format(task["id"]))
        return 1
    if lock.is_file():
        lock.unlink()
    finished = task["status"] in ("done", "dropped")
    if finished:
        # 已完成任务：owner/claimed 是"谁做的"记录，不清空；只删锁
        values = {"status": task["status"], "updated": datetime.now().date().isoformat()}
    else:
        values = {"status": "pending", "owner": "-", "claimed": "-",
                  "updated": datetime.now().date().isoformat()}
    if not set_plan_fields(plan_path, values):
        print("! 锁已删除，但写 plan.md 失败：{}".format(plan_path))
        return 1
    if finished:
        print("✅ 已释放 `{}` 的锁（status 保持 {}，owner 留作记录）".format(
            task["id"], task["status"]))
    else:
        print("✅ 已释放 `{}`（status→pending，owner 清空）".format(task["id"]))
    cmd_tasks(argparse.Namespace(root=str(ai_dir.parent), check=False,
                                 backfill=False, out=None, **{}))
    return 0


def next_dep_state(depends, by_id) -> dict:
    """把 depends 分类成 done / dropped / open（未完成）/ missing（引用不存在）。"""
    state = {"done": [], "dropped": [], "open": [], "missing": []}
    for dep in depends:
        other = by_id.get(dep)
        if other is None:
            state["missing"].append(dep)
        elif other["status"] == "done":
            state["done"].append(dep)
        elif other["status"] == "dropped":
            state["dropped"].append(dep)
        else:
            state["open"].append(dep)
    return state


def next_levels(tasks) -> tuple:
    """按 depends 迭代松弛算层级（1 = 无未完成前置）；算不出的就是缺失引用或成环。"""
    by_id = {t["id"]: t for t in tasks}
    pending = [t for t in tasks if t["status"] not in ("done", "dropped")]
    levels, states = {}, {}
    while True:
        progressed = False
        for t in pending:
            if t["id"] in levels:
                continue
            st = next_dep_state(t["depends"], by_id)
            states[t["id"]] = st
            if st["missing"] or not all(d in levels for d in st["open"]):
                continue
            levels[t["id"]] = 1 + max([levels[d] for d in st["open"]] or [0])
            progressed = True
        if not progressed:
            break
    return levels, states


def _dep_label(dep, by_id) -> str:
    other = by_id.get(dep)
    return "`{}`({})".format(dep, other["status"] if other else "缺失")


def _print_wave(level, items, by_id, ai_dir: Path) -> None:
    head = ("第 {} 波（现在可做）".format(level) if level == 1
            else "第 {} 波（等前序）".format(level))
    print(head)
    for t, st in items:
        note = ""
        if st["open"]:
            note = "← 等 " + "、".join(_dep_label(d, by_id) for d in st["open"])
        if st["dropped"]:
            tail = "前置已放弃 {}".format("、".join("`{}`".format(d) for d in st["dropped"]))
            note = (note + "；" + tail) if note else tail
        lock = ai_dir / t["dir"] / CLAIM_NAME
        claim = read_claim(lock) if lock.is_file() else {}
        if claim:
            left = claim_minutes_left(claim)
            state = "认领中，剩 {} 分钟".format(left) if left > 0 else "认领已超时"
            tag = "（`{}` {}）".format(claim.get("owner", "?"), state)
        elif t["owner"]:
            tag = "（`{}`，缺锁记录）".format(t["owner"])
        else:
            tag = ""
        print("  - `{}` [{}] {}{}{}".format(t["id"], t["status"], t["title"],
                                            (" " + note) if note else "", tag))


def cmd_next(args) -> int:
    """输出可执行前沿：现在能做什么、被谁挡着、哪些排不出来；`--claim/--release` 走认领。"""
    root = Path(args.root).resolve()
    ai_dir = root / "ai"
    tasks = collect_tasks(ai_dir)
    owner = getattr(args, "as_name", None) or default_owner()
    if getattr(args, "claim", None):
        if not tasks:
            print("! ai/ 下没有任务 plan.md，无法认领")
            return 1
        return claim_task(args, tasks, ai_dir, owner)
    if getattr(args, "release", None):
        if not tasks:
            print("! ai/ 下没有任务 plan.md，无法释放")
            return 1
        return release_task(args, tasks, ai_dir, owner)
    if not tasks:
        print("（ai/ 下暂无任务 plan.md：先用模板建 ai/<task>/plan.md，再跑 `sop.py tasks`）")
        return 0

    by_id = {t["id"]: t for t in tasks}
    levels, states = next_levels(tasks)
    unfinished = [t for t in tasks if t["status"] not in ("done", "dropped")]

    ready, blocked, external, unsorted_tasks = [], [], [], []
    for t in unfinished:
        st = states.get(t["id"]) or next_dep_state(t["depends"], by_id)
        if t["id"] not in levels:
            unsorted_tasks.append((t, st))
        elif st["open"]:
            blocked.append((t, st))
        elif t["status"] == "blocked":
            external.append((t, st))
        else:
            ready.append((t, st))

    def order(pairs):
        return sorted(pairs, key=lambda p: (0 if p[0]["status"] == "doing" else 1, p[0]["id"]))

    waves = {}
    for t, st in ready + blocked:
        waves.setdefault(levels[t["id"]], []).append((t, st))

    if getattr(args, "json", False):
        payload = {
            "tasks": len(tasks),
            "unfinished": len(unfinished),
            "ready": [dict(t, wave=levels[t["id"]]) for t, _ in order(ready)],
            "blocked": [
                {"id": t["id"], "status": t["status"], "title": t["title"],
                 "wave": levels[t["id"]],
                 "blocking": [{"id": d, "status": by_id[d]["status"]} for d in st["open"]]}
                for t, st in order(blocked)
            ],
            "unsorted": [
                {"id": t["id"], "status": t["status"], "title": t["title"],
                 "missing": st["missing"],
                 "cycle": [d for d in st["open"] if d not in levels]}
                for t, _ in order(unsorted_tasks)
            ],
            "waves": [
                {"wave": lvl, "tasks": [t["id"] for t, _ in order(waves[lvl])]}
                for lvl in sorted(waves)
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print("next: {} 个任务 / {} 个未完成".format(len(tasks), len(unfinished)))
    if not unfinished:
        print("\n✅ 所有任务已完成或已放弃。")
        return 0

    for lvl in sorted(waves):
        print("")
        _print_wave(lvl, order(waves[lvl]), by_id, ai_dir)

    if external:
        print("\n外部阻塞（depends 已满足，但 status=blocked）")
        for t, _ in order(external):
            print("  - `{}` [{}] {}".format(t["id"], t["status"], t["title"]))

    if unsorted_tasks:
        print("\n无法排序（depends 引用缺失或成环）")
        for t, st in order(unsorted_tasks):
            bits = []
            if st["missing"]:
                bits.append("等 " + "、".join("`{}`(缺失)".format(d) for d in st["missing"]))
            stuck = [d for d in st["open"] if d not in levels]
            if stuck:
                bits.append("depends 成环：" + "、".join("`{}`".format(d) for d in stuck))
            print("  - `{}` [{}] {}{}".format(t["id"], t["status"], t["title"],
                                              (" ← " + "；".join(bits)) if bits else ""))

    if not ready:
        print("\n当前没有可执行任务：先解掉上面列出的阻塞，或把前置改 done/dropped。")
    return 0


# ---------------------------------------------------------------------------
# 命令：gates（四道门 + 新鲜度检查合成一次调用，供 pre-commit / hook 引用）
# ---------------------------------------------------------------------------

GATE_NAMES = ("context", "dims", "audit", "entry", "tasks")


def _capture(fn) -> tuple:
    """跑一个子门并接住它自己的输出，返回 (退出码, 最后一行有效输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    lines = [ln.strip() for ln in buf.getvalue().splitlines() if ln.strip()]
    return code, (lines[-1] if lines else "")


def cmd_gates(args) -> int:
    """一次跑完五道门，任一失败退出码 1。

    为什么单独做一个命令："记得跑四道门"是最不可靠的一环。合成一条命令 + 一个退出码之后，
    pre-commit hook、CI、Claude Code hook 都能直接调用，强制力来自机制而不是自觉。
    """
    root = Path(args.root).resolve()
    mf = load_manifest(root)
    if mf is None:
        if getattr(args, "skip_if_missing", False):
            print("~ 未接入 SOP（缺 ai/{}），gates 跳过".format(MANIFEST_NAME))
            return 0
        print("! ai/{} 缺失，请先运行 `python sop.py init`".format(MANIFEST_NAME))
        return 1

    results = []

    def add(name, status, detail, code):
        results.append({"name": name, "status": status, "detail": detail, "exit": code})

    code, detail = _capture(lambda: cmd_context_check(root))
    add("context", "pass" if code == 0 else "fail", detail, code)

    stale = collect_stale(root, mf)
    if stale:
        ids = "、".join("dim {}".format(d["id"]) for d, _, _ in stale)
        add("dims", "fail", "{} 个维度过期，需复核后 refresh --mark-fresh：{}".format(len(stale), ids), 1)
    else:
        add("dims", "pass", "所有维度最新", 0)

    code, detail = _capture(lambda: cmd_audit(argparse.Namespace(root=str(root), check=True)))
    add("audit", "pass" if code == 0 else "fail", detail, code)

    if not entry_config(mf):
        add("entry", "skip", "manifest 未配置 entry（跳过）", 0)
    else:
        code, detail = _capture(lambda: cmd_entry(
            argparse.Namespace(root=str(root), check=True, out=None, force=False)))
        add("entry", "pass" if code == 0 else "fail", detail, code)

    code, detail = _capture(lambda: cmd_tasks(
        argparse.Namespace(root=str(root), check=True, backfill=False, out=None)))
    add("tasks", "pass" if code == 0 else "fail", detail, code)

    failed = [r for r in results if r["status"] == "fail"]
    skipped = [r for r in results if r["status"] == "skip"]

    if getattr(args, "json", False):
        print(json.dumps({
            "ok": not failed,
            "passed": len([r for r in results if r["status"] == "pass"]),
            "failed": [r["name"] for r in failed],
            "skipped": [r["name"] for r in skipped],
            "gates": results,
        }, ensure_ascii=False))
        return 1 if failed else 0

    print("gates: {} 项（{}）".format(len(results), " / ".join(GATE_NAMES)))
    for r in results:
        mark = {"pass": "✅", "fail": "❌", "skip": "~"}[r["status"]]
        print("{} {:<8} {}".format(mark, r["name"], r["detail"]))
    if failed:
        print("\n❌ {} 项未通过：{}".format(len(failed), "、".join(r["name"] for r in failed)))
        print("   修法：context→`check --context`；dims→复核后 `refresh --mark-fresh`；"
              "audit→按 §1.5 判定表裁决；entry→`entry`；tasks→`tasks`")
        return 1
    print("\n✅ 全部通过（{}{}）".format(
        len(results), "，{} 项跳过".format(len(skipped)) if skipped else ""))
    return 0


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
    # 默认归属表引用 9 个维度 key；迁移来的项目可能只有其中几个，
    # 原样写入会让 check --context 直接判「引用未知维度 key」。
    dim_keys = {d["key"] for d in dimensions}
    attribution = {}
    for info_type, keys in DEFAULT_MANIFEST["attribution"].items():
        kept = [k for k in keys if k in dim_keys]
        if kept or not keys:
            attribution[info_type] = kept
    return {
        "_comment": MIGRATE_COMMENT,
        "version": 2,
        "codeRoot": code_root,
        "taskTypes": task_types,
        # 默认归属表（v4.4+），已裁剪为项目实际声明的维度；audit 依赖
        "attribution": attribution,
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
    write_text_lf(dim_file, new_text)
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
    write_text_lf(idx_path, rebuild_agents_index(root, project_name, manual_text))
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
        write_text_lf(Path(args.out), report)
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
    p = argparse.ArgumentParser(prog="sop.py", description="project-task-sop CLI v4.12.2（manifest 驱动）")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("init", help="初始化 ai/ 结构 + manifest + 维度文件")
    pi.add_argument("--root", default=".", help="项目根目录（默认当前目录）")
    pi.add_argument("--project", default=None, help="项目名（默认取目录名）")
    pi.add_argument("--force", action="store_true", help="重建 AGENTS.md 生成区与缺失维度文件")

    pr = sub.add_parser("refresh", help="按 manifest 锚点检测过期维度")
    pr.add_argument("--root", default=".")
    pr.add_argument("--mark-fresh", action="store_true",
                    help="封存锚点 + 递增版本 + 重写 AGENTS.md 生成区")
    pr.add_argument("--only", default="",
                    help="仅封存指定维度（id 或 key，逗号/空格分隔，如 --only 12,13）；缺省封存全部")

    ps = sub.add_parser("scan", help="自动推导维度初稿（init 后运行；只写事实、不做架构判断）")
    ps.add_argument("--root", default=".")
    ps.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不落盘")
    ps.add_argument("--force", action="store_true", help="覆盖维度文件里已有的人工内容")
    ps.add_argument("--only", default="",
                    help="只处理指定维度（id 或 key，逗号/空格分隔，如 --only 2,5）")

    pe = sub.add_parser("entry", help="生成/校验仓库入口文件（入口 + 纪律指向 + 任务路由，不复制维度正文）")
    pe.add_argument("--root", default=".")
    pe.add_argument("--check", action="store_true", help="只校验：行数上限 / 权威声明 / 凭据 / 时效状态 / 生成区是否与 manifest 同步")
    pe.add_argument("--force", action="store_true", help="目标已存在且无 ENTRY-GEN 标记时，允许整体覆盖")
    pe.add_argument("--out", default=None, help="自定义输出路径（默认 codeRoot/<entry.file>）")

    ptask = sub.add_parser("tasks", help="任务台账：从各任务 plan.md 聚合完成状态与相互关联，渲染 ai/TASKS.md")
    ptask.add_argument("--root", default=".")
    ptask.add_argument("--check", action="store_true",
                       help="只校验：status 枚举 / id 唯一 / 引用存在 / depends 成环 / 台账是否与 plan.md 同步")
    ptask.add_argument("--out", default=None, help="自定义台账路径（默认 ai/TASKS.md）")
    ptask.add_argument("--json", action="store_true",
                       help="只读导出：单行 JSON（全量任务 + 派生字段 + 认领租约 + 统计），不写台账")
    ptask.add_argument("--backfill", action="store_true",
                       help="存量接入：给缺台账字段的 plan.md 补 status/parent/depends/relates/updated（建议先 --dry-run）")
    ptask.add_argument("--status", default="pending",
                       help="补录时的缺省 status（默认 pending；NOTES 有验收记录时优先推断 done）")
    ptask.add_argument("--dry-run", action="store_true", help="只打印将补的字段，不写盘")
    ptask.add_argument("--no-suggest", action="store_true", help="不打印跨任务关系建议")

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

    pg = sub.add_parser("gates", help="五道门一次跑完（context/dims/audit/entry/tasks），供 pre-commit、hook、CI 引用")
    pg.add_argument("--root", default=".")
    pg.add_argument("--json", action="store_true", help="机器可读输出（单行 JSON）")
    pg.add_argument("--skip-if-missing", action="store_true",
                    help="未接入 SOP（缺 ai/）时安静通过，便于 hook 在任意项目里运行")

    pn = sub.add_parser("next", help="台账可执行前沿：现在能做什么、被谁挡着（只有 depends 约束，不排优先级）")
    pn.add_argument("--root", default=".")
    pn.add_argument("--json", action="store_true", help="机器可读输出（单行 JSON）")
    pn.add_argument("--claim", metavar="TASK_ID", help="认领任务：写 owner/claimed + 建 .claim 租约锁（多会话/多机防抢）")
    pn.add_argument("--release", metavar="TASK_ID", help="释放认领：删锁 + status→pending + owner/claimed 清空")
    pn.add_argument("--as", dest="as_name", metavar="NAME",
                    help="认领人标识（缺省 $SOP_OWNER 或 用户名@主机名）")
    pn.add_argument("--lease", type=int, default=DEFAULT_CLAIM_LEASE, metavar="MIN",
                    help="租约分钟数（默认 {}，超时后可被接管）".format(DEFAULT_CLAIM_LEASE))
    pn.add_argument("--force", action="store_true", help="抢占未到期的认领 / 释放别人的认领")

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
        "audit": cmd_audit, "migrate": cmd_migrate, "entry": cmd_entry,
        "tasks": cmd_tasks, "scan": cmd_scan, "gates": cmd_gates, "next": cmd_next,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
