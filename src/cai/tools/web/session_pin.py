"""
session_pin tool — pin / inspect / unpin a session cookie globally.

Once a cookie is pinned every subsequent network-based tool call
(curl, wget, sqlmap, cewl, web_request_framework …) will automatically
include it without the agent having to copy-paste it into every command.
"""

from cai.sdk.agents import function_tool
from cai.util.orchestration import (
    clear_pinned_session,
    get_pinned_cookie,
    pin_session_cookie,
)


@function_tool
def set_session_cookie(cookie: str) -> str:
    """
    Pin a session cookie so it is automatically injected into every
    subsequent network tool call (curl, wget, sqlmap, cewl,
    web_request_framework, etc.).

    Accepts the standard ``name=value; name2=value2`` cookie format.
    Multiple cookies can be pinned in one call; calling this again adds
    to (or updates) the existing pinned set — pinned values from a
    previous call are preserved unless overwritten by the same key.

    Args:
        cookie: Cookie string in ``name=value`` or
                ``name=value; name2=value2`` format.
                Example: "PHPSESSID=abc123def456"
                Example: "session=xyz; csrftoken=tok123"

    Returns:
        str: Confirmation message showing all currently pinned cookies.

    Examples:
        set_session_cookie("PHPSESSID=abc123def456")
        set_session_cookie("session=xyz789; remember=1")
    """
    return pin_session_cookie(cookie)


@function_tool
def get_pinned_session_cookie() -> str:
    """
    Show the currently pinned session cookie (if any).

    Returns:
        str: The cookie string that is being auto-injected into network
             calls, or a message indicating nothing is pinned.
    """
    cookie = get_pinned_cookie()
    if cookie:
        return f"Pinned cookie: {cookie}"
    return "No session cookie is currently pinned."


@function_tool
def unpin_session_cookie() -> str:
    """
    Remove the pinned session cookie so network tools revert to their
    default behaviour (no auto-injected cookie).

    Returns:
        str: Confirmation message.
    """
    return clear_pinned_session()
