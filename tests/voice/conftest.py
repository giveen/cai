import os
import sys

import pytest


def pytest_collect_file(parent, file_path):
    """Skip voice test files when numpy is not installed."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        this_dir = os.path.dirname(__file__)
        if str(file_path).startswith(this_dir) and str(file_path).endswith(".py"):
            return None
    return None


def pytest_collection_modifyitems(config, items):
    try:
        import numpy  # noqa: F401
        _numpy_missing = False
    except ImportError:
        _numpy_missing = True

    this_dir = os.path.dirname(__file__)
    for item in items:
        if _numpy_missing and item.fspath.dirname.startswith(this_dir):
            item.add_marker(pytest.mark.skip(reason="numpy not installed"))
        elif sys.version_info[:2] == (3, 9) and item.fspath.dirname.startswith(this_dir):
            item.add_marker(pytest.mark.skip(reason="Skipped on Python 3.9"))
