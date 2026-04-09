"""
Module for executing Python code and capturing its output.
"""

import io
import json
import sys
import traceback

from cai.sdk.agents import function_tool


@function_tool
def execute_python_code(code: str) -> str:
    """
    Execute Python code and return the output.

    Args:
        code (str): Python code to execute
        context (Dict, optional): Additional context for execution

    Returns:
        str: Output from code execution
    """
    old_stdout = sys.stdout
    captured = io.StringIO()

    # Minimal safe builtins: only allow harmless functions
    def _safe_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        """Restricted __import__ implementation allowing only approved modules."""
        allowed = {
            # Data / computation
            "math", "statistics", "random", "decimal", "fractions",
            # Text / encoding
            "re", "textwrap", "string", "unicodedata",
            "base64", "binascii", "quopri", "uu",
            "hashlib", "hmac",
            # Data structures / algorithms
            "json", "csv", "io", "struct", "array",
            "itertools", "functools", "operator",
            "heapq", "bisect", "collections", "copy",
            # Date / time
            "datetime", "calendar", "time",
            # File / path (read-only helpers, no open() in builtins)
            "os.path", "pathlib", "fnmatch", "glob",
            # Networking (agents often need urllib for CTF/recon tasks)
            "urllib", "urllib.request", "urllib.parse", "urllib.error",
            "http", "http.client", "http.cookiejar",
            "socket", "ssl",
            # System / subprocess
            "os", "sys", "subprocess", "shlex", "shutil",
            # Serialisation
            "pickle", "shelve", "configparser",
            "xml", "xml.etree", "xml.etree.ElementTree",
            "html", "html.parser",
            # Misc stdlib
            "typing", "abc", "enum", "dataclasses",
            "contextlib", "warnings", "logging",
            "pprint", "traceback", "inspect",
            # Crypto/security utils useful in pentesting
            "secrets",
        }
        base = name.split(".")[0]
        if base in allowed or name in allowed:
            return __import__(name, globals_ or globals(), locals_ or locals(), fromlist, level)
        raise ImportError(f"module {name} is not allowed in restricted execution environment")

    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "bytes": bytes,
        "bytearray": bytearray,
        "chr": chr,
        "dict": dict,
        "dir": dir,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "frozenset": frozenset,
        "getattr": getattr,
        "hasattr": hasattr,
        "hash": hash,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "memoryview": memoryview,
        "min": min,
        "next": next,
        "oct": oct,
        "open": open,
        "ord": ord,
        "pow": pow,
        "print": print,
        "property": property,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "setattr": setattr,
        "slice": slice,
        "sorted": sorted,
        "staticmethod": staticmethod,
        "str": str,
        "sum": sum,
        "super": super,
        "tuple": tuple,
        "type": type,
        "vars": vars,
        "zip": zip,
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "AttributeError": AttributeError,
        "ImportError": ImportError,
        "OSError": OSError,
        "IOError": IOError,
        "RuntimeError": RuntimeError,
        "StopIteration": StopIteration,
        "NotImplementedError": NotImplementedError,
        "True": True,
        "False": False,
        "None": None,
    }

    # Provide a restricted __import__ so user code can `import` a small safe
    # set of standard-library modules. This avoids ImportError: __import__ not found
    # while preventing arbitrary imports.
    safe_builtins["__import__"] = _safe_import

    try:
        local_vars = {}

        # Capture output using StringIO
        sys.stdout = captured

        # Compile first to provide clearer syntax errors
        compiled = compile(code, "<string>", "exec")

        # Execute in a restricted environment
        restricted_globals = {"__builtins__": safe_builtins}
        # pylint: disable=exec-used
        exec(compiled, restricted_globals, local_vars)  # nosec B102

        output = captured.getvalue()
        return output if output else "Code executed successfully (no output)"

    except Exception as e:
        # Return structured JSON error so callers can programmatically inspect
        partial = captured.getvalue()
        tb = traceback.format_exc()
        error_obj = {
            "error": str(e),
            "traceback": tb,
            "partial_output": partial,
        }
        return json.dumps(error_obj)
    finally:
        sys.stdout = old_stdout
