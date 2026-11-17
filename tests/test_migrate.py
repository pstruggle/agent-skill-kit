"""v3 → v4 `migrate` 回归测试。

说明：`migrate` 是唯一会**改写用户既有 `ai/`** 的子命令（生成 manifest、给维度文件补摘要行、
把旧 AGENTS.md 拆成"生成区 + 手工区"），此前零测试。这里有两条前置保护必须钉住：
① 已有 manifest 时默认不覆盖；② `--dry-run` 绝不写盘。
其余用例是行为契约（旧常量怎么映射、旧索引摘要怎么注入、手工区怎么保留）。
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

V3_SOP = '''
DIMENSIONS = [
    (1, "topology", "目录拓扑与关键文件"),
    (2, "tech-stack", "技术栈识别"),
]
ANCHOR_RULES = [("file", "package.json", [2])]
TASK_TYPE_DIMS = {"feature": ([1, 2], [])}
RELATED_DIMS = {1: [(2, "相关")]}
'''

V3_INDEX = """# Project Context: demo

| 项 | 值 |
|---|---|
| **版本** | 7 |
| **完整刷新** | 2026-01-01 |

## 版本元数据

- 生成于 2026-01-01

## 维度文件索引

| # | 维度 | 文件 | 摘要 |
|---|---|---|---|
| 1 | 拓扑 | `context/01-topology.md` | 目录与关键文件 |
| 2 | 技术栈 | `context/02-tech-stack.md` | Python 标准库 |

## 任务记录

- 2026-01-01 初始化上下文
"""


def run(*args, root):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(SOP), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(root),
    )


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sop-migrate-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ai = self.root / "ai"
        self.manifest = self.ai / "context.manifest.json"
        self.old_sop = self.root / "old_sop.py"
        self.dims_json = self.root / "dims.json"

    def write_v3_sop(self, text=V3_SOP):
        self.old_sop.write_text(text, encoding="utf-8")
        return self.old_sop

    def write_v3_ai(self):
        self.ai.mkdir(parents=True, exist_ok=True)
        (self.ai / "AGENTS.md").write_text(V3_INDEX, encoding="utf-8")
        ctx = self.ai / "context"
        ctx.mkdir(parents=True, exist_ok=True)
        (ctx / "01-topology.md").write_text("# Dimension 1: 目录拓扑\n\n内容\n", encoding="utf-8")
        (ctx / "02-tech-stack.md").write_text("# Dimension 2: 技术栈\n\n内容\n", encoding="utf-8")

    # --- 前置保护 ---------------------------------------------------------

    def test_refuses_to_overwrite_existing_manifest_without_force(self):
        self.ai.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text("{}", encoding="utf-8")
        self.write_v3_sop()
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("已存在", cp.stdout)
        self.assertEqual(self.manifest.read_text(encoding="utf-8"), "{}",
                         "没有 --force 时必须原样保留既有 manifest")

    def test_force_overwrites_existing_manifest(self):
        self.ai.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text("{}", encoding="utf-8")
        self.write_v3_sop()
        cp = run("migrate", "--old-sop", str(self.old_sop), "--force", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("dimensions", json.loads(self.manifest.read_text(encoding="utf-8")))

    def test_dry_run_writes_nothing(self):
        self.write_v3_sop()
        self.write_v3_ai()
        cp = run("migrate", "--old-sop", str(self.old_sop), "--dry-run", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("--dry-run", cp.stdout)
        self.assertFalse(self.manifest.exists(), "--dry-run 绝不能写盘")
        self.assertIn("AGENTS.md 分区改造", cp.stdout)

    # --- 旧常量解析 -------------------------------------------------------

    def test_migrates_from_v3_constants(self):
        self.write_v3_sop()
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        mf = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual([d["id"] for d in mf["dimensions"]], [1, 2])
        self.assertEqual(mf["dimensions"][1]["anchors"], ["**/package.json"])
        self.assertEqual(mf["dimensions"][0]["anchors"], ["@tree"])
        self.assertIn("must", mf["dimensions"][0]["tasks"])
        self.assertEqual(Path(mf["codeRoot"]).resolve(), self.root.resolve())
        self.assertIn("_index", mf)
        self.assertEqual(mf["_index"]["version"], 1, "没有旧索引时版本从 1 起")
        declared = {d["key"] for d in mf["dimensions"]}
        for info_type, keys in mf["attribution"].items():
            self.assertTrue(set(keys) <= declared,
                            "attribution.{} 不能引用未声明的维度：{}".format(info_type, keys))

    def test_reports_unparsable_old_sop(self):
        self.write_v3_sop("X = 1\n")
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("未解析到 DIMENSIONS", cp.stdout)
        self.assertIn("--dims-json", cp.stdout, "必须给出替代路径，而不是只说失败")

    def test_dims_json_fallback(self):
        self.dims_json.write_text(json.dumps([
            {"id": 1, "key": "topology", "title": "拓扑"},
            {"id": 2, "key": "tech-stack", "title": "技术栈"},
            {"id": 3, "key": "git-history", "title": "Git 历史"},
        ], ensure_ascii=False), encoding="utf-8")
        cp = run("migrate", "--dims-json", str(self.dims_json), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        mf = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(mf["dimensions"]), 3)
        self.assertEqual(Path(mf["codeRoot"]).resolve(), self.root.resolve())

    def test_bad_dims_json_is_reported(self):
        self.dims_json.write_text("{not json", encoding="utf-8")
        cp = run("migrate", "--dims-json", str(self.dims_json), root=self.root)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("读取失败", cp.stdout)

    def test_code_root_is_respected(self):
        self.write_v3_sop()
        cp = run("migrate", "--old-sop", str(self.old_sop), "--code-root", "E:/repo", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(json.loads(self.manifest.read_text(encoding="utf-8"))["codeRoot"], "E:/repo")

    # --- 旧索引：摘要注入与手工区保留 -------------------------------------

    def test_summaries_from_v3_index_are_injected(self):
        self.write_v3_sop()
        self.write_v3_ai()
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        ctx = (self.ai / "context" / "01-topology.md").read_text(encoding="utf-8")
        self.assertIn("> **摘要**: 目录与关键文件", ctx)
        tech = (self.ai / "context" / "02-tech-stack.md").read_text(encoding="utf-8")
        self.assertIn("> **摘要**: Python 标准库", tech)

    def test_agents_index_keeps_manual_sections_and_drops_generated_ones(self):
        self.write_v3_sop()
        self.write_v3_ai()
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        text = (self.ai / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# Project Context: demo", text, "旧项目名必须保留")
        self.assertIn("<!-- SOP-GEN:BEGIN -->", text)
        self.assertIn("<!-- SOP-GEN:END -->", text)
        self.assertIn("## 任务记录", text, "手工区不能丢")
        self.assertNotIn("## 版本元数据", text, "生成区段应由 refresh 重写，迁移时就该剥掉")
        self.assertNotIn("## 维度文件索引", text)

    def test_unregistered_dimension_file_is_warned(self):
        self.write_v3_sop()
        self.write_v3_ai()
        (self.ai / "context" / "03-extra.md").write_text("# Dimension 3\n", encoding="utf-8")
        idx = self.ai / "AGENTS.md"
        idx.write_text(
            V3_INDEX.replace("| 2 | 技术栈 | `context/02-tech-stack.md` | Python 标准库 |",
                             "| 2 | 技术栈 | `context/02-tech-stack.md` | Python 标准库 |\n"
                             "| 3 | 额外 | `context/03-extra.md` | 旧常量没有这一维 |"),
            encoding="utf-8")
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("未登记", cp.stdout)

    def test_default_old_sop_fallback_still_works(self):
        """仓库里跟踪的 sop.v33.bak.py 是 migrate 的缺省输入，不是残留备份；它必须一直可解析。"""
        cp = run("migrate", root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        mf = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(mf["dimensions"]), 8,
                                "v3.3 存档至少应解析出 8 个维度（实跑看输出）")
        self.assertTrue(mf["dimensions"][0]["anchors"])

    # --- 迁移后能接上 v4 工具链 -------------------------------------------

    def test_migrated_project_passes_context_check(self):
        self.write_v3_sop()
        self.write_v3_ai()
        (self.root / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
        cp = run("migrate", "--old-sop", str(self.old_sop), root=self.root)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        cp = run("check", "--context", root=self.root)
        self.assertEqual(cp.returncode, 0, "迁移产物必须能通过 v4 的上下文校验：" + cp.stdout + cp.stderr)


if __name__ == "__main__":
    unittest.main()
