"""版本同步点校验：五处版本号必须一致，frontmatter 必须列出全部子命令。

背景（都是真实发生过的漂移）：
- 4.7.3 之前，`sop.py` docstring 与 `--help` 长期停在 `v4.0`，而 SKILL 已到 4.7.2；
- SKILL 标题曾写 v4.4、历史表把 4.6.0 排在 4.5.2 之前；
- README 技能表曾写 3.1.0，而历史表写 4.6.0；
- frontmatter `description` 曾漏 `entry`/`tasks`，导致技能目录匹配不到「入口文件」「任务台账」。

结论：版本号有五个落点，靠"记得改"不可靠，改成回归测试——漏改一次，提交就红。
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "project-task-sop"
SKILL = SKILL_DIR / "SKILL.md"
SOP = SKILL_DIR / "sop.py"
README = REPO / "README.md"

V = r"(\d+\.\d+\.\d+)"


def skill_title_version() -> str:
    for line in SKILL.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#\s*Project Task SOP v" + V, line)
        if m:
            return m.group(1)
    raise AssertionError("SKILL.md 中未找到 `# Project Task SOP vX.Y.Z` 标题")


def history_versions() -> list:
    rows = []
    for line in SKILL.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*" + V + r"\s*\|", line)
        if m:
            rows.append(m.group(1))
    if not rows:
        raise AssertionError("SKILL.md 版本历史表为空")
    return rows


def readme_version() -> str:
    for line in README.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `project-task-sop`"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(V, cells[-1])
        if m:
            return m.group(1)
    raise AssertionError("README 技能表中未找到 project-task-sop 的版本列")


def docstring_version() -> str:
    head = "\n".join(SOP.read_text(encoding="utf-8").splitlines()[:40])
    m = re.search(r"project-task-sop CLI v" + V, head)
    if not m:
        raise AssertionError("sop.py docstring 首行未声明 `project-task-sop CLI vX.Y.Z`")
    return m.group(1)


def help_text() -> str:
    cp = subprocess.run(
        [sys.executable, str(SOP), "--help"],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    if cp.returncode != 0:
        raise AssertionError("`sop.py --help` 执行失败：\n{}\n{}".format(cp.stdout, cp.stderr))
    return cp.stdout + cp.stderr


def frontmatter_description() -> str:
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("SKILL.md 缺少 frontmatter")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            return line
    raise AssertionError("frontmatter 中未找到 description 行")


class TestVersionSync(unittest.TestCase):
    def test_five_version_points_match(self):
        points = {
            "SKILL.md 标题": skill_title_version(),
            "SKILL.md 历史表末行": history_versions()[-1],
            "README 技能表": readme_version(),
            "sop.py docstring": docstring_version(),
            "sop.py --help": (re.search(r"project-task-sop CLI v" + V, help_text()) or [None, None])[1],
        }
        bad = {k: v for k, v in points.items() if v != points["SKILL.md 标题"]}
        self.assertEqual(bad, {}, "版本号未同步：以 SKILL.md 标题 {} 为准，其余 {}".format(
            points["SKILL.md 标题"], bad))

    def test_history_rows_sorted_ascending(self):
        rows = history_versions()
        keys = [tuple(int(x) for x in v.split(".")) for v in rows]
        self.assertEqual(keys, sorted(keys), "版本历史表必须按版本号升序排列，实际：{}".format(rows))
        self.assertEqual(len(set(rows)), len(rows), "版本历史表存在重复版本行：{}".format(rows))

    def test_no_py310_only_pathlib_write_kwargs(self):
        """`Path.write_text` 的 newline 关键字是 Python 3.10+ 签名，3.9 上直接 TypeError。

        CI 的 windows-3.9 job 抓到过：台账/计划文件全部没写出来，`tasks` 直接崩。
        vermin 这类静态检查查不出关键字参数，只能用文本守卫兜住。
        """
        lines = SOP.read_text(encoding="utf-8").splitlines()
        hits = [i + 1 for i, ln in enumerate(lines) if "write_text(" in ln and "newline=" in ln]
        self.assertEqual(hits, [], "sop.py 第 {} 行用了 3.10+ 的 write_text(newline=)，改用 write_text_lf()".format(hits))

    def test_frontmatter_lists_all_subcommands(self):
        help_out = help_text()
        m = re.search(r"\{([a-z][a-z0-9,\-_]*)\}", help_out)
        self.assertIsNotNone(m, "--help 未输出子命令枚举")
        subcommands = [s for s in m.group(1).split(",") if s]
        desc = frontmatter_description()
        missing = [s for s in subcommands if s not in desc]
        self.assertEqual(missing, [], "frontmatter description 缺少子命令：{}（技能目录会匹配不到这些能力）".format(missing))


if __name__ == "__main__":
    unittest.main()
