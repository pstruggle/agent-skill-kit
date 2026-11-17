"""`tasks --json` 机器可读导出（TDD：先红后绿）。

为什么需要：自动化（CI 看板、demo 对比仓库统计、外部脚本）不该去解析 Markdown 表格；
`next --json` 只给前沿，拿不到「全部任务 + 负责人 + 认领租约」的完整快照。
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


def run(*args, root):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("SOP_OWNER", None)
    return subprocess.run(
        [sys.executable, str(SOP), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(root),
    )


class TasksJsonTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-tasks-json-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def task(self, tid, status="pending", depends="-"):
        body = (
            "# Plan: {tid}\n\n| 字段 | 值 |\n|------|-----|\n"
            "| task.id | {tid} |\n| task.title | 任务 {tid} |\n| task.type | feature |\n"
            "| mode | light |\n| status | {status} |\n| parent | - |\n"
            "| depends | {depends} |\n| relates | - |\n| updated | 2026-11-17 |\n"
        ).format(tid=tid, status=status, depends=depends)
        p = self.root / "ai" / tid / "plan.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def json_out(self, *args):
        cp = run("tasks", "--json", *args, root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(len(cp.stdout.strip().splitlines()), 1, "必须是单行 JSON：" + cp.stdout)
        return json.loads(cp.stdout)

    def test_returns_full_task_snapshot(self):
        self.task("a")
        self.task("b", depends="[a]")
        payload = self.json_out()
        self.assertEqual(payload["tasks"], 2)
        self.assertEqual(payload["edges"], 1)
        self.assertEqual(sorted(i["id"] for i in payload["items"]), ["a", "b"])
        for item in payload["items"]:
            for key in ("id", "title", "type", "status", "owner", "claimed",
                        "depends", "relates", "depends_open", "depends_missing",
                        "blocked_by", "updated"):
                self.assertIn(key, item, "缺字段 {}".format(key))

    def test_dependency_detail_is_derived_not_raw(self):
        self.task("a")
        self.task("b", depends="[a]")
        payload = self.json_out()
        b = [i for i in payload["items"] if i["id"] == "b"][0]
        self.assertEqual(b["depends"], ["a"])
        self.assertEqual(b["depends_open"], ["a"], "a 还没 done，b 被挡着")
        self.assertEqual(b["depends_missing"], [])

    def test_reports_missing_dependency(self):
        self.task("b", depends="[ghost]")
        payload = self.json_out()
        b = payload["items"][0]
        self.assertEqual(b["depends_missing"], ["ghost"])

    def test_includes_claim_lease(self):
        self.task("a")
        cp = run("next", "--claim", "a", "--as", "bob@pc", "--lease", "60", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        payload = self.json_out()
        a = payload["items"][0]
        self.assertEqual(a["owner"], "bob@pc")
        self.assertTrue(a["claim"], "有锁就该带认领记录")
        self.assertEqual(a["claim"]["owner"], "bob@pc")
        self.assertEqual(a["claim"]["lease"], 60)
        self.assertGreater(a["claim"]["minutes_left"], 0)

    def test_no_claim_yields_null(self):
        self.task("a")
        payload = self.json_out()
        self.assertIsNone(payload["items"][0]["claim"])

    def test_does_not_write_ledger(self):
        self.task("a")
        self.json_out()
        self.assertFalse((self.root / "ai" / "TASKS.md").exists(),
                         "--json 是只读导出，不应顺手写台账")

    def test_stats_counts_by_status(self):
        self.task("a", "done")
        self.task("b", "doing")
        self.task("c", "pending")
        payload = self.json_out()
        self.assertEqual(payload["stats"], {"done": 1, "doing": 1, "pending": 1})
        self.assertEqual(payload["unfinished"], 2)


if __name__ == "__main__":
    unittest.main()
