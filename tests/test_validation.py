import socket

import pytest

from cai.tools import validation


def test_contains_shell_metacharacters():
    assert validation.contains_shell_metacharacters("; rm -rf /")
    assert not validation.contains_shell_metacharacters("example.com")


def test_contains_cmd_injection():
    assert validation.contains_cmd_injection("echo hello && echo bye")
    assert not validation.contains_cmd_injection("safe-string")


def test_is_url_safe():
    assert validation.is_url_safe("https://example.com/path")
    assert not validation.is_url_safe("bad;url")


def test_is_valid_target_and_host():
    assert validation.is_valid_target("127.0.0.1")
    assert validation.is_valid_target("example.com")
    assert not validation.is_valid_target("not an ip")

    assert validation.is_valid_host("127.0.0.1")
    # IPv6 support can be optional in some CI/build environments.
    # If the interpreter/platform lacks IPv6 support, skip this assertion.
    if getattr(socket, "has_ipv6", False):
        assert validation.is_valid_host("::1")
    else:
        pytest.skip("IPv6 not available on this platform")


def test_has_disallowed_nc_flags_and_filename():
    assert validation.has_disallowed_nc_flags("-e /bin/sh")
    assert not validation.has_disallowed_nc_flags("-z 1234")

    assert validation.is_valid_filename("script_py")
    # Use a non-empty repeated string to test length limits ("" * 100 == "")
    assert not validation.is_valid_filename("a" * 100)


def test_validate_args_no_injection():
    assert validation.validate_args_no_injection("safe", name="args") is None
    assert validation.validate_args_no_injection("bad; rm", name="args") is not None
    assert validation.validate_args_no_injection("longish", name="args", max_length=2) is not None
