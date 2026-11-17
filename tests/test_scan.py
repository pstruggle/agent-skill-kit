"""`sop.py scan` 回归测试（TDD：先红后绿）。

覆盖历史真实痛点：init 后 9 个维度全靠手填、默认锚点在真实项目上 0 命中导致
`check --context` 首次即失败（维度 9 候选第 3 条）。

约定与 test_sop.py 一致：全部在临时目录跑，通过 subprocess 调真 CLI。
"""

import hashlib
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
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, str(SOP), *args, "--root", str(root)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env=env, cwd=str(cwd or root),
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ScanTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-scan-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def init(self, *extra):
        cp = run("init", *extra, root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        return cp

    def write(self, rel, text, encoding="utf-8"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding=encoding)
        return p

    def manifest(self):
        return json.loads((self.root / MANIFEST_REL).read_text(encoding="utf-8"))

    def dim_path(self, dim_id):
        for d in self.manifest()["dimensions"]:
            if d["id"] == dim_id:
                return self.root / CONTEXT_REL / "{:02d}-{}.md".format(d["id"], d["key"])
        raise AssertionError("维度 {} 不存在".format(dim_id))

    def dim_text(self, dim_id):
        return self.dim_path(dim_id).read_text(encoding="utf-8")


class TestScanContract(ScanTestBase):
    def test_scan_without_manifest_asks_for_init(self):
        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("init", cp.stdout)

    def test_dry_run_changes_nothing(self):
        self.init("--project", "demo")
        self.write("pyproject.toml", '[project]\nname = "demo"\nrequires-python = ">=3.9"\n')
        self.write("src/app.py", "print('hi')\n")
        before = {
            "dim2": sha(self.dim_path(2)),
            "manifest": sha(self.root / MANIFEST_REL),
            "agents": sha(self.root / AGENTS_REL),
        }
        cp = run("scan", "--dry-run", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("预览", cp.stdout)
        self.assertEqual(before["dim2"], sha(self.dim_path(2)))
        self.assertEqual(before["manifest"], sha(self.root / MANIFEST_REL))
        self.assertEqual(before["agents"], sha(self.root / AGENTS_REL))


class TestScanFillsFacts(ScanTestBase):
    def python_project(self):
        self.init("--project", "demo")
        self.write("README.md", "# demo\n\n一个演示项目。\n")
        self.write("pyproject.toml",
                   '[project]\nname = "demo"\ndependencies = ["fastapi>=0.100"]\n\n'
                   '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        self.write("tests/test_app.py", "def test_ok():\n    assert True\n")
        self.write(".github/workflows/ci.yml", "name: ci\non: [push]\n")
        self.write("Makefile", "test:\n\tpytest -q\n\nlint:\n\truff check .\n")

    def test_fills_core_dimensions_with_detected_facts(self):
        self.python_project()
        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

        dim1 = self.dim_text(1)
        self.assertIn("tests", dim1)
        self.assertIn("README.md", dim1)

        dim2 = self.dim_text(2)
        self.assertIn("Python", dim2)
        self.assertIn("pyproject.toml", dim2)
        self.assertIn("FastAPI", dim2)

        dim4 = self.dim_text(4)
        self.assertIn("README.md", dim4)

        dim5 = self.dim_text(5)
        self.assertIn("tests/", dim5)
        self.assertIn(".github/workflows/ci.yml", dim5)

        dim6 = self.dim_text(6)
        self.assertIn("pyproject.toml", dim6)
        self.assertIn("fastapi", dim6.lower())

        dim8 = self.dim_text(8)
        self.assertIn("Makefile", dim8)
        self.assertIn("test", dim8)

        for dim_id in (1, 2, 4, 5, 6, 8):
            text = self.dim_text(dim_id)
            self.assertNotIn("**摘要**: 待分析", text)
            self.assertIn("自动推导", text)

    def test_identity_table_gets_placeholder_cells_filled(self):
        self.python_project()
        run("scan", root=self.root)
        agents = (self.root / AGENTS_REL).read_text(encoding="utf-8")
        self.assertRegex(agents, r"\|\s*主语言\s*\|\s*Python")
        self.assertRegex(agents, r"\|\s*包管理器\s*\|")
        self.assertNotRegex(agents, r"\|\s*主语言\s*\|\s*待分析\s*\|")

    def test_dead_anchors_are_repaired_so_check_passes(self):
        # 只有 pyproject + 源码：dim4/5/8 默认锚点全部 0 命中，check 必然失败
        self.init("--project", "demo")
        self.write("pyproject.toml", '[project]\nname = "demo"\n')
        self.write("src/app.py", "print('hi')\n")
        cp = run("check", "--context", root=self.root)
        self.assertNotEqual(cp.returncode, 0, "前置条件不成立：check 本应因锚点失效而失败")

        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        cp = run("check", "--context", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def test_empty_project_does_not_crash_or_break_check(self):
        self.init("--project", "empty")
        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("未检测到", cp.stdout)
        cp = run("check", "--context", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)


class TestScanSafety(ScanTestBase):
    def test_preserves_manual_content_until_force(self):
        self.init("--project", "demo")
        p = self.dim_path(1)
        text = p.read_text(encoding="utf-8").replace(
            "## 维度内容\n", "## 维度内容\n\n人工判断：这是单仓单包结构。\n")
        p.write_text(text, encoding="utf-8")
        self.write("README.md", "# demo\n")

        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("人工判断：这是单仓单包结构。", self.dim_text(1))
        self.assertIn("已有手工内容", cp.stdout)

        cp = run("scan", "--force", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertNotIn("人工判断：这是单仓单包结构。", self.dim_text(1))

    def test_only_limits_which_dimensions_change(self):
        self.init("--project", "demo")
        self.write("pyproject.toml", '[project]\nname = "demo"\n')
        self.write("tests/test_a.py", "def test_a():\n    assert True\n")
        run("scan", "--only", "2", root=self.root)
        self.assertIn("pyproject.toml", self.dim_text(2))
        self.assertIn("**摘要**: 待分析", self.dim_text(5))

    def test_second_run_is_idempotent(self):
        self.init("--project", "demo")
        self.write("README.md", "# demo\n")
        self.write("pyproject.toml", '[project]\nname = "demo"\n')
        run("scan", root=self.root)
        before = {i: sha(self.dim_path(i)) for i in (1, 2, 4, 5, 6, 8)}
        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        after = {i: sha(self.dim_path(i)) for i in (1, 2, 4, 5, 6, 8)}
        self.assertEqual(before, after, "二次 scan 不应改写已自动推导的维度")


class TestScanNodeProject(ScanTestBase):
    def test_detects_node_pnpm_and_scripts(self):
        self.init("--project", "web")
        self.write("package.json", json.dumps({
            "name": "web",
            "scripts": {"test": "jest", "build": "vite build"},
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {"vite": "^5.0.0"},
        }, ensure_ascii=False))
        self.write("pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        self.write("__tests__/app.test.js", "test('ok', () => {});\n")

        cp = run("scan", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        dim2 = self.dim_text(2)
        self.assertIn("pnpm", dim2)
        self.assertIn("JavaScript", dim2)
        self.assertIn("React", dim2)
        dim5 = self.dim_text(5)
        self.assertIn("__tests__", dim5)
        dim8 = self.dim_text(8)
        self.assertIn("jest", dim8)
        self.assertIn("build", dim8)


if __name__ == "__main__":
    unittest.main()
