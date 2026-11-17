"""`sop.py next` 回归测试（TDD：先红后绿）。

next 只做约束推导：给定 status + depends，算"现在能做什么、被谁挡着"。
它不排优先级——那是 agent 的语义判断，脚本排了只会误导（见 ai/next-command/plan.md 的适配性分析）。
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


def run(*args, root, cwd=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, str(SOP), *args, "--root", str(root)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=str(cwd or root),
    )


class NextTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-next-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def task(self, tid, status="pending", depends="-", title=None, type_="feature"):
        body = (
            "# Plan: {tid}\n\n| 字段 | 值 |\n|------|-----|\n"
            "| task.id | {tid} |\n| task.title | {title} |\n| task.type | {type_} |\n"
            "| mode | light |\n| context.dimensions | 1 |\n| status | {status} |\n"
            "| parent | - |\n| depends | {depends} |\n| relates | - |\n"
            "| updated | 2026-11-17 |\n"
        ).format(tid=tid, status=status, depends=depends,
                 title=title or "任务 " + tid, type_=type_)
        p = self.root / "ai" / tid / "plan.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def next(self, *extra):
        return run("next", *extra, root=self.root)


class NextTest(NextTestBase):
    def test_ready_and_blocked_split(self):
        self.task("a", "done")
        self.task("b")
        self.task("c", depends="b")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("第 1 波", cp.stdout)
        self.assertIn("`b`", cp.stdout)
        self.assertIn("第 2 波", cp.stdout)
        self.assertIn("`c`", cp.stdout)
        self.assertIn("等 `b`", cp.stdout)  # 阻塞原因要指名道姓

    def test_done_and_dropped_never_ready(self):
        self.task("a", "done")
        self.task("b", "dropped")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertNotIn("`a` [", cp.stdout)
        self.assertNotIn("`b` [", cp.stdout)

    def test_waves_group_parallel_tasks(self):
        self.task("a", "done")
        self.task("b")
        self.task("d")
        self.task("c", depends="b")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        wave1, _, wave2 = cp.stdout.partition("第 2 波")
        self.assertIn("`b`", wave1)
        self.assertIn("`d`", wave1)
        self.assertIn("`c`", wave2)

    def test_doing_listed_before_pending_in_same_wave(self):
        self.task("p")
        self.task("q", "doing")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertLess(cp.stdout.index("`q`"), cp.stdout.index("`p`"))
        self.assertIn("[doing]", cp.stdout)

    def test_dropped_dependency_releases_successor(self):
        self.task("a", "dropped")
        self.task("b", depends="a")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("第 1 波", cp.stdout)
        self.assertIn("前置已放弃", cp.stdout)

    def test_blocked_status_with_satisfied_depends_is_external(self):
        self.task("a", "done")
        self.task("b", "blocked", depends="a")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("外部阻塞", cp.stdout)
        self.assertIn("`b`", cp.stdout)

    def test_cycle_is_reported_not_hung(self):
        self.task("a", depends="b")
        self.task("b", depends="a")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("无法排序", cp.stdout)
        self.assertIn("`a`", cp.stdout)
        self.assertIn("`b`", cp.stdout)

    def test_missing_dependency_is_reported(self):
        self.task("a", depends="ghost")
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("无法排序", cp.stdout)
        self.assertIn("缺失", cp.stdout)

    def test_empty_ledger(self):
        cp = self.next()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("暂无任务", cp.stdout)

    def test_json_output(self):
        self.task("a", "done")
        self.task("b")
        self.task("c", depends="b")
        cp = self.next("--json")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        data = json.loads(cp.stdout.strip().splitlines()[-1])
        self.assertEqual([t["id"] for t in data["ready"]], ["b"])
        self.assertEqual([t["id"] for t in data["blocked"]], ["c"])
        self.assertEqual(data["blocked"][0]["blocking"][0]["id"], "b")
        # waves 给的是完整规划视图：第 1 波现在可做，第 2 波等第 1 波
        self.assertEqual([w["wave"] for w in data["waves"]], [1, 2])
        self.assertEqual(data["waves"][0]["tasks"], ["b"])
        self.assertEqual(data["waves"][1]["tasks"], ["c"])


if __name__ == "__main__":
    unittest.main()
