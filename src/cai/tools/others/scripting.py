"""
This is used to create and execute a script in python
"""
# pylint: disable=import-error
# run_command is used in other parts of the codebase that import this module

# pylint: disable=too-many-locals,too-many-branches
from cai.sdk.agents import function_tool


@function_tool
def scripting_tool(
    command: str = "",
    args: str = "",
    ctf=None,  # pylint: disable=unused-argument
) -> str:
    """Scripting tool for executing Python code directly in memory.
    IMPORTANT: Use with caution - executes Python code directly.
    IMPORTANT: Remember to import all the modules and libraries you need.

    Args:
        command: Python code, with or without markdown format. Can handle:
            - Raw Python code
            - Markdown formatted code (```python\\ncode)
            - Code with leading/trailing whitespace
        args: Additional command line arguments
        ctf: CTF context object (unused but required for tool interface)

    Returns:
        str: Output from the executed Python code

    Raises:
        ValueError: If the command is empty or invalid
        SecurityError: If potentially dangerous operations are detected
    """
    # pylint: disable=import-outside-toplevel
    import ast
    import json
    import re
    import sys
    import traceback
    from io import StringIO

    if not command or not isinstance(command, str):
        raise ValueError("Command must be a non-empty string")

    command = command.strip()

    markdown_patterns = [
        r"^```python\n(.*?)\n```",  # Standard markdown
        r"^```python(.+?)```",  # No newlines
        r"^```\n(.*?)\n```",  # No language specified
        r"^`{1,3}(.*?)`{1,3}",  # Single or triple backticks
    ]
    script = command
    for pattern in markdown_patterns:
        match = re.search(pattern, command, re.DOTALL)
        if match:
            script = match.group(1)
            break

    script = script.strip()
    if not script:
        raise ValueError("No valid Python code found in command")

    _BLOCKED_MODULES = {"os", "sys", "subprocess", "shutil"}

    def _restricted_import(name, globals_=None, locals_=None, fromlist=(), level=0):
        """Allow imports of any module not in _BLOCKED_MODULES."""
        base = name.split(".")[0]
        if base in _BLOCKED_MODULES:
            raise ImportError(f"module '{name}' is not allowed in restricted execution environment")
        return __import__(name, globals_ or {}, locals_ or {}, fromlist, level)

    try:
        tree = ast.parse(script)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # Iterate all aliases: `import os, sys` has two names
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in _BLOCKED_MODULES:
                        raise SecurityError(f"Importing potentially dangerous module: {module}")
            elif isinstance(node, ast.ImportFrom):
                # `from os import path` — check the module being imported from
                module = (node.module or "").split(".")[0]
                if module in _BLOCKED_MODULES:
                    raise SecurityError(f"Importing potentially dangerous module: {module}")
    except SyntaxError as e:
        err = {
            "error": "SyntaxError",
            "message": str(e),
            "lineno": getattr(e, "lineno", None),
            "offset": getattr(e, "offset", None),
            "text": getattr(e, "text", None),
        }
        return json.dumps(err)
    except SecurityError as e:
        return json.dumps({"error": "SecurityError", "message": str(e)})

    # Capture stdout
    old_stdout = sys.stdout
    redirected_output = StringIO()
    sys.stdout = redirected_output

    try:
        local_vars = {}
        if args:
            local_vars["args"] = args

        # Create a restricted environment for execution
        safe_builtins = {
            "abs": abs,
            "all": all,
            "any": any,
            "ascii": ascii,
            "bin": bin,
            "bool": bool,
            "bytearray": bytearray,
            "bytes": bytes,
            "chr": chr,
            "complex": complex,
            "dict": dict,
            "divmod": divmod,
            "enumerate": enumerate,
            "filter": filter,
            "float": float,
            "format": format,
            "frozenset": frozenset,
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
            "min": min,
            "next": next,
            "object": object,
            "oct": oct,
            "ord": ord,
            "pow": pow,
            "print": print,
            "range": range,
            "repr": repr,
            "reversed": reversed,
            "round": round,
            "set": set,
            "slice": slice,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "type": type,
            "zip": zip,
            "__import__": _restricted_import,
        }

        try:
            # Script is already parsed/validated above; compile directly
            compiled_code = compile(script, "<string>", "exec")

            # Execute in a restricted environment
            restricted_globals = {"__builtins__": safe_builtins}

            # pylint: disable=exec-used
            # Execute with local_vars as the locals mapping so user-provided
            # args are available to the script but do not pollute globals.
            exec(compiled_code, restricted_globals, local_vars)  # nosec B102
        except Exception as e:  # capture runtime exceptions and give diagnostics
            partial = redirected_output.getvalue()
            tb = traceback.format_exc()
            err = {
                "error": e.__class__.__name__,
                "message": str(e),
                "traceback": tb,
                "partial_output": partial,
            }
            return json.dumps(err)

        # Get the output
        output = redirected_output.getvalue()
        return output if output else "Code executed successfully (no output)"
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Unexpected errors in the wrapping logic — return structured diagnostics
        tb = traceback.format_exc()
        return json.dumps(
            {
                "error": e.__class__.__name__,
                "message": str(e),
                "traceback": tb,
            }
        )
    finally:
        sys.stdout = old_stdout  # restore


class SecurityError(Exception):  # pylint: disable=missing-class-docstring
    pass
