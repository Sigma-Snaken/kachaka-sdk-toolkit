"""Kachaka gRPC connection management with pooling and health checks.

Patterns extracted from:
- bio-patrol RobotManager: lazy init, resolver patching, async-safe
- visual-patrol RobotService: serial-number ping, thread-safe locks

Both MCP Server and application code use ``KachakaConnection.get(ip)``
to obtain a pooled, verified connection.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable, Optional

import grpc
from kachaka_api import KachakaApiClient
from kachaka_api.generated.kachaka_api_pb2_grpc import KachakaApiStub

from kachaka_core.interceptors import TimeoutInterceptor

logger = logging.getLogger(__name__)


class ConnectionState(enum.Enum):
    """Connection health state (two-state: no rebuild needed)."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


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

        # ── Monitoring state ──
        self._state = ConnectionState.CONNECTED
        self._state_lock = threading.Lock()
        self._state_condition = threading.Condition(self._state_lock)
        self._on_state_change: Optional[Callable[[ConnectionState], None]] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()

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

    # ── Connection monitoring ────────────────────────────────────────

    @property
    def state(self) -> ConnectionState:
        """Current connection state (thread-safe read)."""
        with self._state_lock:
            return self._state

    def start_monitoring(
        self,
        interval: float = 5.0,
        on_state_change: Optional[Callable[[ConnectionState], None]] = None,
    ) -> None:
        """Start background health-check loop.

        Args:
            interval: Seconds between pings.
            on_state_change: Called on CONNECTED ↔ DISCONNECTED transitions.
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._on_state_change = on_state_change
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._health_check_loop,
            args=(interval,),
            daemon=True,
            name=f"conn-monitor-{self.target}",
        )
        self._monitor_thread.start()
        logger.info("Started monitoring for %s (interval=%.1fs)", self.target, interval)

    def stop_monitoring(self) -> None:
        """Stop background health-check loop."""
        if self._monitor_thread is None:
            return
        self._monitor_stop.set()
        self._monitor_thread.join(timeout=10.0)
        self._monitor_thread = None
        self._on_state_change = None
        logger.info("Stopped monitoring for %s", self.target)

    def wait_for_state(
        self, target_state: ConnectionState, timeout: float | None = None
    ) -> bool:
        """Block until connection reaches *target_state*.

        Returns True if the state was reached, False on timeout.
        """
        with self._state_condition:
            return self._state_condition.wait_for(
                lambda: self._state == target_state,
                timeout=timeout,
            )

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update state and notify waiters + callback."""
        with self._state_condition:
            if self._state == new_state:
                return
            old = self._state
            self._state = new_state
            self._state_condition.notify_all()
        logger.info("Connection %s: %s → %s", self.target, old.value, new_state.value)
        if self._on_state_change is not None:
            try:
                self._on_state_change(new_state)
            except Exception:
                logger.exception("on_state_change callback error")

    def _health_check_loop(self, interval: float) -> None:
        """Daemon thread: ping periodically, update state."""
        while not self._monitor_stop.wait(timeout=interval):
            result = self.ping()
            if result["ok"]:
                self._set_state(ConnectionState.CONNECTED)
            else:
                self._set_state(ConnectionState.DISCONNECTED)

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
