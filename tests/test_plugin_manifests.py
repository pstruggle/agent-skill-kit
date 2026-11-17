"""plugin 打包契约：Claude Code 与 Cursor 两份清单必须存在、可解析、口径一致。

为什么要用测试而不是"看一眼再提交"：
- 两端都要求清单 `name` 与 `skills/<name>/SKILL.md` 的目录结构对得上，
  写错一个字就是"装上了但不加载"这种最难查的故障；
- `plugin.json` 的 `version` 一旦写死，Claude Code 就拿它当缓存键，
  不 bump 用户永远收不到更新；它必须与 SKILL 标题同源，
  否则会出现"插件 4.8.2 / 技能 4.8.3"的错位；
- marketplace 的 `source` 是相对 marketplace 根解析的，写 `../` 或绝对路径会被
  `claude plugin validate` 判非法。仓库根既是插件根又是 marketplace 根，所以这里只允许 `.`。
"""

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "project-task-sop"
SKILL = SKILL_DIR / "SKILL.md"

CLAUDE_PLUGIN = REPO / ".claude-plugin" / "plugin.json"
CLAUDE_MARKET = REPO / ".claude-plugin" / "marketplace.json"
CURSOR_PLUGIN = REPO / ".cursor-plugin" / "plugin.json"
CURSOR_MARKET = REPO / ".cursor-plugin" / "marketplace.json"

HOSTS = [
    ("claude", CLAUDE_PLUGIN, CLAUDE_MARKET),
    ("cursor", CURSOR_PLUGIN, CURSOR_MARKET),
]

# Claude 与 Cursor 共用的名称口径：小写 kebab-case，字母数字起止，允许 `.` `_` `-`，禁止 `..`
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def skill_title_version() -> str:
    for line in SKILL.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#\s*Project Task SOP v(\d+\.\d+\.\d+)", line)
        if m:
            return m.group(1)
    raise AssertionError("SKILL.md 中未找到 `# Project Task SOP vX.Y.Z` 标题")


class PluginManifestTest(unittest.TestCase):
    def test_manifests_exist_and_parse(self):
        """两端各两份清单都要在，且是能解析的 JSON 对象。"""
        for host, manifest, market in HOSTS:
            for path in (manifest, market):
                with self.subTest(host=host, file=path.name):
                    self.assertTrue(path.is_file(), f"{host} 缺少 {path.relative_to(REPO)}")
                    data = load(path)
                    self.assertIsInstance(data, dict)

    def test_plugin_metadata_is_distributable(self):
        """plugin.json 必须具备可分发的最小元数据（名称、描述、许可、仓库）。"""
        for host, manifest, _ in HOSTS:
            with self.subTest(host=host):
                data = load(manifest)
                self.assertIn("name", data, "两端的 plugin.json 都只有 name 是必填")
                self.assertTrue(data.get("description"), "没有 description 的插件在市场里没人点")
                self.assertTrue(data.get("license"), "缺少 license 字段")
                self.assertIn("github.com", data.get("repository", ""), "repository 应指向公开仓库")

    def test_plugin_name_is_valid_and_backed_by_skills_dir(self):
        """名称合规，且 skills/<name>/SKILL.md 真实存在（否则装了也不加载）。"""
        names = set()
        for host, manifest, _ in HOSTS:
            with self.subTest(host=host):
                name = load(manifest)["name"]
                names.add(name)
                self.assertRegex(name, NAME_RE)
                self.assertNotIn("..", name)
                self.assertTrue(
                    (REPO / "skills" / name / "SKILL.md").is_file(),
                    f"skills/{name}/SKILL.md 不存在，插件装上也发现不了技能",
                )
        self.assertEqual(len(names), 1, "两端 plugin.json 的 name 必须一致，否则同步维护两份身份")

    def test_plugin_version_tracks_skill_title(self):
        """插件版本必须等于 SKILL 标题版本：写死的版本不 bump 就发不出更新。"""
        title = skill_title_version()
        for host, manifest, _ in HOSTS:
            with self.subTest(host=host):
                version = load(manifest)["version"]
                self.assertRegex(version, VERSION_RE, "version 必须是 MAJOR.MINOR.PATCH")
                self.assertEqual(
                    version,
                    title,
                    f"{host} plugin.json 写 {version}，SKILL 标题是 {title}；两者必须同源",
                )

    def test_marketplace_declares_owner_and_single_root_plugin(self):
        """marketplace 的必填项齐备，且条目名与 plugin.json 的 name 一致。"""
        plugin_name = load(CLAUDE_PLUGIN)["name"]
        for host, manifest, market in HOSTS:
            with self.subTest(host=host):
                data = load(market)
                self.assertIn("name", data)
                self.assertRegex(data["name"], NAME_RE)
                self.assertTrue(data.get("owner", {}).get("name"), "owner.name 是必填")
                plugins = data.get("plugins")
                self.assertIsInstance(plugins, list)
                self.assertEqual(len(plugins), 1, "本仓库目前只有一个插件，条目数应为 1")
                entry = plugins[0]
                self.assertEqual(entry["name"], plugin_name, "条目名与 manifest 名不一致会装不上")
                self.assertEqual(
                    entry["name"],
                    load(manifest)["name"],
                    f"{host} 的条目名与自身 plugin.json 的 name 必须相同",
                )

    def test_marketplace_source_points_at_plugin_root(self):
        """source 只能是可解析的相对路径，且落在插件根（本仓库为 `.`）。"""
        for host, manifest, market in HOSTS:
            with self.subTest(host=host):
                entry = load(market)["plugins"][0]
                source = entry["source"]
                self.assertIsInstance(source, str)
                self.assertNotIn("..", source, "含 `..` 的相对路径会被 claude plugin validate 判非法")
                self.assertFalse(Path(source).is_absolute(), "source 应相对 marketplace 根解析")
                self.assertIn(source, (".", "./"), f"插件根就是仓库根，source 应为 `.`，实际为 {source}")
                self.assertTrue(
                    (REPO / source / ".claude-plugin" / "plugin.json").is_file()
                    or manifest.relative_to(REPO).as_posix().startswith(source.replace("./", "")),
                    "source 必须能解析到插件自己的 manifest",
                )

    def test_every_skill_dir_declares_matching_name(self):
        """skills/ 下每个技能目录名都要与 frontmatter name 一致，两端发现规则才成立。"""
        skill_files = sorted((REPO / "skills").glob("*/SKILL.md"))
        self.assertTrue(skill_files, "skills/ 下没有任何技能")
        for path in skill_files:
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---"), f"{path} 缺少 YAML frontmatter")
                m = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
                self.assertIsNotNone(m, f"{path} frontmatter 缺少 name")
                self.assertEqual(m.group(1), path.parent.name, "目录名与 frontmatter name 必须一致")


if __name__ == "__main__":
    unittest.main()
