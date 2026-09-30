import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "hype-or-fact" / "scripts"
FAKES = Path(__file__).resolve().parent / "fakes"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def fake_claude():
    p = FAKES / "claude"
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME, so nothing a test does can touch the real one."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    for k in ("FAKE_ESCAPE", "FAKE_NO_BASH", "FAKE_USE", "FAKE_HANG", "FAKE_LIMIT",
              "FAKE_READ_LEAK"):
        monkeypatch.delenv(k, raising=False)
    return h


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_skill(root: Path, name: str = "fancy", desc: str = "Does fancy things.",
               extra_fm: str = "") -> Path:
    write(root / "skills" / name / "SKILL.md",
          f"---\nname: {name}\ndescription: {desc}\n{extra_fm}---\n\nBody.\n")
    return root


def make_plugin(root: Path, name: str = "fancy-plugin", hooks: bool = False) -> Path:
    write(root / ".claude-plugin" / "plugin.json", f'{{"name": "{name}", "version": "1.0.0"}}')
    make_skill(root)
    if hooks:
        write(root / "hooks" / "hooks.json", '{"hooks": {}}')
    return root
