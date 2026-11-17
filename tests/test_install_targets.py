"""安装器目标覆盖：cursor / gemini / copilot 三家必须在 ps1 与 sh 里同时可用。

背景（P0-3）：
- 4.8.1 前 `-Target` 只有 dsh/codex/claude/agents/custom，而这三家都已是 Agent Skills 标准的使用方；
- 两个安装器是两份手动维护的代码，最容易出现「ps1 加了、sh 忘了」的单边漂移；
- 目标目录有权威出处：Cursor `~/.cursor/skills`、Gemini CLI `~/.gemini/skills`、
  Copilot `~/.copilot/skills`（个人）/ `.github/skills`（项目）。

只跑 `--list` / `-List`：不写任何文件，可在 CI 与本地安全执行。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PS1 = REPO / "install.ps1"
SH = REPO / "install.sh"

NEW_TARGETS = ["cursor", "copilot", "gemini"]


def ps1_targets() -> set:
    text = PS1.read_text(encoding="utf-8")
    m = re.search(r"\[ValidateSet\((.+?)\)\]\s*\n\s*\[string\[\]\]\$Target", text, re.S)
    if not m:
        return set()
    return {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}


def sh_targets() -> set:
    text = SH.read_text(encoding="utf-8")
    m = re.search(r"if \[ \"\$targets\" = all \]; then\n\s*else\n\s*target_list=.*?\nfi", text, re.S)
    if m:
        # 显式目标不在默认串里也能从 root_for 的 case 分支取全集
        pass
    roots = re.search(r"root_for\(\) \{\n(.*?)\n\}", text, re.S)
    if not roots:
        return set()
    return set(re.findall(r"^\s{8}([a-z]+)\)", roots.group(1), re.M))


def run_ps1_list(extra: str) -> str:
    """用 -Command 调用（-File 会把 "a,b" 当成单个字符串，ValidateSet 不认）。"""
    cmd = "& '{}' -List {}".format(str(PS1).replace("'", "''"), extra)
    cp = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", cmd],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    if cp.returncode != 0:
        raise AssertionError("install.ps1 -List 失败：\n{}\n{}".format(cp.stdout, cp.stderr))
    return cp.stdout


def run_sh_list(args: list) -> str:
    cp = subprocess.run(
        ["sh", str(SH), "--list"] + args,
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
    )
    if cp.returncode != 0:
        raise AssertionError("install.sh --list 失败：\n{}\n{}".format(cp.stdout, cp.stderr))
    return cp.stdout


class TestInstallerTargetParity(unittest.TestCase):
    def test_both_scripts_declare_the_same_targets(self):
        """两个安装器的目标集合必须一致，且都含本次新增的三家。"""
        ps1_set = ps1_targets() - {"all", "custom"}  # custom 两边都走 -Dest/--dest，不在 root_for 里
        sh_set = sh_targets()
        self.assertTrue(ps1_set, "未解析出 install.ps1 的 -Target 集合")
        self.assertTrue(sh_set, "未解析出 install.sh 的 target 集合")
        self.assertEqual(ps1_set, sh_set,
                         "install.ps1 与 install.sh 目标不一致：ps1={} sh={}".format(
                             sorted(ps1_set), sorted(sh_set)))
        missing = [t for t in NEW_TARGETS if t not in ps1_set]
        self.assertEqual(missing, [], "安装器缺少目标：{}".format(missing))


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "需要 Windows + pwsh")
class TestInstallerTargetsWindows(unittest.TestCase):
    def test_user_scope_paths(self):
        out = run_ps1_list("-Target cursor,copilot,gemini")
        for frag in (".cursor{}skills".format(os.sep), ".copilot{}skills".format(os.sep),
                     ".gemini{}skills".format(os.sep)):
            self.assertIn(frag, out, "install.ps1 未给出 {} 的用户级路径".format(frag))

    def test_project_scope_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_ps1_list("-Target cursor,copilot,gemini -Scope project -ProjectRoot '{}'".format(tmp))
            out_norm = out.replace("\\", "/")
            base = Path(tmp).as_posix()
            for frag in (".cursor/skills", ".github/skills", ".gemini/skills"):
                self.assertIn("{}/{}".format(base, frag), out_norm,
                              "项目级路径缺少 {}".format(frag))


@unittest.skipIf(os.name == "nt", "POSIX 专用")
class TestInstallerTargetsPosix(unittest.TestCase):
    def test_user_scope_paths(self):
        out = run_sh_list(["--target", ",".join(NEW_TARGETS)])
        for frag in (".cursor/skills", ".copilot/skills", ".gemini/skills"):
            self.assertIn(frag, out, "install.sh 未给出用户级路径 {}".format(frag))

    def test_project_scope_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_sh_list(["--target", ",".join(NEW_TARGETS),
                               "--scope", "project", "--project-root", tmp])
            for frag in (".cursor/skills", ".github/skills", ".gemini/skills"):
                self.assertIn("{}/{}".format(tmp, frag), out,
                              "项目级路径缺少 {}".format(frag))


if __name__ == "__main__":
    unittest.main()
