# kachaka-setup CLI — Design Document

**Date:** 2026-02-23
**Status:** Approved

## Problem

Setting up kachaka-sdk-toolkit for Claude Code requires 4 manual steps (pip install, claude mcp add, skill symlink, CLAUDE.md). This is error-prone and hard to share across machines or with other users.

## Solution

A Python CLI entry point `kachaka-setup` that automates Claude Code integration after `pip install`.

## CLI Interface

```
kachaka-setup            # default: install everything
kachaka-setup install     # explicit install
kachaka-setup uninstall   # remove MCP registration + skill symlink
```

### Install (2 steps)

1. Register MCP server: `claude mcp add kachaka --scope user -- python -m mcp_server.server`
2. Create skill symlink: `~/.claude/skills/kachaka-sdk -> <repo>/skill`

### Uninstall (2 steps)

1. `claude mcp remove kachaka --scope user`
2. Remove `~/.claude/skills/kachaka-sdk` symlink

Note: `pip install` / `pip uninstall` is the user's responsibility — `kachaka-setup` only manages Claude Code integration.

## Implementation

**One new file:** `kachaka_sdk_toolkit/setup_cli.py`

- Uses only Python stdlib (`subprocess`, `pathlib`, `sys`, `argparse`)
- Calls `claude` CLI via `subprocess.run` (cross-platform)
- Detects `~/.claude/skills/` path via `Path.home()`
- Symlink on Linux/macOS, directory junction or copy on Windows

**Entry point in `pyproject.toml`:**

```toml
[project.scripts]
kachaka-setup = "kachaka_sdk_toolkit.setup_cli:main"
```

**Error handling:**

- Check `claude` CLI is available (friendly error if not)
- Check if MCP already registered (skip if exists)
- Check if skill symlink already exists (skip if exists)
- Idempotent: running twice is safe

## User Flow

**First-time setup:**

```
git clone https://github.com/snaken/kachaka-sdk-toolkit.git
cd kachaka-sdk-toolkit
pip install -e .
kachaka-setup
```

**Uninstall:**

```
kachaka-setup uninstall
pip uninstall kachaka-sdk-toolkit
```
