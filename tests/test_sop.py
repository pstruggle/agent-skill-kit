"""project-task-sop CLI 回归测试。

目标：用最少的用例拦住历史上真实返工过的两类缺陷，以及跨项目复用时最容易踩的两处
（codeRoot 解析、entry 生成区漂移）。

- 全部用例在临时目录里跑，互不污染，也不碰仓库自己的 ai/。
- 通过 subprocess 调真 CLI（不 import sop.py），保证测的是命令行契约而非内部函数。
- 运行方式（仓库根）：
      python -m unittest discover -s tests -v
      python tests/test_sop.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOP = REPO_ROOT / "skills" / "project-task-sop" / "sop.py"

MANIFEST_REL = Path("ai") / "context.manifest.json"
CONTEXT_REL = Path("ai") / "context"
AGENTS_REL = Path("ai") / "AGENTS.md"


def run(*args, root, cwd=None):
    """调用 sop.py 子命令；--root 统一追加在末尾。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, str(SOP), *args, "--root", str(root)]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(cwd or root),
    )


class SopTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    # --- helpers -----------------------------------------------------------
    def init(self, *extra):
        cp = run("init", *extra, root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        return cp

    def manifest(self):
        return json.loads((self.root / MANIFEST_REL).read_text(encoding="utf-8"))

    def write_manifest(self, mf):
        (self.root / MANIFEST_REL).write_text(
            json.dumps(mf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def dim_path(self, dim_id):
        mf = self.manifest()
        for d in mf["dimensions"]:
            if d["id"] == dim_id:
                return self.root / CONTEXT_REL / "{:02d}-{}.md".format(d["id"], d["key"])
        raise AssertionError("维度 {} 不存在".format(dim_id))


class TestScaffold(SopTestBase):
    def test_init_creates_manifest_dimensions_and_index(self):
        self.init("--project", "demo")
        mf = self.manifest()
        self.assertEqual(len(mf["dimensions"]), 9)
        self.assertIn("_index", mf)
        for d in mf["dimensions"]:
            p = self.root / CONTEXT_REL / "{:02d}-{}.md".format(d["id"], d["key"])
            self.assertTrue(p.is_file(), "缺维度文件: {}".format(p))
            text = p.read_text(encoding="utf-8")
            self.assertIn("**变更锚点**", text)
            self.assertIn("**摘要**", text)
        idx = (self.root / AGENTS_REL).read_text(encoding="utf-8")
        self.assertIn("<!-- SOP-GEN:BEGIN -->", idx)
        self.assertIn("<!-- SOP-GEN:END -->", idx)
        self.assertIn("项目身份速览", idx)  # 手工区存在且未被生成区吞掉

    def test_check_context_passes_with_matching_anchors(self):
        # 默认锚点集需要项目里有对应文件，否则 check 会判「锚点声明失效」——见下一条用例
        (self.root / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
        (self.root / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        (self.root / "README.md").write_text("# demo\n", encoding="utf-8")
        (self.root / "Makefile").write_text("all:\n\t@echo ok\n", encoding="utf-8")
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
        self.init()
        cp = run("check", "--context", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("上下文校验通过", cp.stdout)

    def test_check_context_flags_anchor_with_zero_hits(self):
        # 空目录 init：内置建议集的锚点全部 0 命中 -> 必须报「锚点声明失效」并退出 1，
        # 提醒使用者按本项目实际文件改 manifest 锚点（不能静默通过）
        self.init()
        cp = run("check", "--context", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("锚点声明失效", cp.stdout)

    def test_dims_returns_task_mapping(self):
        self.init()
        cp = run("dims", "feature", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("必读", cp.stdout)
        self.assertIn("9", cp.stdout)  # 项目级教训恒为必读


class TestAnchorLifecycle(SopTestBase):
    def test_refresh_detects_change_then_seals(self):
        self.init()
        cp = run("refresh", root=self.root)
        self.assertEqual(cp.returncode, 0)
        self.assertIn("最新", cp.stdout)

        # 新增一个被锚点覆盖的文件 -> 技术栈/依赖维度必须变「过期」
        (self.root / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
        cp = run("refresh", root=self.root)
        self.assertEqual(cp.returncode, 0)
        self.assertIn("过期", cp.stdout)

        before = self.manifest()["_index"]["version"]
        cp = run("refresh", "--mark-fresh", root=self.root)
        self.assertEqual(cp.returncode, 0)
        after = self.manifest()["_index"]["version"]
        self.assertEqual(after, before + 1)

        cp = run("refresh", root=self.root)
        self.assertIn("最新", cp.stdout)
        self.assertNotIn("过期", cp.stdout)

    def test_mark_fresh_only_seals_named_dimension(self):
        self.init()
        (self.root / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
        cp = run("refresh", "--mark-fresh", "--only", "2", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("已封存", cp.stdout)

        cp = run("refresh", root=self.root)
        self.assertIn("过期", cp.stdout)  # 未封存的维度仍应报过期

    def test_tree_anchor_catches_new_top_level_dir(self):
        self.init()
        cp = run("refresh", root=self.root)
        self.assertIn("最新", cp.stdout)
        (self.root / "newdir").mkdir()
        cp = run("refresh", root=self.root)
        self.assertIn("过期", cp.stdout)


class TestCodeRoot(SopTestBase):
    """ai/ 与代码目录分离时的锚点解析；历史缺陷：codeRoot 未 resolve 会静默产出空锚点。"""

    def _make_split_layout(self, code_root_value):
        self.init()
        code = self.root / "code"
        (code / "sub").mkdir(parents=True)
        (code / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        mf = self.manifest()
        mf["codeRoot"] = code_root_value
        self.write_manifest(mf)
        cp = run("refresh", "--mark-fresh", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        return self.dim_path(2).read_text(encoding="utf-8")

    def test_absolute_code_root_anchors_project_files(self):
        text = self._make_split_layout(str(self.root / "code"))
        self.assertIn("pyproject.toml", text)

    def test_relative_code_root_resolves_against_cwd(self):
        # 相对 codeRoot 以进程 CWD 解析：从 root 启动才正确，这是当前实现的既定行为
        text = self._make_split_layout("code")
        self.assertIn("pyproject.toml", text)


class TestAudit(SopTestBase):
    def test_audit_clean_on_fresh_init(self):
        self.init()
        cp = run("audit", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("未发现维度归属可疑点", cp.stdout)

    def test_audit_flags_env_facts_outside_legal_dimension(self):
        self.init()
        # 环境事实（盘符路径）写进「技术栈」维度 -> 应被判为归属可疑
        p = self.dim_path(2)
        p.write_text(p.read_text(encoding="utf-8")
                     + "\n- 解释器：D:\\venv\\python.exe\n", encoding="utf-8")
        cp = run("audit", root=self.root)
        self.assertIn("可疑", cp.stdout)
        self.assertIn("技术栈识别", cp.stdout)


class TestEntry(SopTestBase):
    def _enable_entry(self, title="仓库入口（AGENTS.md）"):
        mf = self.manifest()
        mf["entry"] = {"file": "AGENTS.md", "maxLines": 45, "title": title}
        for d in mf["dimensions"]:
            if d["key"] == "dev-workflow":
                d["entryHint"] = "本机工具链版本可能与构建所需不一致，先核对再动手"
        self.write_manifest(mf)
        return self.root / "AGENTS.md"

    def test_entry_generates_then_check_passes(self):
        self.init()
        target = self._enable_entry()
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = target.read_text(encoding="utf-8")
        self.assertIn("<!-- ENTRY-GEN:BEGIN -->", text)
        self.assertIn("<!-- ENTRY-MANUAL:BEGIN -->", text)
        self.assertIn("ai/context", text)

        cp = run("entry", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("入口文件校验通过", cp.stdout)

    def test_entry_check_flags_stale_generated_section(self):
        self.init()
        self._enable_entry()
        run("entry", root=self.root)
        mf = self.manifest()
        for d in mf["dimensions"]:
            if d["key"] == "dev-workflow":
                d["entryHint"] = "改过的纪律摘要"
        self.write_manifest(mf)
        cp = run("entry", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("不同步", cp.stdout)

    def test_entry_lint_blocks_home_path_and_status_words(self):
        self.init()
        target = self._enable_entry()
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0)
        text = target.read_text(encoding="utf-8")
        text = text.replace("<!-- ENTRY-MANUAL:END -->",
                            "C:\\Users\\someone\\bin\n最近变更：修了锚点\n<!-- ENTRY-MANUAL:END -->")
        target.write_text(text, encoding="utf-8")
        cp = run("entry", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("家目录", cp.stdout)
        self.assertIn("时效性", cp.stdout)

    def test_entry_refuses_existing_file_without_markers(self):
        self.init()
        target = self._enable_entry()
        target.write_text("# 手工写的入口\n", encoding="utf-8")
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("缺少 ENTRY-GEN 标记", cp.stdout)
        # 拒绝失败时不允许改动原内容
        self.assertEqual(target.read_text(encoding="utf-8"), "# 手工写的入口\n")

    def test_entry_onboarding_row_covers_all_dimensions(self):
        # 回归：entry 的「按任务读维度」表曾漏掉 onboarding 特例，导致入口表露 1、9，
        # 而 ai/AGENTS.md 同源生成的是 1–9（同一 manifest 产出两份互相矛盾的路由）。
        self.init()
        self._enable_entry()
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        entry_text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        row = [ln for ln in entry_text.splitlines() if ln.startswith("| onboarding |")][0]
        ids = [d["id"] for d in self.manifest()["dimensions"]]
        for d in self.manifest()["dimensions"]:
            self.assertIn("{} {}".format(d["id"], d["key"]), row)

        idx = (self.root / AGENTS_REL).read_text(encoding="utf-8")
        idx_row = [ln for ln in idx.splitlines() if ln.startswith("| onboarding |")][0]
        for i in ids:
            self.assertIn(str(i), idx_row)

    def test_entry_disabled_manifest_is_noop(self):
        self.init()
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("未启用 entry", cp.stdout)


class TestExperiments(SopTestBase):
    def test_record_dedup_render_check(self):
        self.init()
        (self.root / "ai" / "exp").mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            cp = run("record", "exp", "-m", "fit", "-p", "lr=1e-4", "-p", "bs=8",
                     "-r", "acc=0.8", "--status", "success", root=self.root)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

        cp = run("dedup", "exp", "-m", "fit", "-p", "lr=1e-4", "-p", "bs=8", root=self.root)
        self.assertEqual(cp.returncode, 0)
        self.assertIn("重复", cp.stdout)

        cp = run("dedup", "exp", "-m", "fit", "-p", "lr=9e-9", root=self.root)
        self.assertIn("无重复", cp.stdout)

        cp = run("check", "exp", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

        out = self.root / "ai" / "exp" / "report.md"
        cp = run("render", "exp", "--out", str(out), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue(out.is_file())

    def test_check_detects_tampered_hash(self):
        self.init()
        task = self.root / "ai" / "exp"
        run("record", "exp", "-m", "fit", "-p", "lr=1e-4", root=self.root)
        records = (task / ".experiments.jsonl").read_text(encoding="utf-8")
        rec = json.loads(records.splitlines()[0])
        rec["hash"] = "deadbeef" * 8
        (task / ".experiments.jsonl").write_text(
            json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        cp = run("check", "exp", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("哈希不匹配", cp.stdout)


class TestLessons(SopTestBase):
    def test_lessons_keyword_search(self):
        self.init()
        task = self.root / "ai" / "exp"
        task.mkdir(parents=True, exist_ok=True)
        (task / "NOTES.md").write_text(
            "# NOTES\n\n## 教训: 锚点静默截断\n\n- 相对 codeRoot 会静默产出空锚点\n",
            encoding="utf-8")
        cp = run("lessons", "exp", "锚点", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("锚点静默截断", cp.stdout)

        cp = run("lessons", "exp", "不存在的关键词", root=self.root)
        self.assertNotIn("锚点静默截断", cp.stdout)

    def test_lessons_on_missing_task_reports_error(self):
        self.init()
        cp = run("lessons", "nope", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)


class TestTasks(SopTestBase):
    """任务台账：ai/<task>/plan.md 字段表 -> ai/TASKS.md。

    历史缺口：任务之间完全没有关联，完成状态只靠 AGENTS.md 手工区手抄，
    导致 sop-init -> tests-entry 这类真实因果在上下文里无迹可寻。
    """

    def _plan(self, tid, status="doing", depends="-", relates="-", title=None,
              parent="-"):
        d = self.root / "ai" / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.md").write_text(
            f"# Plan: {tid}\n\n"
            "| 字段 | 值 |\n"
            "|------|-----|\n"
            f"| task.id | {tid} |\n"
            f"| task.title | {title or tid + ' 目标'} |\n"
            "| task.type | feature |\n"
            "| mode | standard |\n"
            f"| status | {status} |\n"
            f"| parent | {parent} |\n"
            f"| depends | {depends} |\n"
            f"| relates | {relates} |\n"
            "| updated | 2026-11-16 |\n"
            "\n## objective\n\n演示\n",
            encoding="utf-8")
        return d / "plan.md"

    def test_tasks_renders_status_and_relations(self):
        self.init()
        self._plan("base", status="done")
        self._plan("next", status="doing", depends="[base]", relates="base")
        cp = run("tasks", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        ledger = (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("禁止手改", ledger)
        self.assertIn("| 任务数 | 2 |", ledger)
        self.assertIn("| `next` | doing |", ledger)
        self.assertIn("base", ledger)
        # 被依赖列必须由 depends 反向算出
        row = [ln for ln in ledger.splitlines() if ln.startswith("| `base` |")][0]
        self.assertIn("next", row)
        # 关联边同时含 depends 与 relates
        self.assertIn("| depends | `next` | `base` |", ledger)
        self.assertIn("| relates | `next` | `base` |", ledger)

    def test_tasks_check_passes_when_in_sync(self):
        self.init()
        self._plan("a", status="done")
        self._plan("b", status="doing", depends="[a]")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("任务台账校验通过", cp.stdout)

    def test_tasks_check_flags_drift(self):
        self.init()
        p = self._plan("a", status="doing")
        run("tasks", root=self.root)
        p.write_text(p.read_text(encoding="utf-8").replace("| status | doing |", "| status | done |"),
                     encoding="utf-8")
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("不同步", cp.stdout)

    def test_tasks_check_flags_unknown_reference(self):
        self.init()
        self._plan("a", status="doing", depends="[ghost]")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("指向未知任务", cp.stdout)
        self.assertIn("ghost", cp.stdout)

    def test_tasks_check_flags_self_reference(self):
        self.init()
        self._plan("a", status="doing", relates="a")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("指向自己", cp.stdout)

    def test_tasks_check_flags_depends_cycle(self):
        self.init()
        self._plan("a", status="doing", depends="[b]")
        self._plan("b", status="doing", depends="[a]")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("成环", cp.stdout)

    def test_tasks_check_flags_invalid_status(self):
        self.init()
        self._plan("a", status="瞎写")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertIn("status 非法", cp.stdout)

    def test_tasks_warns_done_with_unfinished_dependency(self):
        self.init()
        self._plan("a", status="doing")
        self._plan("b", status="done", depends="[a]")
        cp = run("tasks", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("仍是 doing", cp.stdout)  # 告警但不阻塞

    def test_tasks_render_is_idempotent(self):
        self.init()
        self._plan("a", status="done")
        run("tasks", root=self.root)
        first = (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8")
        run("tasks", root=self.root)
        self.assertEqual((self.root / "ai" / "TASKS.md").read_text(encoding="utf-8"), first)

    def test_tasks_collapses_bidirectional_relates(self):
        # relates 是无向关系：双方都声明时台账只应出现一条边，否则边数虚高
        self.init()
        self._plan("a", status="done", relates="b")
        self._plan("b", status="done", relates="a")
        cp = run("tasks", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        ledger = (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8")
        self.assertEqual(ledger.count("| relates |"), 1, ledger)

    def test_refresh_mark_fresh_syncs_ledger(self):
        # 台账不能靠人记得重跑：封存时顺带重新聚合
        self.init()
        p = self._plan("a", status="doing")
        run("tasks", root=self.root)
        p.write_text(p.read_text(encoding="utf-8").replace("| status | doing |", "| status | done |"),
                     encoding="utf-8")
        cp = run("refresh", "--mark-fresh", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("任务台账已同步", cp.stdout)
        self.assertIn("| `a` | done |", (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8"))


class TestBackfill(SopTestBase):
    """存量项目接入台账：老 plan.md 没有台账字段，需要「先预览、再写入、后可校验」。"""

    def _legacy(self, tid, notes=None, with_table=True):
        d = self.root / "ai" / tid
        d.mkdir(parents=True, exist_ok=True)
        body = "# Plan: {}\n\n".format(tid)
        if with_table:
            body += ("| 字段 | 值 |\n|------|-----|\n"
                     "| task.id | {} |\n| task.title | {} 旧任务 |\n| task.type | feature |\n"
                     "| mode | light |\n").format(tid, tid)
        body += "\n## objective\n\n历史遗留，没有任何台账字段\n"
        (d / "plan.md").write_text(body, encoding="utf-8")
        if notes:
            (d / "NOTES.md").write_text(notes, encoding="utf-8")
        return d / "plan.md"

    def test_dry_run_reports_without_writing(self):
        self.init()
        plan = self._legacy("legacy-a")
        before = plan.read_text(encoding="utf-8")
        cp = run("tasks", "--backfill", "--dry-run", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("dry-run", cp.stdout)
        self.assertIn("legacy-a", cp.stdout)
        self.assertEqual(plan.read_text(encoding="utf-8"), before, "dry-run 不允许落盘")

    def test_backfill_writes_fields_then_ledger_lists_task(self):
        self.init()
        self._legacy("legacy-a")
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = (self.root / "ai" / "legacy-a" / "plan.md").read_text(encoding="utf-8")
        for key in ("| status |", "| parent |", "| depends |", "| relates |", "| updated |"):
            self.assertIn(key, text)

        run("tasks", root=self.root)
        ledger = (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("`legacy-a`", ledger)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def test_backfill_infers_done_from_notes(self):
        self.init()
        self._legacy("legacy-done", notes="# NOTES\n\n## 验收\n\n- 验收通过：24 用例全绿\n")
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = (self.root / "ai" / "legacy-done" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("| status | done |", text)
        self.assertIn("NOTES 有验收", cp.stdout)
        self.assertNotIn("需人工确认状态", cp.stdout)

    def test_backfill_marks_status_for_human_confirmation(self):
        self.init()
        self._legacy("legacy-unknown")
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("需人工确认状态", cp.stdout)
        self.assertIn("legacy-unknown", cp.stdout)
        # 缺省 pending，不得凭空推断成 done
        text = (self.root / "ai" / "legacy-unknown" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("| status | pending |", text)

    def test_backfill_is_idempotent(self):
        self.init()
        self._legacy("legacy-a")
        run("tasks", "--backfill", root=self.root)
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("已齐全", cp.stdout)
        text = (self.root / "ai" / "legacy-a" / "plan.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("| status |"), 1, "不得重复补同一字段")

    def test_backfill_skips_plan_without_field_table(self):
        self.init()
        self._legacy("no-table", with_table=False)
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("未找到字段表", cp.stdout)

    def test_backfill_suggests_relations_without_writing(self):
        self.init()
        self._legacy("legacy-a", notes="# NOTES\n\n后续见 legacy-b 的处理方式\n")
        self._legacy("legacy-b")
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("建议关系", cp.stdout)
        self.assertIn("legacy-a → legacy-b", cp.stdout)
        # 建议不得自动写进 plan.md
        a = (self.root / "ai" / "legacy-a" / "plan.md").read_text(encoding="utf-8")
        self.assertIn("| relates | - |", a)


class TestEntryOutsideRepo(SopTestBase):
    """入口放在 ai 同级（仓库外）：ai/ 不进版本库时，仓根入口对 clone 者必然断链。"""

    def _split(self, entry_file):
        self.init()
        code = self.root / "code"
        code.mkdir()
        mf = self.manifest()
        mf["codeRoot"] = str(code)
        mf["entry"] = {"file": entry_file, "maxLines": 45, "title": "工作目录入口"}
        self.write_manifest(mf)
        return code

    def test_entry_file_dotdot_lands_beside_ai(self):
        code = self._split("../AGENTS.md")
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue((self.root / "AGENTS.md").is_file(), "入口应落在 ai 同级")
        self.assertFalse((code / "AGENTS.md").exists(), "不应落进 codeRoot")

        cp = run("entry", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("入口文件校验通过", cp.stdout)

    def test_entry_prints_location_preflight(self):
        # 落点由人决定：工具必须先把「落在哪」和「仓库内/外」说清楚，不能默认落仓根
        self._split("../AGENTS.md")
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("entry 落点", cp.stdout)
        self.assertIn("仓库外", cp.stdout)

    def test_entry_warns_when_split_layout_lands_in_repo(self):
        # ai/ 在仓库外 + 落点仍在仓库内 -> 必须提示 clone 者看不到 ai/context
        self._split("AGENTS.md")
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("entry 落点", cp.stdout)
        self.assertIn("仓库内", cp.stdout)
        self.assertIn("clone 者不可见", cp.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
