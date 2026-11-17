"""`sop.py gates` 回归测试（TDD：先红后绿）。

为什么要有 gates：四道门（check --context / refresh 过期 / audit / entry --check / tasks --check）
平时靠人记得跑，而"记得"是最不可靠的一环——报告 P1-1 指出的正是"无 hook 强制力"。
gates 把这五个检查合成一次调用、一个退出码，才能被 pre-commit / Claude Code hook 直接引用。

约定与其余用例一致：全部在临时目录跑，通过 subprocess 调真 CLI，不碰使用者自己的 ai/。
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


class GatesTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-gates-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def manifest(self):
        return json.loads((self.root / "ai" / "context.manifest.json").read_text(encoding="utf-8"))

    def save_manifest(self, mf):
        self.write("ai/context.manifest.json", json.dumps(mf, ensure_ascii=False, indent=2))

    def dim_path(self, dim_id):
        for d in self.manifest()["dimensions"]:
            if d["id"] == dim_id:
                return self.root / "ai" / "context" / "{:02d}-{}.md".format(d["id"], d["key"])
        raise AssertionError("维度 {} 不存在".format(dim_id))

    def fresh_project(self):
        """init → scan（修 0 命中锚点）→ refresh --mark-fresh：此时五道门应全过。"""
        cp = run("init", "--project", "demo", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.write("README.md", "# demo\n\n一个普通项目。\n")
        self.write("app.py", "print('hi')\n")
        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        cp = run("refresh", "--mark-fresh", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)


class GatesTest(GatesTestBase):
    def test_gates_pass_on_clean_project(self):
        self.fresh_project()
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        for name in ("context", "dims", "audit", "tasks"):
            self.assertIn(name, cp.stdout, "缺少 gate 名称：{}\n{}".format(name, cp.stdout))
        self.assertIn("通过", cp.stdout)

    def test_entry_gate_reports_skip_when_not_configured(self):
        """默认 manifest 的 entry.file 为空：entry 门应报「跳过」而不是通过，更不能算失败。"""
        self.fresh_project()
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("entry", cp.stdout)
        self.assertIn("跳过", cp.stdout)

    def test_gates_fail_when_dimension_stale(self):
        self.fresh_project()
        with (self.root / "README.md").open("a", encoding="utf-8") as fh:
            fh.write("\n新增一行会动到文档维度的锚点。\n")
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("过期", cp.stdout)
        self.assertIn("dims", cp.stdout)

    def test_gates_fail_when_tasks_out_of_sync(self):
        self.fresh_project()
        self.write("ai/extra/plan.md",
                   "# Plan: extra\n\n| 字段 | 值 |\n|------|-----|\n"
                   "| task.id | extra |\n| task.title | 临时任务 |\n| task.type | feature |\n"
                   "| mode | light |\n| context.dimensions | 1 |\n| status | pending |\n"
                   "| parent | - |\n| depends | - |\n| relates | - |\n| updated | 2026-11-17 |\n")
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("tasks", cp.stdout)

    def test_gates_fail_when_audit_finds_env_fact(self):
        """环境事实写进非法维度（默认 attribution 的 env-facts 合法集不含 git-history）→ audit 门失败。"""
        self.fresh_project()
        dim = self.dim_path(3)
        dim.write_text(dim.read_text(encoding="utf-8")
                       + "\n测试环境服务跑在 192.168.10.7:8080 上。\n", encoding="utf-8")
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("audit", cp.stdout)
        self.assertIn("可疑点", cp.stdout)

    def test_gates_fail_when_entry_drift(self):
        """启用 entry 后，入口生成区被手改 → entry 门失败。"""
        self.fresh_project()
        mf = self.manifest()
        mf["entry"] = {"file": "../AGENTS.md", "maxLines": 45, "title": "演示入口"}
        self.save_manifest(mf)
        cp = run("entry", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        entry = self.root.parent / "AGENTS.md"
        self.assertTrue(entry.is_file(), "入口应生成在 ai/ 同级：{}".format(entry))
        entry.write_text(entry.read_text(encoding="utf-8").replace("纪律索引", "手改过的标题"),
                         encoding="utf-8")
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("entry", cp.stdout)

    def test_gates_without_manifest_asks_for_init(self):
        cp = run("gates", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("init", cp.stdout)

    def test_skip_if_missing_makes_hook_safe(self):
        """hook 里跑 gates 时项目可能还没接 SOP：--skip-if-missing 必须安静放行（退出 0）。"""
        cp = run("gates", "--skip-if-missing", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def test_json_output_is_machine_readable(self):
        self.fresh_project()
        with (self.root / "README.md").open("a", encoding="utf-8") as fh:
            fh.write("\n再动一次锚点。\n")
        cp = run("gates", "--json", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        data = json.loads(cp.stdout.strip().splitlines()[-1])
        self.assertFalse(data["ok"])
        names = [g["name"] for g in data["gates"]]
        self.assertEqual(sorted(names), sorted(["context", "dims", "audit", "entry", "tasks"]))
        failed = [g["name"] for g in data["gates"] if g["status"] == "fail"]
        self.assertIn("dims", failed)


if __name__ == "__main__":
    unittest.main()
