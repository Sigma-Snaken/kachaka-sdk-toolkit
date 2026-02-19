"""Kachaka gRPC connection management with pooling and health checks.

Patterns extracted from:
- bio-patrol RobotManager: lazy init, resolver patching, async-safe
- visual-patrol RobotService: serial-number ping, thread-safe locks

Both MCP Server and application code use ``KachakaConnection.get(ip)``
to obtain a pooled, verified connection.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import grpc
from kachaka_api import KachakaApiClient

logger = logging.getLogger(__name__)


class KachakaConnection:
    """Thread-safe, pooled connection to a single Kachaka robot.

    Usage::

        conn = KachakaConnection.get("192.168.1.100:26400")
        result = conn.ping()   # {"ok": True, "serial": "...", "pose": {...}}
        sdk = conn.client      # raw KachakaApiClient for direct access
    """

    _pool: dict[str, KachakaConnection] = {}
    _pool_lock = threading.Lock()

    def __init__(self, target: str, timeout: float = 5.0):
        self.target = self._normalise_target(target)
        self.timeout = timeout
        self._client: Optional[KachakaApiClient] = None
        self._client_lock = threading.Lock()
        self._resolver_ready = False

    # ── Pool management ──────────────────────────────────────────────

    @classmethod
    def get(cls, target: str, timeout: float = 5.0) -> KachakaConnection:
        """Get or create a pooled connection for *target*."""
        key = cls._normalise_target(target)
        with cls._pool_lock:
            if key not in cls._pool:
                cls._pool[key] = cls(key, timeout)
            conn = cls._pool[key]
        conn._ensure_connected()
        return conn

    @classmethod
    def remove(cls, target: str) -> None:
        """Remove a connection from the pool (e.g. on permanent failure)."""
        key = cls._normalise_target(target)
        with cls._pool_lock:
            cls._pool.pop(key, None)

    @classmethod
    def clear_pool(cls) -> None:
        """Drop every pooled connection. Useful in tests."""
        with cls._pool_lock:
            cls._pool.clear()

    # ── Client access ────────────────────────────────────────────────

    @property
    def client(self) -> KachakaApiClient:
        """Return the underlying SDK client, connecting lazily."""
        self._ensure_connected()
        assert self._client is not None
        return self._client

    # ── Health check ─────────────────────────────────────────────────

    def ping(self) -> dict:
        """Verify connectivity by reading serial number and pose."""
        try:
            sdk = self.client
            serial = sdk.get_robot_serial_number()
            pose = sdk.get_robot_pose()
            return {
                "ok": True,
                "serial": serial,
                "pose": {"x": pose.x, "y": pose.y, "theta": pose.theta},
            }
        except grpc.RpcError as exc:
            code = exc.code()
            return {"ok": False, "error": f"{code.name}: {exc.details() or ''}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Resolver ─────────────────────────────────────────────────────

    def ensure_resolver(self) -> bool:
        """Refresh the shelf/location name-to-ID resolver.

        The resolver is needed before any command that references a shelf
        or location by *name*.  Safe to call multiple times.
        """
        if self._resolver_ready:
            return True
        try:
            self.client.update_resolver()
            self._patch_resolver()
            self._resolver_ready = True
            logger.info("Resolver ready for %s", self.target)
            return True
        except Exception as exc:
            logger.warning("Resolver init failed for %s: %s", self.target, exc)
            return False

    # ── Internal ─────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            logger.info("Connecting to Kachaka at %s …", self.target)
            self._client = KachakaApiClient(self.target)
            # Lightweight connectivity check (same as visual-patrol)
            try:
                self._client.get_robot_serial_number()
                logger.info("Connected to %s", self.target)
            except Exception as exc:
                logger.warning(
                    "Connection created but ping failed for %s: %s",
                    self.target,
                    exc,
                )

    def _patch_resolver(self) -> None:
        """Extend the resolver to also match by ID (bio-patrol pattern).

        The stock resolver only looks up by name.  In practice we often
        receive IDs from the UI or from previous query results, so we
        fall back to ID matching when name lookup fails.
        """
        resolver = self._client.resolver

        _orig_shelf = resolver.get_shelf_id_by_name
        _orig_loc = resolver.get_location_id_by_name

        def get_shelf_id_by_name(name_or_id: str) -> str:
            result = _orig_shelf(name_or_id)
            if result != name_or_id:
                return result
            # Fallback: match by ID directly
            for shelf in resolver.shelves:
                if shelf.id == name_or_id:
                    return shelf.id
            logger.warning("Shelf not found by name or ID: %s", name_or_id)
            return name_or_id

        def get_location_id_by_name(name_or_id: str) -> str:
            result = _orig_loc(name_or_id)
            if result != name_or_id:
                return result
            for loc in resolver.locations:
                if loc.id == name_or_id:
                    return loc.id
            logger.warning("Location not found by name or ID: %s", name_or_id)
            return name_or_id

        resolver.get_shelf_id_by_name = get_shelf_id_by_name
        resolver.get_location_id_by_name = get_location_id_by_name

    @staticmethod
    def _normalise_target(target: str) -> str:
        """Ensure target includes gRPC port."""
        if ":" not in target:
            return f"{target}:26400"
        return target
