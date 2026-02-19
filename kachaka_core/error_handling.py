"""Unified error handling and retry logic for Kachaka gRPC operations.

Patterns extracted from bio-patrol's retry_with_backoff() and
visual-patrol's structured error responses.
"""

from __future__ import annotations

import functools
import logging
import time

import grpc

logger = logging.getLogger(__name__)

# gRPC status codes that are safe to retry (transient network issues)
RETRYABLE_CODES = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}


def with_retry(max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):
    """Exponential-backoff retry decorator for gRPC operations.

    Only retries on transient network errors (UNAVAILABLE, DEADLINE_EXCEEDED,
    RESOURCE_EXHAUSTED). Business-logic errors (INVALID_ARGUMENT, NOT_FOUND,
    etc.) fail immediately.

    Args:
        max_attempts: Total attempts (including the first call).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Cap on delay between retries.

    Returns:
        dict with ``ok`` key. On failure includes ``error``, ``retryable``,
        and ``attempts`` fields.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except grpc.RpcError as exc:
                    last_error = exc
                    code = exc.code()
                    details = exc.details() or ""
                    if code not in RETRYABLE_CODES:
                        logger.warning(
                            "gRPC non-retryable %s: %s", code.name, details
                        )
                        return {
                            "ok": False,
                            "error": f"{code.name}: {details}",
                            "retryable": False,
                        }
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2**attempt), max_delay)
                        logger.info(
                            "gRPC %s, retrying in %.1fs (attempt %d/%d)",
                            code.name,
                            delay,
                            attempt + 1,
                            max_attempts,
                        )
                        time.sleep(delay)
                except Exception as exc:
                    logger.error("Unexpected error in %s: %s", func.__name__, exc)
                    return {"ok": False, "error": str(exc), "retryable": False}
            # All retries exhausted
            return {
                "ok": False,
                "error": str(last_error),
                "retryable": True,
                "attempts": max_attempts,
            }

        return wrapper

    return decorator


def format_grpc_error(exc: grpc.RpcError) -> dict:
    """Convert a gRPC exception into a structured error dict."""
    code = exc.code()
    return {
        "ok": False,
        "error": f"{code.name}: {exc.details() or ''}",
        "retryable": code in RETRYABLE_CODES,
        "grpc_code": code.name,
    }
