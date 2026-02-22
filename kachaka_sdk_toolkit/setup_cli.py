"""CLI tool to register kachaka-sdk-toolkit with Claude Code.

Usage:
    kachaka-setup            # install (default)
    kachaka-setup install     # explicit install
    kachaka-setup uninstall   # remove registration
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# -- Constants ----------------------------------------------------------------

MCP_SERVER_NAME = "kachaka"
SKILL_LINK_NAME = "kachaka-sdk"
SKILL_DIR = Path(__file__).resolve().parent.parent / "skill"
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"


def _find_claude_cli() -> str | None:
    """Return path to claude CLI, or None if not found."""
    return shutil.which("claude")


def _run_claude(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a claude CLI command and return the result."""
    claude = _find_claude_cli()
    if claude is None:
        print("Error: 'claude' CLI not found. Install Claude Code first.")
        print("  https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    return subprocess.run(
        [claude, *args],
        capture_output=True,
        text=True,
    )


# -- Install ------------------------------------------------------------------

def _install_mcp() -> bool:
    """Register the MCP server with Claude Code. Returns True on success/skip."""
    python = sys.executable
    result = _run_claude(
        "mcp", "add", MCP_SERVER_NAME,
        "--scope", "user",
        "--", python, "-m", "mcp_server.server",
    )
    if result.returncode != 0:
        if "already exists" in (result.stderr or "").lower():
            print("  MCP server already registered — skipping")
            return True
        print(f"  Error registering MCP server: {result.stderr.strip()}")
        return False
    print("  MCP server registered")
    return True


def _install_skill() -> bool:
    """Create skill symlink. Returns True on success/skip."""
    if not SKILL_DIR.is_dir():
        print(f"  Error: skill directory not found at {SKILL_DIR}")
        return False

    CLAUDE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    link_path = CLAUDE_SKILLS_DIR / SKILL_LINK_NAME

    if link_path.exists() or link_path.is_symlink():
        print("  Skill symlink already exists — skipping")
        return True

    try:
        link_path.symlink_to(SKILL_DIR)
    except OSError:
        if platform.system() == "Windows":
            shutil.copytree(SKILL_DIR, link_path)
            print("  Skill copied (symlink not supported)")
            return True
        raise
    print("  Skill symlink created")
    return True


def install() -> None:
    """Run full installation."""
    print("[1/2] Registering MCP server...")
    ok = _install_mcp()
    print("[2/2] Installing skill...")
    ok = _install_skill() and ok
    print()
    if ok:
        print("Setup complete! Restart Claude Code to use kachaka tools.")
    else:
        print("Setup finished with errors. Check messages above.")


# -- Uninstall ----------------------------------------------------------------

def _uninstall_mcp() -> bool:
    """Remove MCP server registration. Returns True on success/skip."""
    result = _run_claude(
        "mcp", "remove", MCP_SERVER_NAME,
        "--scope", "user",
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        if "not found" in stderr.lower() or "does not exist" in stderr.lower():
            print("  MCP server not registered — skipping")
            return True
        print(f"  Error removing MCP server: {stderr}")
        return False
    print("  MCP server removed")
    return True


def _uninstall_skill() -> bool:
    """Remove skill symlink or directory. Returns True if action taken."""
    link_path = CLAUDE_SKILLS_DIR / SKILL_LINK_NAME

    if not link_path.exists() and not link_path.is_symlink():
        print("  Skill not installed — skipping")
        return False

    if link_path.is_symlink():
        link_path.unlink()
    elif link_path.is_dir():
        shutil.rmtree(link_path)
    else:
        link_path.unlink()

    print("  Skill removed")
    return True


def uninstall() -> None:
    """Run full uninstallation."""
    print("[1/2] Removing MCP server...")
    ok = _uninstall_mcp()
    print("[2/2] Removing skill...")
    ok = _uninstall_skill() and ok
    print()
    if ok:
        print("Teardown complete. Restart Claude Code to apply changes.")
    else:
        print("Teardown finished with errors. Check messages above.")


# -- CLI entry point ----------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kachaka-setup",
        description="Register kachaka-sdk-toolkit with Claude Code",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "uninstall"],
        help="Action to perform (default: install)",
    )
    args = parser.parse_args()

    if args.action == "uninstall":
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
