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

# P0-1 的验收口径：description 是"何时触发"，不是"功能清单"。
# 这些是用户真实会说的口语，缺了它们说明描述又退回了能力罗列。
TRIGGER_PHRASES = [
    "接着上次",
    "之前怎么改",
    "重复踩坑",
    "上下文过期",
    "任务排一下",
    "接下来做哪个",
]


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

    def test_skill_body_within_token_budget(self):
        """SKILL.md 是技能被选中即整篇注入的常驻文档，必须有预算，否则会无声膨胀。

        4.12.1 时实测 ~7.8k token（大半是版本历史里每版几百字的变更说明）；
        4.12.2 把详述搬进 reference/changelog.md 后 ~5.5k。预算按整数留余量：
        主文档 6000、changelog 4000——两者都超说明又该拆，而不是放宽阈值。
        """
        out = subprocess.run(
            [sys.executable, str(SKILL.parent / "sop.py"), "tokens", str(SKILL)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(SKILL.parent),
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        m = re.search(r"(\d+) tokens", out.stdout)
        self.assertTrue(m, "sop.py tokens 输出格式变了，守卫失效：" + out.stdout)
        tokens = int(m.group(1))
        self.assertLessEqual(tokens, 6000, "SKILL.md 已涨到 {} token（预算 6000）："
                              "把细节继续搬进 reference/，不要放宽预算".format(tokens))

        log = SKILL.parent / "reference" / "changelog.md"
        out = subprocess.run(
            [sys.executable, str(SKILL.parent / "sop.py"), "tokens", str(log)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(SKILL.parent),
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        m = re.search(r"(\d+) tokens", out.stdout)
        self.assertTrue(m, "sop.py tokens 输出格式变了，守卫失效：" + out.stdout)
        self.assertLessEqual(int(m.group(1)), 4000, "changelog 已涨到 {} token（预算 4000）："
                              "按版本区间归档（如 changelog-v4.0-4.6.md）")

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

    def test_description_contains_trigger_phrases(self):
        """P0-1：description 必须能被用户的口语命中。

        真实漂移：4.8.0 前 description 是"轻量项目任务执行规程：启动前边界对齐、规模决策门…"，
        全是能力项，用户说"接着上次做"匹配不到；`whenToUse` 也只有抽象场景。
        """
        desc = frontmatter_description()
        hits = [p for p in TRIGGER_PHRASES if p in desc]
        self.assertGreaterEqual(len(hits), 4,
                                "description 里口语触发语不足 4 条（当前 {} 条：{}）。应写成"
                                "「场景 + 触发语」，而不是功能清单".format(len(hits), hits))
        self.assertLessEqual(len(desc), 800, "description 过长（{} 字符），触发面会被人名/命令清单稀释".format(len(desc)))

    def test_when_to_use_entries_are_concrete(self):
        """`whenToUse` 每条应是用户能说出口的话，不是抽象场景名。"""
        lines = SKILL.read_text(encoding="utf-8").splitlines()
        start = None
        for i, ln in enumerate(lines):
            if ln.startswith("whenToUse:"):
                start = i
                break
        self.assertIsNotNone(start, "frontmatter 缺少 whenToUse")
        items = []
        for ln in lines[start + 1:]:
            if ln.strip() == "---" or (ln and not ln.startswith((" ", "\t", "-"))):
                break
            if ln.strip().startswith("- "):
                items.append(ln.strip()[2:])
        self.assertGreaterEqual(len(items), 5, "whenToUse 条目过少：{}".format(items))
        quoted = [s for s in items if "「" in s or "“" in s or '"' in s]
        self.assertGreaterEqual(len(quoted), 3,
                                "whenToUse 至少 3 条要写成口语触发（带引号的原话），当前只有 {} 条：{}".format(
                                    len(quoted), items))


if __name__ == "__main__":
    unittest.main()
