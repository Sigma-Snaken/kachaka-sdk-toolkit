"""Typical kachaka_core usage — patrol script that visits all locations.

Demonstrates: connection, resolver, movement, polling, camera, error handling.
"""

from __future__ import annotations

import base64
import sys
import time

from kachaka_core.commands import KachakaCommands
from kachaka_core.connection import KachakaConnection
from kachaka_core.queries import KachakaQueries


def patrol_all_locations(robot_ip: str) -> None:
    """Visit every registered location, capture an image at each, return home."""

    # 1. Connect
    conn = KachakaConnection.get(robot_ip)
    ping = conn.ping()
    if not ping["ok"]:
        print(f"Cannot reach robot: {ping['error']}")
        return
    print(f"Connected to {ping['serial']}")

    cmds = KachakaCommands(conn)
    queries = KachakaQueries(conn)

    # 2. List all locations
    loc_result = queries.list_locations()
    if not loc_result["ok"]:
        print(f"Failed to list locations: {loc_result['error']}")
        return

    locations = loc_result["locations"]
    print(f"Found {len(locations)} locations")

    # 3. Visit each location
    for loc in locations:
        name = loc["name"]
        print(f"\n--- Moving to: {name} ---")

        move = cmds.move_to_location(name)
        if not move["ok"]:
            print(f"  Move failed: {move.get('error_code', move.get('error'))}")
            continue

        # Wait for arrival
        poll = cmds.poll_until_complete(timeout=120.0)
        if not poll["ok"]:
            print(f"  Timed out or failed: {poll}")
            continue

        print(f"  Arrived in {poll.get('elapsed', '?')}s")

        # Brief pause then capture
        time.sleep(2)
        img = queries.get_front_camera_image()
        if img["ok"]:
            data = base64.b64decode(img["image_base64"])
            filename = f"patrol_{name.replace(' ', '_')}.jpg"
            with open(filename, "wb") as f:
                f.write(data)
            print(f"  Saved {filename} ({len(data)} bytes)")

    # 4. Return home
    print("\n--- Returning home ---")
    cmds.return_home()
    poll = cmds.poll_until_complete(timeout=120.0)
    status = "OK" if poll["ok"] else f"Failed: {poll}"
    print(f"  Return home: {status}")

    # 5. Final status
    final = queries.get_status()
    if final["ok"]:
        print(f"\nBattery: {final['battery']['percentage']}%")
        print(f"Errors: {final['errors'] or 'None'}")


if __name__ == "__main__":
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.100"
    patrol_all_locations(ip)
