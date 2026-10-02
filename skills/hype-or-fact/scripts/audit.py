#!/usr/bin/env python3
"""Static audit of a cloned candidate repo, before anything from it runs.

    audit.py /path/to/clone            human-readable
    audit.py /path/to/clone --json

Reports:
  1. What it is: skill / plugin / MCP server / other, and what it would add.
  2. Unsandboxed surfaces: parts that Claude Code executes OUTSIDE its Bash
     sandbox -- plugin hooks, MCP servers, LSP servers. The A/B test refuses
     to run these unless explicitly allowed, because the sandbox cannot
     contain them.
  3. Red flags: code patterns worth reading before any install.
  4. Always-on cost: description characters every session pays for, used
     or not.

This is a tripwire, not a verdict. A clean result does not prove the repo is
safe; a hit means a human should read that file.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
             ".next", "target", "vendor"}
TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash",
            ".zsh", ".fish", ".ps1", ".json", ".md", ".toml", ".yaml", ".yml",
            ".rb", ".go", ".rs", ".lua", ""}
MAX_FILES = 20_000
MAX_BYTES = 1_500_000
# JSON files that are configuration and can execute things. Other JSON files
# (catalogues, fixtures, lockfiles) are data and are not pattern-scanned.
CODE_JSON = {"package.json", "hooks.json", "plugin.json", ".mcp.json", "settings.json",
             "settings.local.json", "marketplace.json"}

# (severity, label, regex). HIGH = stop and read. MED = mention in the verdict.
PATTERNS = [
    ("HIGH", "pipes a remote script to a shell",
     r"(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|fi)?sh\b"),
    ("HIGH", "reads credential stores",
     r"(~/\.ssh/|\$HOME/\.ssh|\.aws/credentials|\.netrc\b|\.npmrc\b|\.pypirc\b|"
     r"login\.keychain|security\s+find-(generic|internet)-password|\.config/gh/hosts|"
     r"\.docker/config\.json|\.kube/config)"),
    ("HIGH", "touches Claude credentials or global config",
     r"(\.claude\.json|\.claude/\.credentials|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|"
     r"CLAUDE_CODE_OAUTH)"),
    ("HIGH", "decodes and executes",
     r"(base64\s+(-d|--decode|-D)[^\n]*\|\s*(ba|z)?sh|eval\s*\(\s*(atob|Buffer\.from)|"
     r"exec\s*\(\s*(base64\.b64decode|zlib\.decompress|codecs\.decode))"),
    ("HIGH", "deletes home or root",
     r"rm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(~/?\s|~/\*|\$HOME/?\s|\$HOME/\*|/\s|/\*)"),
    ("MED", "npm/pip install hook", r"\"(pre|post)install\"\s*:"),
    ("MED", "edits global Claude settings", r"~/\.claude/settings(\.local)?\.json"),
    ("MED", "sudo", r"\bsudo\s+\S"),
    ("MED", "disables permission prompts", r"(dangerously-skip-permissions|bypassPermissions)"),
    # Social platforms reached through the user's own logged-in sessions:
    # account-ban risk, and session cookies in files on disk.
    ("MED", "uses your browser logins or cookies",
     r"(browser[_-]cookie3|cookies-from-browser|cookie-editor|"
     r"Application Support/Google/Chrome|\.config/google-chrome|reuse[s]? (your )?Chrome log)"),
    # What gets installed can change after the audit.
    ("MED", "installs from a moving branch",
     r"(archive/(main|master)\.(zip|tar\.gz)|raw\.githubusercontent\.com/\S+?/(main|master)/\S+\.(md|sh|py))"),
    # Call sites, not names: a logo library listing "mixpanel.svg" is data.
    ("MED", "sends telemetry",
     r"(posthog\.(init|capture)|api\.segment\.io|mixpanel\.(init|track)|"
     r"amplitude\.(init|track|getInstance)|sentry_sdk\.init|Sentry\.init)"),
]
MCP_SERVER_RE = re.compile(
    r"(FastMCP\(|from mcp\.server|import mcp\.server|mcp\.server\.(stdio|fastmcp)|"
    r"McpServer\(|@modelcontextprotocol/sdk/server)")
# Descriptions that claim every request ("MUST USE", "any URL") win the
# trigger over skills the user already relies on.
GREEDY_RE = re.compile(r"(\bMUST USE\b|\bALWAYS use\b|\bany (URL|link)s?\b|\bevery (request|task|URL|link)\b|"
                       r"\bfor (any|all) (web|internet|request)s?\b)", re.I)
URL_RE = re.compile(r"https?://([a-zA-Z0-9.-]+\.[a-z]{2,})")
BENIGN_DOMAINS = {"github.com", "raw.githubusercontent.com", "docs.anthropic.com",
                  "anthropic.com", "claude.ai", "claude.com", "code.claude.com",
                  "docs.claude.com", "npmjs.com", "www.npmjs.com", "pypi.org",
                  "shields.io", "img.shields.io", "opensource.org", "www.w3.org",
                  "modelcontextprotocol.io", "example.com", "localhost"}


def _skip(root: Path, p: Path) -> bool:
    return bool(set(p.relative_to(root).parts[:-1]) & SKIP_DIRS)


def walk(root: Path):
    """Regular files inside root. Symlinks are skipped: a symlink can point
    anywhere on the auditor's machine, and following it would audit (and
    report snippets of) files that are not part of the candidate."""
    n = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    stack.append(p)
            elif p.is_file():
                n += 1
                if n > MAX_FILES:
                    return
                yield p


def frontmatter(text: str) -> str:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def _json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(errors="replace"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def classify(root: Path) -> dict:
    all_files = list(walk(root))
    rel = lambda p: str(p.relative_to(root))
    skill_files = [p for p in all_files if p.name == "SKILL.md"]
    manifest = root / ".claude-plugin" / "plugin.json"
    plugin_json = _json(manifest) if manifest.exists() else {}
    is_plugin = manifest.exists()
    is_marketplace = (root / ".claude-plugin" / "marketplace.json").exists()

    # Everything Claude Code would run outside the Bash sandbox.
    unsandboxed = []
    for p in all_files:
        if p.name == "hooks.json":
            unsandboxed.append(f"hooks: {rel(p)}")
        elif p.name == ".mcp.json":
            unsandboxed.append(f"MCP servers: {rel(p)}")
        elif p.name == ".lsp.json":
            unsandboxed.append(f"LSP servers: {rel(p)}")
    for key in ("hooks", "mcpServers", "lspServers"):
        if plugin_json.get(key):
            unsandboxed.append(f"{key} in .claude-plugin/plugin.json")
    for sk in skill_files:
        fm = frontmatter(sk.read_text(errors="replace"))
        if re.search(r"^hooks\s*:", fm, re.M):
            unsandboxed.append(f"hooks in frontmatter: {rel(sk)}")

    # An MCP server is code that constructs one -- not a repo whose keywords
    # or optional extras mention "mcp" (that misread a CLI tool as a server).
    mcp_files = [rel(p) for p in all_files
                 if p.suffix in (".py", ".js", ".mjs", ".cjs", ".ts")
                 and "test" not in p.relative_to(root).parts[0].lower()
                 and p.stat().st_size < MAX_BYTES
                 and MCP_SERVER_RE.search(p.read_text(errors="replace"))]
    mcp = bool(mcp_files)
    pyproject = root / "pyproject.toml"
    pkg_json = _json(root / "package.json")
    cli = (pyproject.exists() and "[project.scripts]" in pyproject.read_text(errors="replace")) \
        or bool(pkg_json.get("bin"))
    if mcp and not is_plugin:
        # A CLI that ships an optional server is a CLI; say where the server is.
        unsandboxed.append(
            f"{'includes an optional' if cli else 'is an'} MCP server "
            f"(runs as its own process if registered): {', '.join(mcp_files[:3])}")

    kind = ("plugin" if is_plugin or is_marketplace else "cli" if cli
            else "mcp" if mcp else "skill" if skill_files else "other")
    return {
        "kind": kind, "is_plugin": is_plugin, "is_marketplace": is_marketplace,
        "plugin_name": plugin_json.get("name"),
        "skill_dirs": sorted({str(p.parent.relative_to(root)) for p in skill_files}),
        "commands": sorted(rel(p) for p in all_files
                           if p.suffix == ".md" and p.parent.name == "commands"),
        "agents": sorted(rel(p) for p in all_files
                         if p.suffix == ".md" and p.parent.name == "agents"),
        "unsandboxed": unsandboxed,
    }


def descriptions(root: Path, skill_dirs: list[str]) -> dict[str, str]:
    """Skill descriptions are loaded into every session; bodies are not."""
    out = {}
    for d in skill_dirs:
        fm = frontmatter((root / d / "SKILL.md").read_text(errors="replace"))
        m = re.search(r"^description:\s*(.*?)(?=^[A-Za-z_-]+:|\Z)", fm, re.S | re.M)
        if m:
            text = re.sub(r"^[>|][-+]?\s*", "", m.group(1).strip())   # YAML block markers
            out[d] = " ".join(text.split()).strip("\"'")
    return out


def description_chars(root: Path, skill_dirs: list[str]) -> int:
    return sum(len(t) for t in descriptions(root, skill_dirs).values())


def greedy_triggers(root: Path, skill_dirs: list[str]) -> list[str]:
    out = []
    for d, text in descriptions(root, skill_dirs).items():
        hits = sorted({m.group(0) for m in GREEDY_RE.finditer(text)})
        if hits:
            out.append(f"{d}: {', '.join(hits)}")
    return out


def scan(root: Path) -> tuple[list[dict], set[str], int, int]:
    flags, domains, n_files, n_lines = [], set(), 0, 0
    for p in walk(root):
        if p.suffix.lower() not in TEXT_EXT or p.stat().st_size > MAX_BYTES:
            continue
        if p.suffix.lower() == ".json" and p.name not in CODE_JSON:
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        n_files += 1
        n_lines += text.count("\n")
        is_doc = p.suffix.lower() == ".md" and p.name != "SKILL.md"
        rel = str(p.relative_to(root))
        for sev, label, rx in PATTERNS:
            hits = list(re.finditer(rx, text, re.I))
            if not hits:
                continue
            m = hits[0]
            line_no = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[line_no - 1] if text.splitlines() else ""
            col = m.start() - (text.rfind("\n", 0, m.start()) + 1)
            snippet = line[max(0, col - 60): col + 100].strip()
            # A README telling humans to run `curl | sh` is common and worth a
            # mention, but it is not code that runs by itself. SKILL.md is not
            # a doc here: its instructions are carried out by the agent.
            flags.append({
                "severity": "MED" if (is_doc and sev == "HIGH") else sev,
                "label": label + (" (in docs)" if is_doc else "")
                         + (f" x{len(hits)}" if len(hits) > 1 else ""),
                "file": rel, "line": line_no, "snippet": snippet,
            })
        if not is_doc:
            domains.update(d.lower() for d in URL_RE.findall(text)
                           if d.lower() not in BENIGN_DOMAINS)
    flags.sort(key=lambda f: (f["severity"] != "HIGH", f["file"]))
    return flags, domains, n_files, n_lines


def audit(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    info = classify(root)
    flags, domains, n_files, n_lines = scan(root)
    return {**info, "files_scanned": n_files, "lines": n_lines,
            "always_on_description_chars": description_chars(root, info["skill_dirs"]),
            "greedy_triggers": greedy_triggers(root, info["skill_dirs"]),
            "flags": flags, "n_high": sum(f["severity"] == "HIGH" for f in flags),
            "outbound_domains": sorted(domains)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo_dir", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = audit(a.repo_dir)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"kind: {r['kind']}{' (' + r['plugin_name'] + ')' if r['plugin_name'] else ''}"
          f" · {r['files_scanned']} files scanned, {r['lines']} lines")
    print(f"skills: {', '.join(r['skill_dirs']) or 'none'}")
    if r["commands"] or r["agents"]:
        print(f"commands: {len(r['commands'])} · agents: {len(r['agents'])}")
    if r["unsandboxed"]:
        print("RUNS OUTSIDE THE SANDBOX:")
        for u in r["unsandboxed"]:
            print(f"  - {u}")
    if r["greedy_triggers"]:
        print("TRIGGER-GREEDY description (may take requests from skills you rely on):")
        for g in r["greedy_triggers"]:
            print(f"  - {g}")
    chars = r["always_on_description_chars"]
    print(f"always-on description cost: {chars} chars (~{chars // 4} tokens per session)")
    print(f"outbound domains in code: {', '.join(r['outbound_domains'][:25]) or 'none'}")
    print(f"flags: {r['n_high']} HIGH, {len(r['flags']) - r['n_high']} other")
    for f in r["flags"][:30]:
        print(f"  [{f['severity']}] {f['label']}: {f['file']}:{f['line']}  {f['snippet']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
