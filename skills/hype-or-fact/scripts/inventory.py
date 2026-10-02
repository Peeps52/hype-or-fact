#!/usr/bin/env python3
"""What is already installed in this Claude Code setup.

    inventory.py                       counts and every name (compact)
    inventory.py --match diagram,svg   full descriptions of likely overlaps
    inventory.py --json                everything, machine-readable

Covers user skills, project skills in the current directory, installed
plugins (their skills, commands, agents, hooks), MCP servers (user and
per-project) and CLAUDE.md headings. Honours CLAUDE_CONFIG_DIR. Read-only.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def global_json() -> Path:
    """~/.claude.json normally; inside CLAUDE_CONFIG_DIR only when that is set.
    (A stray ~/.claude/.claude.json exists on some machines and is stale;
    preferring it silently reported zero MCP servers.)"""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        inside = config_dir() / ".claude.json"
        if inside.exists():
            return inside
    return Path.home() / ".claude.json"


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def frontmatter(p: Path) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", _read(p), re.S)
    if not m:
        return {}
    out: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val.strip("\"'")
        elif key and line[:1] in (" ", "\t"):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _skills_in(root: Path, kind: str) -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        sk = d / "SKILL.md"
        if sk.is_file():
            fm = frontmatter(sk)
            out.append({"kind": kind, "name": fm.get("name") or d.name,
                        "description": fm.get("description", ""), "path": str(d)})
    return out


def user_skills() -> list[dict]:
    return _skills_in(config_dir() / "skills", "skill") + \
        _skills_in(Path.cwd() / ".claude" / "skills", "project-skill")


def plugins() -> list[dict]:
    f = config_dir() / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(_read(f) or "{}")
    except json.JSONDecodeError:
        return []
    data = data.get("plugins", data) if isinstance(data, dict) else {}
    out = []
    for pid, installs in data.items():
        if not isinstance(installs, list) or not installs:
            continue
        root = Path(installs[-1].get("installPath", ""))
        entry = {"kind": "plugin", "name": pid, "path": str(root), "description": "",
                 "skills": [], "commands": [], "agents": [], "hooks": False}
        try:
            entry["description"] = json.loads(
                _read(root / ".claude-plugin" / "plugin.json") or "{}").get("description", "")
        except json.JSONDecodeError:
            pass
        seen = set()   # plugins often vendor the same skill several times
        for sk in sorted(root.glob("**/skills/*/SKILL.md")) if root.is_dir() else []:
            if "node_modules" in sk.parts:
                continue
            fm = frontmatter(sk)
            name = fm.get("name") or sk.parent.name
            if name not in seen:
                seen.add(name)
                entry["skills"].append({"name": name, "description": fm.get("description", "")})
        if root.is_dir():
            entry["commands"] = sorted(p.stem for p in root.glob("commands/*.md"))
            entry["agents"] = sorted(p.stem for p in root.glob("agents/*.md"))
            entry["hooks"] = (root / "hooks" / "hooks.json").exists() or \
                (root / "hooks.json").exists()
        out.append(entry)
    return out


def mcp_servers() -> list[dict]:
    try:
        d = json.loads(_read(global_json()) or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for name, cfg in (d.get("mcpServers") or {}).items():
        out.append({"kind": "mcp", "name": name, "scope": "user",
                    "command": (cfg or {}).get("command") or (cfg or {}).get("url", "")})
    for proj, pc in (d.get("projects") or {}).items():
        for name, cfg in ((pc or {}).get("mcpServers") or {}).items():
            out.append({"kind": "mcp", "name": name, "scope": proj,
                        "command": (cfg or {}).get("command") or (cfg or {}).get("url", "")})
    local = Path.cwd() / ".mcp.json"
    try:
        for name, cfg in (json.loads(_read(local) or "{}").get("mcpServers") or {}).items():
            out.append({"kind": "mcp", "name": name, "scope": str(Path.cwd()),
                        "command": (cfg or {}).get("command") or (cfg or {}).get("url", "")})
    except json.JSONDecodeError:
        pass
    return out


def claude_md() -> list[str]:
    text = _read(config_dir() / "CLAUDE.md")
    return [l.lstrip("#").strip() for l in text.splitlines() if l.startswith("#")]


def collect() -> dict:
    return {"skills": user_skills(), "plugins": plugins(), "mcp": mcp_servers(),
            "claude_md_headings": claude_md()}


def matches(inv: dict, terms: list[str]) -> list[str]:
    def hit(*texts: str) -> bool:
        blob = " ".join(texts).lower()
        return any(t in blob for t in terms)
    lines = []
    for s in inv["skills"]:
        if hit(s["name"], s["description"]):
            lines.append(f"- {s['kind']} `{s['name']}`: {s['description'][:400]}")
    for p in inv["plugins"]:
        for s in p["skills"]:
            if hit(p["name"], s["name"], s["description"]):
                lines.append(f"- plugin `{p['name']}` skill `{s['name']}`: {s['description'][:400]}")
        if hit(p["name"], p["description"], " ".join(p["commands"])):
            lines.append(f"- plugin `{p['name']}`: {p['description'][:300]}"
                         + (f" (commands: {', '.join(p['commands'][:12])})" if p["commands"] else ""))
    for m in inv["mcp"]:
        if hit(m["name"], m["command"]):
            lines.append(f"- mcp `{m['name']}` ({m['scope']}): {m['command']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--match", default="", help="comma-separated terms")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    inv = collect()
    if a.json:
        print(json.dumps(inv, indent=2))
        return 0
    terms = [t.strip().lower() for t in a.match.split(",") if t.strip()]
    if terms:
        lines = matches(inv, terms)
        print(f"# {len(lines)} installed entries match {terms}\n")
        print("\n".join(lines) if lines else "(none)")
        return 0
    n_plug_sk = sum(len(p["skills"]) for p in inv["plugins"])
    print(f"# Installed: {len(inv['skills'])} skills, {len(inv['plugins'])} plugins "
          f"({n_plug_sk} plugin skills), {len(inv['mcp'])} MCP servers\n")
    print("Skills:", ", ".join(s["name"] for s in inv["skills"]) or "none")
    print("\nPlugins:", ", ".join(f"{p['name']}{' [hooks]' if p['hooks'] else ''}"
                                  for p in inv["plugins"]) or "none")
    print("\nMCP:", ", ".join(f"{m['name']}({'user' if m['scope'] == 'user' else 'project'})"
                              for m in inv["mcp"]) or "none")
    print("\nCLAUDE.md sections:", " | ".join(inv["claude_md_headings"]) or "none")
    print("\nNext: --match term1,term2 for full descriptions of likely overlaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
