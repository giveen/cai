"""Virtual Context Manager (VCM) for paging large workspace context.

This module provides a simple, thread-safe Virtual Context Manager that
divides a large model context window (e.g. ~393k tokens) into named
"Pages" (Recon, Creds, Payloads, etc.). Pages can be paged into the
GPU-active context or paged out to host RAM. The implementation is a
lightweight simulation: it manages residency flags and an in-memory
"gpu store" to represent what is currently resident in the active
context. Higher-level agents and tools call into this API to swap
pages in/out so the agent can 'forget' noisy reconnaissance data while
cracking hashes, and quickly page it back when needed.

This is intentionally implementation-light: actual GPU/context memory
management is beyond this project's scope; instead we provide a clear
API and eviction policy (LRU) to emulate paging semantics.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rudimentary token estimate based on character count.

    This is a heuristic used for capacity planning; a conservative
    estimate is sufficient for eviction decisions.
    """
    if not text:
        return 0
    # Rough heuristic: ~4 characters per token
    return max(1, len(text) // 4)


@dataclass
class Page:
    name: str
    content: str = ""
    tags: List[str] = field(default_factory=list)
    pinned: bool = False
    preferred_tokens: Optional[int] = None

    # Runtime fields
    in_gpu: bool = False
    last_access: float = field(default_factory=time.time)

    def token_count(self) -> int:
        return _estimate_tokens(self.content or "")

    def touch(self) -> None:
        self.last_access = time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tokens": self.token_count(),
            "pinned": self.pinned,
            "in_gpu": self.in_gpu,
            "last_access": self.last_access,
            "tags": list(self.tags),
        }


class VirtualContextManager:
    """Manages named Pages and which pages are resident in the active context.

    Args:
        max_active_tokens: total token budget for pages resident in the
            'GPU' active context (default ~393k tokens)
    """

    def __init__(self, max_active_tokens: int = 393216) -> None:
        self.max_active_tokens = int(max_active_tokens)
        self._pages: Dict[str, Page] = {}
        self._gpu_store: Dict[str, str] = {}  # page_name -> content
        self._lock = threading.RLock()

    # -- Page lifecycle ----------------------------------------------
    def add_page(self, name: str, content: str = "", tags: Optional[List[str]] = None, *,
                 pinned: bool = False, preferred_tokens: Optional[int] = None) -> Page:
        with self._lock:
            if name in self._pages:
                pg = self._pages[name]
                pg.content = content
                if tags:
                    pg.tags = tags
                pg.pinned = pinned
                pg.preferred_tokens = preferred_tokens
                pg.touch()
            else:
                pg = Page(name=name, content=content, tags=tags or [], pinned=pinned,
                          preferred_tokens=preferred_tokens)
                self._pages[name] = pg
            logger.debug("VCM: added/updated page %s (tokens=%d)", name, pg.token_count())
            return pg

    def remove_page(self, name: str) -> bool:
        with self._lock:
            pg = self._pages.pop(name, None)
            if pg and pg.in_gpu:
                self._gpu_store.pop(name, None)
            logger.debug("VCM: removed page %s", name)
            return bool(pg)

    def list_pages(self) -> List[dict]:
        with self._lock:
            return [p.to_dict() for p in self._pages.values()]

    # -- Residency / paging ops -------------------------------------
    def _active_tokens(self) -> int:
        with self._lock:
            total = 0
            for name in list(self._gpu_store.keys()):
                pg = self._pages.get(name)
                if pg:
                    total += pg.token_count()
            return total

    def _evict_until_fit(self, needed_tokens: int, protect: Optional[List[str]] = None) -> List[str]:
        """Evict least-recently-used non-pinned pages until there is room.

        Returns a list of evicted page names.
        """
        with self._lock:
            protect = protect or []
            evicted: List[str] = []
            current = self._active_tokens()
            if current + needed_tokens <= self.max_active_tokens:
                return evicted

            # Build candidate list: in-gpu, not pinned, not in protect
            candidates = [p for p in self._pages.values() if p.in_gpu and not p.pinned and p.name not in protect]
            # Sort by last_access ascending (LRU)
            candidates.sort(key=lambda p: p.last_access or 0)

            for c in candidates:
                logger.debug("VCM: evicting page %s to make room", c.name)
                self._gpu_store.pop(c.name, None)
                c.in_gpu = False
                evicted.append(c.name)
                current = self._active_tokens()
                if current + needed_tokens <= self.max_active_tokens:
                    break

            return evicted

    def page_in(self, name: str, *, force: bool = False, pin: bool = False) -> dict:
        """Bring a named page into the 'GPU' active context.

        If necessary, evict pages using LRU unless `force` is True. If a page
        is larger than the configured budget and `force` is False, the call
        will return an error indicating the page doesn't fit.
        """
        with self._lock:
            if name not in self._pages:
                raise KeyError(f"Page not found: {name}")
            pg = self._pages[name]
            tokens = pg.token_count()

            if pg.in_gpu:
                pg.touch()
                logger.debug("VCM: page %s already in GPU", name)
                return {"status": "already_in_gpu", "name": name}

            if tokens > self.max_active_tokens and not force:
                return {"status": "error", "reason": "page_too_large", "name": name, "tokens": tokens}

            evicted = self._evict_until_fit(tokens, protect=[name])

            # If still doesn't fit and force==True, evict all non-pinned; if still
            # doesn't fit, accept it (will exceed budget) but log a warning.
            if self._active_tokens() + tokens > self.max_active_tokens:
                if force:
                    # evict everything non-pinned
                    for p in list(self._pages.values()):
                        if p.in_gpu and not p.pinned and p.name != name:
                            self._gpu_store.pop(p.name, None)
                            p.in_gpu = False
                            evicted.append(p.name)
                    logger.warning("VCM: force-paged in %s; exceeded budget", name)
                else:
                    return {"status": "error", "reason": "no_space", "evicted": evicted}

            # Page it in
            self._gpu_store[name] = pg.content
            pg.in_gpu = True
            pg.touch()
            if pin:
                pg.pinned = True

            logger.info("VCM: paged in %s (tokens=%d), evicted=%s", name, tokens, evicted)
            return {"status": "paged_in", "name": name, "evicted": evicted, "tokens": tokens}

    def page_out(self, name: str) -> dict:
        with self._lock:
            pg = self._pages.get(name)
            if not pg:
                return {"status": "error", "reason": "not_found", "name": name}
            if not pg.in_gpu:
                return {"status": "already_paged_out", "name": name}
            self._gpu_store.pop(name, None)
            pg.in_gpu = False
            pg.touch()
            logger.info("VCM: paged out %s", name)
            return {"status": "paged_out", "name": name}

    def swap(self, in_name: str, out_name: Optional[str] = None, *, force: bool = False, pin: bool = False) -> dict:
        with self._lock:
            result_in = self.page_in(in_name, force=force, pin=pin)
            if out_name:
                result_out = self.page_out(out_name)
            else:
                result_out = None
            return {"in": result_in, "out": result_out}

    def get_active_context(self, joiner: str = "\n\n") -> str:
        """Return concatenated content of all pages currently resident in GPU.

        Pages are returned in most-recently-accessed order (MRU), which tends
        to align with relevance for active tasks.
        """
        with self._lock:
            # Sort by last_access descending for MRU
            pages = [p for p in self._pages.values() if p.in_gpu]
            pages.sort(key=lambda p: p.last_access or 0, reverse=True)
            return joiner.join([self._gpu_store.get(p.name, "") for p in pages])

    def get_page(self, name: str) -> Optional[Page]:
        with self._lock:
            return self._pages.get(name)

    def export_state(self) -> dict:
        with self._lock:
            return {
                "max_active_tokens": self.max_active_tokens,
                "pages": {n: {"content": p.content, "tags": p.tags, "pinned": p.pinned} for n, p in self._pages.items()},
                "gpu": list(self._gpu_store.keys()),
            }

    def import_state(self, state: dict) -> None:
        with self._lock:
            self.max_active_tokens = int(state.get("max_active_tokens", self.max_active_tokens))
            pages = state.get("pages", {})
            for name, info in pages.items():
                self.add_page(name, info.get("content", ""), tags=info.get("tags", []), pinned=info.get("pinned", False))
            gpu = state.get("gpu", [])
            for n in gpu:
                try:
                    self.page_in(n, force=True)
                except Exception:
                    pass


# Singleton instance for convenience
VCM = VirtualContextManager()


# Convenience API used by tools/agents ---------------------------------
def register_page(name: str, content: str = "", tags: Optional[List[str]] = None, *, pinned: bool = False, preferred_tokens: Optional[int] = None) -> Page:
    return VCM.add_page(name, content, tags, pinned=pinned, preferred_tokens=preferred_tokens)


def page_in(name: str, *, force: bool = False, pin: bool = False) -> dict:
    return VCM.page_in(name, force=force, pin=pin)


def page_out(name: str) -> dict:
    return VCM.page_out(name)


def swap(in_name: str, out_name: Optional[str] = None, *, force: bool = False, pin: bool = False) -> dict:
    return VCM.swap(in_name, out_name, force=force, pin=pin)


def list_pages() -> List[dict]:
    return VCM.list_pages()


def get_active_context() -> str:
    return VCM.get_active_context()


def init_default_partition(total_tokens: int = 393216) -> None:
    """Create a sensible default partition of the big window into named pages.

    Default split (percentages): Recon=50%, Creds=20%, Payloads=20%, Notes=10%.
    Pages are initially empty; tools/agents should populate them via
    `register_page(name, content=...)`.
    """
    recon = int(total_tokens * 0.50)
    creds = int(total_tokens * 0.20)
    payloads = int(total_tokens * 0.20)
    notes = total_tokens - (recon + creds + payloads)
    register_page("recon", "", tags=["recon"], preferred_tokens=recon)
    register_page("creds", "", tags=["creds"], preferred_tokens=creds)
    register_page("payloads", "", tags=["payloads"], preferred_tokens=payloads)
    register_page("notes", "", tags=["notes"], preferred_tokens=notes)


# ── VPN / network-log protection ─────────────────────────────────────────────

# Any page whose content matches one of these terms will be treated as
# high-salience and automatically pinned so the LRU eviction policy never
# compacts critical network pivoting data.
VPN_PROTECTION_PATTERNS: List[str] = [
    "tun0",
    "VPN",
    "AUTHENTICATION SUCCESS",
    "Initialization Sequence Completed",
    "AUTH_FAILED",
    "CONNECTED",
]


def register_vpn_log_page(name: str, content: str) -> Page:
    """Register (or update) a VPN log page in the VCM, always pinned.

    Pinned pages are never subject to LRU eviction, ensuring that tunnel
    logs and authentication events survive context-window compaction while
    the agent is actively pivoting through the target network.

    Args:
        name:    The page name (e.g. ``"vpn_logs"``).
        content: The current log text to store.

    Returns:
        The registered :class:`Page` object.
    """
    pg = VCM.add_page(name, content, tags=["vpn", "network", "critical"], pinned=True)
    # Force page into active context so it's always visible to the agent
    try:
        VCM.page_in(name, force=True, pin=True)
    except Exception:
        pass
    logger.info("VCM: pinned VPN log page '%s' (%d tokens)", name, pg.token_count())
    return pg


def protect_vpn_logs() -> List[str]:
    """Scan all current VCM pages and pin those containing VPN protection patterns.

    Call this after bulk-loading pages (e.g. from a restored session) to
    ensure any pre-existing VPN/tun0 content is not accidentally evicted.

    Returns:
        List of page names that were newly pinned.
    """
    newly_pinned: List[str] = []
    with VCM._lock:
        for name, pg in VCM._pages.items():
            if pg.pinned:
                continue
            content_lower = (pg.content or "").lower()
            if any(pat.lower() in content_lower for pat in VPN_PROTECTION_PATTERNS):
                pg.pinned = True
                newly_pinned.append(name)
                logger.info("VCM: auto-pinned page '%s' (matched VPN protection pattern)", name)
    return newly_pinned


__all__ = [
    "VCM",
    "register_page",
    "page_in",
    "page_out",
    "swap",
    "list_pages",
    "get_active_context",
    "init_default_partition",
    "register_vpn_log_page",
    "protect_vpn_logs",
    "VPN_PROTECTION_PATTERNS",
    "VirtualContextManager",
    "Page",
]
