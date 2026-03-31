# Kachaka SDK Toolkit — Development Rules

## MANDATORY: Read Skill Before Writing Code

**You MUST invoke the `kachaka-sdk` skill and read it BEFORE writing or modifying any code in this project.** Do not skip this step. Do not assume you already know the patterns. The skill contains the authoritative API reference, architecture decisions, and anti-patterns.

## Architecture

- This project IS `kachaka_core` — the shared core for MCP Server, Skill, and Claude Code Plugin
- Structure: `kachaka_core/` (connection, commands, queries, camera, controller, detection, error_handling) + `mcp_server/server.py` + `skills/`
- 182 tests across 9 test files (all mocked) — run with `pytest`

## Hard Rules

1. **Connection**: Always use `KachakaConnection.get(ip)` — never instantiate `KachakaApiClient` directly
2. **Retry**: Use `@with_retry` decorator — never write custom retry logic
3. **Name resolution**: `KachakaConnection` owns the resolver. Do NOT call `sdk.update_resolver()`
4. **Return format**: Every public method returns `{"ok": True/False, ...}` — always check `result["ok"]`
5. **Camera**: Use `CameraStreamer` for continuous capture — never call `get_front_camera_image()` in a loop
6. **Patrols**: Use `RobotController` for multi-step sequences with metrics — `KachakaCommands` is for simple one-shot operations
7. **Error enrichment**: Failed commands include human-readable descriptions from firmware — maintain this pattern
8. **Robot IP**: Always passed as parameter — never hard-coded

## Adding New Functionality

1. Implement in `kachaka_core/commands.py` or `kachaka_core/queries.py`
2. Add corresponding tool in `mcp_server/server.py`
3. Update `skills/kachaka-sdk/SKILL.md`
4. Add tests in `tests/`

## Anti-Patterns (NEVER Do These)

| Don't | Do Instead |
|-------|-----------|
| `KachakaApiClient(ip)` directly | `KachakaConnection.get(ip)` |
| Custom retry/backoff logic | `@with_retry` decorator |
| `sdk.update_resolver()` | `conn.ensure_resolver()` |
| `get_front_camera_image()` in a loop | `CameraStreamer` |
| `KachakaCommands` for patrol sequences | `RobotController` |
| Hard-code robot IP | Pass as parameter |
| Skip `result["ok"]` check | Always check before proceeding |

## Versioning

- **Primary**: `setuptools-scm` reads version from git tags automatically (works with pip >= 23)
- **Fallback**: `setup.cfg` provides static version for old pip (22.x) where build isolation is broken and `[project]` table is not recognized
- **CI auto-syncs**: on `v*` tag push, CI updates `setup.cfg` version, `pyproject.toml` fallback_version, and `marketplace.json` — then commits back to master
- **Never manually edit version** in `pyproject.toml` (`dynamic`) or `setup.cfg` (CI-managed)

## Run

- MCP Server: `kachaka-mcp` or `python -m mcp_server.server`
- Tests: `pytest`
- Install: `pip install -e .`
