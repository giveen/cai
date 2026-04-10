"""Async HTTP helper utilities using httpx.

Provides a small helper to POST files with retries and exponential backoff
for common transient errors (429, 503, network errors).
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any

import httpx


async def post_file_with_retries(
    endpoint: str,
    file_path: str,
    field_name: str = "log",
    data: dict[str, Any] | None = None,
    timeout: float = 15.0,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> bool:
    """POST a file to ``endpoint`` with retry/backoff for transient errors.

    Returns True on 2xx, False otherwise.
    """
    attempt = 0

    while True:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
                with open(file_path, "rb") as f:
                    files = {field_name: (os.path.basename(file_path), f)}
                    resp = await client.post(endpoint, files=files, data=data)

            # Successful
            if 200 <= resp.status_code < 300:
                return True

            # Retry on rate-limit / server overload codes
            if resp.status_code in (429, 503):
                # Honor Retry-After header when available
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except Exception:
                        delay = None
                else:
                    delay = None

                if delay is None:
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(
                        0, 0.1 * base_delay
                    )

                if attempt >= max_retries:
                    return False

                await asyncio.sleep(delay)
                continue

            # For other non-retriable status codes, fail fast
            return False

        except httpx.RequestError:
            # Network-level error -- retry
            if attempt >= max_retries:
                return False

            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(
                0, 0.1 * base_delay
            )
            await asyncio.sleep(delay)
            continue
        except Exception:
            # Unexpected error
            return False
