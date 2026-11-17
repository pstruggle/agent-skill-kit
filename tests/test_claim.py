"""认领（next --claim / --release）回归测试（TDD：先红后绿）。

为什么不是"改一下 status 就算认领"：
- 单人也会多会话并发，两个会话都写 `doing` 无法分辨是谁、是否还活着；
- 有些项目的 `ai/` 是进版本库的，owner 必须能随之同步；
- 所以认领要有 owner + 时间 + 租约（.claim 锁，O_EXCL 原子创建）+ 超时接管 + 释放。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOP = REPO_ROOT / "skills" / "project-task-sop" / "sop.py"


def run(*args, root, cwd=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("SOP_OWNER", None)
    cmd = [sys.executable, str(SOP), *args, "--root", str(root)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=str(cwd or root),
    )


class ClaimTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-claim-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def plan_path(self, tid):
        return self.root / "ai" / tid / "plan.md"

    def lock_path(self, tid):
        return self.root / "ai" / tid / ".claim"

    def task(self, tid, status="pending", depends="-", title=None):
        body = (
            "# Plan: {tid}\n\n| 字段 | 值 |\n|------|-----|\n"
            "| task.id | {tid} |\n| task.title | {title} |\n| task.type | feature |\n"
            "| mode | light |\n| context.dimensions | 1 |\n| status | {status} |\n"
            "| parent | - |\n| depends | {depends} |\n| relates | - |\n"
            "| updated | 2026-11-17 |\n"
        ).format(tid=tid, status=status, depends=depends, title=title or "任务 " + tid)
        p = self.plan_path(tid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def field(self, tid, key):
        for line in self.plan_path(tid).read_text(encoding="utf-8").splitlines():
            if line.startswith("| {} |".format(key)):
                return line.strip().strip("|").split("|")[1].strip()
        return None

    def write_lock(self, tid, owner="alice@pc", minutes_ago=0, lease=120):
        claimed = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        self.lock_path(tid).parent.mkdir(parents=True, exist_ok=True)
        self.lock_path(tid).write_text(json.dumps({
            "task": tid, "owner": owner, "claimed": claimed, "lease": lease,
        }, ensure_ascii=False), encoding="utf-8")


class ClaimTest(ClaimTestBase):
    def test_claim_ready_task_records_owner_and_timestamp(self):
        self.task("a")
        cp = run("next", "--claim", "a", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.field("a", "status"), "doing")
        owner = self.field("a", "owner")
        self.assertTrue(owner and owner != "-", "owner 必须落盘，否则没人知道是谁认领的")
        self.assertRegex(self.field("a", "claimed"), r"^\d{4}-\d{2}-\d{2}T")
        self.assertTrue(self.lock_path("a").is_file(), "认领必须留锁，供并发会话检测")
        self.assertIn("认领", cp.stdout)

    def test_claim_uses_explicit_name(self):
        self.task("a")
        cp = run("next", "--claim", "a", "--as", "bob@laptop", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.field("a", "owner"), "bob@laptop")
        self.assertEqual(json.loads(self.lock_path("a").read_text(encoding="utf-8"))["owner"], "bob@laptop")

    def test_live_claim_blocks_second_session(self):
        self.task("a")
        self.write_lock("a", owner="alice@pc")
        cp = run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("alice@pc", cp.stdout)
        self.assertIn("--force", cp.stdout)

    def test_force_takes_over_live_claim(self):
        self.task("a")
        self.write_lock("a", owner="alice@pc")
        cp = run("next", "--claim", "a", "--as", "bob@pc", "--force", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.field("a", "owner"), "bob@pc")
        self.assertEqual(json.loads(self.lock_path("a").read_text(encoding="utf-8"))["owner"], "bob@pc")

    def test_expired_claim_is_auto_taken_over(self):
        self.task("a")
        self.write_lock("a", owner="alice@pc", minutes_ago=200, lease=120)
        cp = run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(self.field("a", "owner"), "bob@pc")
        self.assertIn("接管", cp.stdout)

    def test_claim_refuses_task_with_unfinished_dependency(self):
        self.task("a")
        self.task("b", depends="a")
        cp = run("next", "--claim", "b", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("未完成", cp.stdout)

    def test_claim_refuses_done_task(self):
        self.task("a", "done")
        cp = run("next", "--claim", "a", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("done", cp.stdout)

    def test_release_clears_owner_and_restores_pending(self):
        self.task("a")
        run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        cp = run("next", "--release", "a", "--as", "bob@pc", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertFalse(self.lock_path("a").exists())
        self.assertEqual(self.field("a", "status"), "pending")
        self.assertEqual(self.field("a", "owner"), "-")

    def test_release_by_stranger_is_refused(self):
        self.task("a")
        run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        cp = run("next", "--release", "a", "--as", "eve@pc", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("bob@pc", cp.stdout)

    def test_tasks_check_warns_expired_claim(self):
        self.task("a", "doing")
        self.write_lock("a", owner="alice@pc", minutes_ago=300, lease=120)
        run("tasks", root=self.root)  # 先落台账，否则 --check 会先报「台账不存在」
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, "超时是告警不是错误：" + cp.stdout + cp.stderr)
        self.assertIn("超时", cp.stdout)
        self.assertIn("alice@pc", cp.stdout)

    def test_release_keeps_owner_as_record_when_task_done(self):
        self.task("a")
        run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        self.plan_path("a").write_text(
            self.plan_path("a").read_text(encoding="utf-8").replace(
                "| status | doing |", "| status | done |"), encoding="utf-8")
        cp = run("next", "--release", "a", "--as", "bob@pc", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertFalse(self.lock_path("a").exists())
        self.assertEqual(self.field("a", "status"), "done")
        self.assertEqual(self.field("a", "owner"), "bob@pc", "已完成任务释放后 owner 应留作记录")

    def test_tasks_check_does_not_warn_owner_on_done_task(self):
        self.task("a", "done")
        lines = self.plan_path("a").read_text(encoding="utf-8").replace(
            "| updated | 2026-11-17 |", "| owner | bob@pc |\n| updated | 2026-11-17 |")
        self.plan_path("a").write_text(lines, encoding="utf-8")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertNotIn("缺", cp.stdout, "已完成任务的 owner 是记录，不是缺失的锁")

    def test_tasks_check_warns_owner_without_lock(self):
        self.task("a", "doing")
        lines = self.plan_path("a").read_text(encoding="utf-8").replace(
            "| updated | 2026-11-17 |", "| owner | bob@pc |\n| updated | 2026-11-17 |")
        self.plan_path("a").write_text(lines, encoding="utf-8")
        run("tasks", root=self.root)
        cp = run("tasks", "--check", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("缺", cp.stdout)

    def test_next_shows_claim_owner_and_remaining(self):
        self.task("a")
        run("next", "--claim", "a", "--as", "bob@pc", "--lease", "60", root=self.root)
        cp = run("next", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("bob@pc", cp.stdout)
        self.assertIn("认领", cp.stdout)

    def test_ledger_has_owner_column(self):
        self.task("a")
        run("next", "--claim", "a", "--as", "bob@pc", root=self.root)
        ledger = (self.root / "ai" / "TASKS.md").read_text(encoding="utf-8")
        self.assertIn("负责人", ledger)
        self.assertIn("bob@pc", ledger)

    def test_backfill_adds_owner_fields(self):
        self.task("a")
        plan = self.plan_path("a")
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "| updated | 2026-11-17 |", ""), encoding="utf-8")
        cp = run("tasks", "--backfill", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("owner", cp.stdout)


if __name__ == "__main__":
    unittest.main()
