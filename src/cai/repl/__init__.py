"""REPL package initializer.

Provides robust, lazy access to commonly-used submodules (e.g. ``commands``)
so that test import-ordering or light monkeypatching doesn't cause collection-time
ImportError when tests import submodules directly.

This uses PEP 562 (module-level ``__getattr__`` and ``__dir__``) to lazily
import subpackages on attribute access without forcing heavy imports at
package-import time.
"""

from importlib import import_module
from typing import Any

_LAZY_SUBMODULES = ("commands", "ui", "history", "toolbar")


def __getattr__(name: str) -> Any:  # pragma: no cover - trivial delegator
    if name in _LAZY_SUBMODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover - helper for tooling
    return sorted(list(globals().keys()) + list(_LAZY_SUBMODULES))
