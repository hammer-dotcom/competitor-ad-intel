"""One-shot: register this repo as an MCP server in Claude Desktop.

    python scripts_install_connector.py            # add / update the connector
    python scripts_install_connector.py --remove   # take it out

Works on macOS, Windows, and Linux. Merges into the existing config file — does not
overwrite other connectors. Prints the config path and the exact block it wrote so you
can inspect or edit by hand.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

NAME = "competitor-ad-intel"


def config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def find_python() -> str:
    """Prefer the repo's .venv if one exists — otherwise the interpreter running us."""
    for candidate in [Path(".venv/bin/python"), Path(".venv/Scripts/python.exe")]:
        if candidate.exists():
            return str(candidate.resolve())
    return sys.executable


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    """Read .env so the connector inherits the same keys the CLI uses."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v and not v.startswith("sk-ant-...") and v != "":
            env[k.strip()] = v
    return env


def install() -> None:
    cfg = config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)

    if cfg.exists():
        current = json.loads(cfg.read_text() or "{}")
        shutil.copy(cfg, cfg.with_suffix(cfg.suffix + ".bak"))
    else:
        current = {}

    repo = Path.cwd().resolve()
    block = {
        "command": find_python(),
        "args": ["-m", "src.mcp_server"],
        "cwd": str(repo),
        "env": {
            "PYTHONPATH": str(repo),
            "ADINTEL_CONFIG": str(repo / "config/competitors.yaml"),
            **load_env(),
        },
    }

    current.setdefault("mcpServers", {})[NAME] = block
    cfg.write_text(json.dumps(current, indent=2))

    print(f"✓ wrote {cfg}")
    print(f"✓ backup at {cfg.with_suffix(cfg.suffix + '.bak')}" if cfg.with_suffix(cfg.suffix + ".bak").exists() else "")
    print("\nRegistered block:")
    print(json.dumps({NAME: block}, indent=2))
    print("\nNow quit Claude Desktop completely (⌘Q on macOS) and reopen.")
    print("The connector 'competitor-ad-intel' should appear under Tools.")


def remove() -> None:
    cfg = config_path()
    if not cfg.exists():
        print(f"nothing to do — {cfg} doesn't exist")
        return
    current = json.loads(cfg.read_text())
    if NAME in current.get("mcpServers", {}):
        del current["mcpServers"][NAME]
        cfg.write_text(json.dumps(current, indent=2))
        print(f"✓ removed '{NAME}' from {cfg}. Restart Claude Desktop.")
    else:
        print(f"'{NAME}' was not present in {cfg}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()
    (remove if args.remove else install)()


if __name__ == "__main__":
    main()
