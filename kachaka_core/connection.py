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
from kachaka_api.generated.kachaka_api_pb2_grpc import KachakaApiStub

from kachaka_core.interceptors import TimeoutInterceptor

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
        self._shelves: dict[str, str] = {}
        self._shelf_ids: set[str] = set()
        self._locations: dict[str, str] = {}
        self._location_ids: set[str] = set()

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
        """Fetch shelf/location lists and build our own name-to-ID maps.

        We intentionally do NOT call ``sdk.update_resolver()`` so that
        the SDK's internal resolver stays uninitialised.  All name→ID
        resolution is performed by :meth:`resolve_shelf` and
        :meth:`resolve_location` before handing raw IDs to the SDK.
        """
        if self._resolver_ready:
            return True
        try:
            sdk = self.client
            self._shelves = {s.name: s.id for s in sdk.get_shelves()}
            self._shelf_ids = set(self._shelves.values())
            self._locations = {loc.name: loc.id for loc in sdk.get_locations()}
            self._location_ids = set(self._locations.values())
            self._resolver_ready = True
            logger.info("Resolver ready for %s", self.target)
            return True
        except Exception as exc:
            logger.warning("Resolver init failed for %s: %s", self.target, exc)
            return False

    def resolve_shelf(self, name_or_id: str) -> str:
        """Resolve a shelf name or ID to its canonical ID."""
        if name_or_id in self._shelf_ids:
            return name_or_id
        shelf_id = self._shelves.get(name_or_id)
        if shelf_id:
            return shelf_id
        logger.warning("Shelf not found by name or ID: %s", name_or_id)
        return name_or_id

    def resolve_location(self, name_or_id: str) -> str:
        """Resolve a location name or ID to its canonical ID."""
        if name_or_id in self._location_ids:
            return name_or_id
        loc_id = self._locations.get(name_or_id)
        if loc_id:
            return loc_id
        logger.warning("Location not found by name or ID: %s", name_or_id)
        return name_or_id

    # ── Internal ─────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            logger.info("Connecting to Kachaka at %s …", self.target)
            self._client = KachakaApiClient(self.target)

            # Replace the SDK's plain channel with one that has a timeout
            # interceptor.  The SDK never sets per-call timeouts, so without
            # this, any gRPC call can block indefinitely on server-side
            # disconnects (e.g. robot WiFi drop — measured 522s in testing).
            intercepted_channel = grpc.intercept_channel(
                grpc.insecure_channel(self.target),
                TimeoutInterceptor(self.timeout),
            )
            self._client.stub = KachakaApiStub(intercepted_channel)

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

    @staticmethod
    def _normalise_target(target: str) -> str:
        """Ensure target includes gRPC port."""
        if ":" not in target:
            return f"{target}:26400"
        return target
