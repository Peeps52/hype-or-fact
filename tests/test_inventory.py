import json

import inventory
from conftest import make_plugin, make_skill, write


def setup_config(home, cfg):
    make_skill(cfg, "excalidraw-diagram", "Create Excalidraw diagrams from descriptions.")
    make_skill(cfg, "grill-me", desc="")
    (cfg / "skills" / "not-a-skill").mkdir()
    plug = make_plugin(home / "plugcache" / "brag", "brag", hooks=True)
    write(cfg / "plugins" / "installed_plugins.json", json.dumps(
        {"version": 2, "plugins": {"brag@brag": [{"installPath": str(plug)}]}}))
    write(home / ".claude.json", json.dumps({
        "mcpServers": {"obsidian": {"command": "npx"}},
        "projects": {"/p": {"mcpServers": {"granola": {"url": "https://g"}}}}}))
    write(cfg / "CLAUDE.md", "# Rules\ntext\n## UI Work\n")


def test_collects_everything(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    setup_config(home, home / ".claude")
    inv = inventory.collect()
    assert {s["name"] for s in inv["skills"]} == {"excalidraw-diagram", "grill-me"}
    [p] = inv["plugins"]
    assert p["name"] == "brag@brag" and p["hooks"] and p["skills"][0]["name"] == "fancy"
    assert {(m["name"], m["scope"]) for m in inv["mcp"]} == {("obsidian", "user"), ("granola", "/p")}
    assert inv["claude_md_headings"] == ["Rules", "UI Work"]


def test_match_finds_by_capability_words(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    setup_config(home, home / ".claude")
    lines = inventory.matches(inventory.collect(), ["diagram"])
    assert len(lines) == 1 and "excalidraw-diagram" in lines[0]


def test_honours_claude_config_dir(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "altcfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    setup_config(home, cfg)
    assert len(inventory.collect()["skills"]) == 2


def test_empty_or_broken_setup_does_not_crash(home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write(home / ".claude.json", "{not json")
    write(home / ".claude" / "plugins" / "installed_plugins.json", "[]")
    assert inventory.main([]) == 0
    assert "0 skills, 0 plugins" in capsys.readouterr().out


def test_project_skills_are_included(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_skill(tmp_path / ".claude", "local-one")
    kinds = {(s["name"], s["kind"]) for s in inventory.collect()["skills"]}
    assert ("local-one", "project-skill") in kinds


def test_stale_json_inside_default_config_dir_is_ignored(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(home / ".claude" / ".claude.json", "{}")
    write(home / ".claude.json", json.dumps({"mcpServers": {"obsidian": {"command": "npx"}}}))
    assert [m["name"] for m in inventory.collect()["mcp"]] == ["obsidian"]
