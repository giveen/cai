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
            "math",
            "json",
            "datetime",
            "statistics",
            "itertools",
            "functools",
            "random",
            "re",
            "textwrap",
            "heapq",
            "collections",
        }
        base = name.split(".")[0]
        if base in allowed:
            return __import__(name, globals_ or globals(), locals_ or locals(), fromlist, level)
        raise ImportError(f"module {name} is not allowed in restricted execution environment")

    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "chr": chr,
        "dict": dict,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "pow": pow,
        "print": print,
        "range": range,
        "repr": repr,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
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
