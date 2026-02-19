# Kachaka Robot SDK Skill

## When to Use

When a task involves **Kachaka robot** control, status queries, connection management, or patrol scripting — read this skill.

## Core Principle

**All Kachaka operations MUST go through `kachaka_core`.**
This layer is shared with the MCP Server, ensuring conversation-tested behaviour and production code are always consistent.

## Installation

```bash
pip install -e /path/to/kachaka-sdk-toolkit
```

## Quick Start

```python
from kachaka_core.connection import KachakaConnection
from kachaka_core.commands import KachakaCommands
from kachaka_core.queries import KachakaQueries

# Connect (port 26400 appended automatically)
conn = KachakaConnection.get("192.168.1.100")
result = conn.ping()   # {"ok": True, "serial": "...", "pose": {...}}

cmds = KachakaCommands(conn)
queries = KachakaQueries(conn)
```

## Connection Management

```python
from kachaka_core.connection import KachakaConnection

# Get or create a pooled connection
conn = KachakaConnection.get("192.168.1.100")

# Health check
result = conn.ping()
# {"ok": True, "serial": "KCK-XXXX", "pose": {"x": 1.2, "y": 0.5, "theta": 0.0}}

# Initialise resolver (required before name-based commands)
conn.ensure_resolver()

# Remove from pool (e.g. after IP change)
KachakaConnection.remove("192.168.1.100")
```

### Connection pool is automatic

- First call to `KachakaConnection.get(ip)` creates a new client
- Subsequent calls return the cached instance
- Thread-safe via internal locking
- Resolver supports both name and ID lookups (bio-patrol pattern)

## Movement Commands

```python
cmds = KachakaCommands(conn)

# Move to a named location (resolver auto-initialised)
result = cmds.move_to_location("Kitchen")
# {"ok": True, "action": "move_to_location", "target": "Kitchen"}

# Move to coordinates
result = cmds.move_to_pose(x=1.5, y=2.0, yaw=0.0)

# Relative movement
cmds.move_forward(0.5)         # Forward 0.5m
cmds.move_forward(-0.3)       # Backward 0.3m
cmds.rotate_in_place(1.57)    # 90° counter-clockwise

# Return to charger
cmds.return_home()

# Poll until command finishes
result = cmds.poll_until_complete(timeout=60.0)
# {"ok": True, "error_code": 0, "command": "...", "elapsed": 12.3}
```

## Shelf Operations

```python
# Pick up shelf and deliver to location
cmds.move_shelf("Shelf A", "Meeting Room")

# Return shelf to its home
cmds.return_shelf("Shelf A")     # Named
cmds.return_shelf()               # Currently held

# Dock / undock
cmds.dock_shelf()
cmds.undock_shelf()
```

## Speech

```python
cmds.speak("Patrol complete")
cmds.set_speaker_volume(5)    # 0–10
```

## Status Queries

```python
queries = KachakaQueries(conn)

# Full status snapshot
status = queries.get_status()
# {"ok": True, "pose": {...}, "battery": {"percentage": 85, ...}, ...}

# Individual queries
queries.get_pose()          # {"ok": True, "x": ..., "y": ..., "theta": ...}
queries.get_battery()       # {"ok": True, "percentage": 85, "power_status": "..."}
queries.list_locations()    # {"ok": True, "locations": [{name, id, type, pose}, ...]}
queries.list_shelves()      # {"ok": True, "shelves": [{name, id, home_location_id}, ...]}
queries.get_moving_shelf()  # {"ok": True, "shelf_id": "..." or null}
queries.get_command_state() # {"ok": True, "state": "...", "is_running": false}
queries.get_errors()        # {"ok": True, "errors": []}
```

## Camera

```python
# Returns base64-encoded JPEG
img = queries.get_front_camera_image()
# {"ok": True, "image_base64": "...", "format": "jpeg"}

img = queries.get_back_camera_image()
```

### Decoding the image

```python
import base64
from PIL import Image
import io

data = base64.b64decode(img["image_base64"])
image = Image.open(io.BytesIO(data))
image.save("snapshot.jpg")
```

## Map

```python
# Current map as base64 PNG
map_data = queries.get_map()
# {"ok": True, "image_base64": "...", "format": "png", "name": "...", ...}

# List all maps
queries.list_maps()
# {"ok": True, "maps": [{id, name}, ...], "current_map_id": "..."}
```

## Error Handling

### Built-in retry

All `@with_retry` methods automatically retry on transient gRPC errors (UNAVAILABLE, DEADLINE_EXCEEDED, RESOURCE_EXHAUSTED) with exponential backoff. Non-retryable errors fail immediately.

### Return format

Every method returns a dict:

```python
{"ok": True, ...}                              # Success
{"ok": False, "error": "UNAVAILABLE: ...",     # Failure
 "retryable": True, "attempts": 3}
```

### Custom retry for new functions

```python
from kachaka_core.error_handling import with_retry

@with_retry(max_attempts=5, base_delay=2.0, max_delay=15.0)
def my_custom_operation(sdk):
    ...
```

## Command Control

```python
# Cancel running command
cmds.cancel_command()

# Check state
queries.get_command_state()
queries.get_last_command_result()

# Resume waiting command
cmds.proceed()
```

## Manual Velocity Control

```python
cmds.set_manual_control(True)
cmds.set_velocity(linear=0.1, angular=0.0)    # Forward slowly
cmds.stop()                                      # Emergency stop
```

## Adding New Functionality

### Correct flow

1. Implement in `kachaka_core/commands.py` or `kachaka_core/queries.py`
2. Add corresponding tool in `mcp_server/server.py`
3. Update this SKILL.md
4. Add test in `tests/`

### Wrapping a new SDK method

```python
# In kachaka_core/commands.py
@with_retry()
def my_new_command(self, param: str) -> dict:
    result = self.sdk.some_sdk_method(param)
    return self._result_to_dict(result, action="my_new_command", target=param)

# In mcp_server/server.py
@mcp.tool()
def my_new_command(ip: str, param: str) -> dict:
    """Description for Claude to understand when to use this tool."""
    return KachakaCommands(KachakaConnection.get(ip)).my_new_command(param)
```

## Anti-patterns

| Don't | Do Instead |
|-------|-----------|
| `KachakaApiClient(ip)` directly | `KachakaConnection.get(ip)` |
| Write your own retry logic | Use `@with_retry` decorator |
| Forget to poll command status | Use `poll_until_complete()` |
| Block main thread on long gRPC | Run in a separate thread |
| Hard-code robot IP | Pass as parameter or env var |
| Ignore `result["ok"]` | Always check before proceeding |
| Call `sdk.move_to_location()` raw | Use `cmds.move_to_location()` which handles resolver |

## SDK Reference

The underlying `kachaka-api` SDK (v3.10+) provides:

- **Sync client**: `kachaka_api.KachakaApiClient(target)`
- **Async client**: `kachaka_api.aio.KachakaApiClient(target)`
- **71 methods** covering movement, shelf ops, camera, map, LIDAR, IMU, etc.
- **Resolver**: Auto-maps shelf/location names to IDs
- **Proto types**: `pb2.Result`, `pb2.Pose`, `pb2.Command`, etc.

`kachaka_core` wraps the sync client with connection pooling, retry logic, and structured responses. The async client is available for advanced use cases (streaming, callbacks) but is not wrapped by this toolkit.
