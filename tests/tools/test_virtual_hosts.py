"""
Unit tests for the VirtualHostsManager (dns_proxy.py).

All file I/O is performed inside a temporary directory so the tests are
hermetic and do not touch the real workspace.
"""

import os
import importlib
import sys
import tempfile
import textwrap
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vhm(tmp_dir):
    """Return a VirtualHostsManager rooted in *tmp_dir*."""
    from cai.tools.network.dns_proxy import VirtualHostsManager

    return VirtualHostsManager(workspace_dir=tmp_dir)


# ---------------------------------------------------------------------------
# _validate_hostname / _validate_ip
# ---------------------------------------------------------------------------


class TestValidators:
    def test_valid_hostname(self):
        from cai.tools.network.dns_proxy import _validate_hostname

        assert _validate_hostname("principal.htb") is None
        assert _validate_hostname("sub.principal.htb") is None
        assert _validate_hostname("a") is None

    def test_invalid_hostname_empty(self):
        from cai.tools.network.dns_proxy import _validate_hostname

        assert _validate_hostname("") is not None
        assert _validate_hostname("   ") is not None

    def test_invalid_hostname_injection_chars(self):
        from cai.tools.network.dns_proxy import _validate_hostname

        assert _validate_hostname("bad;host") is not None
        assert _validate_hostname("bad host") is not None
        assert _validate_hostname("host$(rm)") is not None

    def test_valid_ipv4(self):
        from cai.tools.network.dns_proxy import _validate_ip

        assert _validate_ip("10.10.11.42") is None
        assert _validate_ip("192.168.1.1") is None

    def test_valid_ipv6(self):
        from cai.tools.network.dns_proxy import _validate_ip

        assert _validate_ip("::1") is None
        assert _validate_ip("2001:db8::1") is None

    def test_invalid_ip(self):
        from cai.tools.network.dns_proxy import _validate_ip

        assert _validate_ip("not-an-ip") is not None
        assert _validate_ip("999.999.999.999") is not None
        assert _validate_ip("") is not None


# ---------------------------------------------------------------------------
# VirtualHostsManager — hosts file management
# ---------------------------------------------------------------------------


class TestHostsFile:
    def test_get_hosts_path(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        assert vhm.get_hosts_path() == str(tmp_path / "hosts.txt")

    def test_add_and_list(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        result = vhm.add_host("principal.htb", "10.10.11.42")
        assert "Added" in result
        entries = vhm.list_hosts()
        assert len(entries) == 1
        assert entries[0] == {"ip": "10.10.11.42", "hostname": "principal.htb"}

    def test_add_updates_existing(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("principal.htb", "10.10.11.1")
        vhm.add_host("principal.htb", "10.10.11.42")  # update
        entries = vhm.list_hosts()
        ips = [e["ip"] for e in entries if e["hostname"] == "principal.htb"]
        assert ips == ["10.10.11.42"]

    def test_add_multiple_hosts(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("principal.htb", "10.10.11.42")
        vhm.add_host("dev.principal.htb", "10.10.11.42")
        entries = vhm.list_hosts()
        hostnames = {e["hostname"] for e in entries}
        assert hostnames == {"principal.htb", "dev.principal.htb"}

    def test_remove_existing(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("principal.htb", "10.10.11.42")
        result = vhm.remove_host("principal.htb")
        assert "Removed" in result
        assert vhm.list_hosts() == []

    def test_remove_nonexistent(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        result = vhm.remove_host("ghost.htb")
        assert "Not found" in result

    def test_round_trip_persists_to_disk(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("box.htb", "10.10.20.5")
        # Re-create instance pointing at the same dir — should read from disk
        vhm2 = _make_vhm(str(tmp_path))
        entries = vhm2.list_hosts()
        assert any(e["hostname"] == "box.htb" for e in entries)

    def test_invalid_hostname_rejected(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        result = vhm.add_host("bad host!", "10.0.0.1")
        assert "Error" in result
        assert vhm.list_hosts() == []

    def test_invalid_ip_rejected(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        result = vhm.add_host("good.htb", "not-an-ip")
        assert "Error" in result
        assert vhm.list_hosts() == []

    def test_hosts_file_format(self, tmp_path):
        """The written file should be parseable as a standard hosts file."""
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("box.htb", "10.10.20.5")
        content = (tmp_path / "hosts.txt").read_text()
        # Should contain the IP and hostname on the same line
        assert "10.10.20.5" in content
        assert "box.htb" in content


# ---------------------------------------------------------------------------
# VirtualHostsManager — wrap_command
# ---------------------------------------------------------------------------


class TestWrapCommand:
    def _vhm_with_hosts(self, tmp_path):
        vhm = _make_vhm(str(tmp_path))
        vhm.add_host("target.htb", "10.10.11.1")
        return vhm

    def test_wraps_nmap(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true"}):
            vhm = self._vhm_with_hosts(tmp_path)
            wrapped = vhm.wrap_command("nmap -sV target.htb")
            assert "LD_PRELOAD=" in wrapped
            assert "NSS_WRAPPER_HOSTS=" in wrapped
            assert "nmap -sV target.htb" in wrapped

    def test_wraps_curl(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true"}):
            vhm = self._vhm_with_hosts(tmp_path)
            wrapped = vhm.wrap_command("curl http://target.htb/")
            assert "LD_PRELOAD=" in wrapped
            assert "curl http://target.htb/" in wrapped

    def test_no_wrap_when_disabled(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "false"}):
            vhm = self._vhm_with_hosts(tmp_path)
            cmd = "nmap -sV target.htb"
            assert vhm.wrap_command(cmd) == cmd

    def test_no_wrap_inside_container(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(
            os.environ, {"CAI_ACTIVE_CONTAINER": "abc123", "CAI_VHOST_WRAP": "true"}
        ):
            vhm = self._vhm_with_hosts(tmp_path)
            cmd = "nmap -sV target.htb"
            assert vhm.wrap_command(cmd) == cmd

    def test_no_wrap_when_library_absent(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper", return_value=None
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true"}):
            vhm = self._vhm_with_hosts(tmp_path)
            cmd = "nmap -sV target.htb"
            assert vhm.wrap_command(cmd) == cmd

    def test_no_wrap_when_no_hosts(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true"}):
            vhm = _make_vhm(str(tmp_path))  # no hosts added
            cmd = "nmap -sV target.htb"
            assert vhm.wrap_command(cmd) == cmd

    def test_no_wrap_for_non_network_tool(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(os.environ, {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true"}):
            vhm = self._vhm_with_hosts(tmp_path)
            cmd = "ls -la /tmp"
            assert vhm.wrap_command(cmd) == cmd

    def test_custom_hostname_env(self, tmp_path):
        with mock.patch(
            "cai.tools.network.dns_proxy._find_libnss_wrapper",
            return_value="/usr/lib/libnss_wrapper.so",
        ), mock.patch.dict(
            os.environ,
            {"CAI_ACTIVE_CONTAINER": "", "CAI_VHOST_WRAP": "true", "CAI_VHOST_HOSTNAME": "my-node"},
        ):
            vhm = self._vhm_with_hosts(tmp_path)
            wrapped = vhm.wrap_command("nmap 10.10.11.1")
            assert "NSS_WRAPPER_HOSTNAME=my-node" in wrapped


# ---------------------------------------------------------------------------
# Function tools (smoke tests — call underlying VHM methods directly)
# ---------------------------------------------------------------------------


class TestFunctionTools:
    def test_add_virtual_host_tool(self, tmp_path, monkeypatch):
        from cai.tools.network import dns_proxy

        vhm = dns_proxy.VirtualHostsManager(str(tmp_path))
        monkeypatch.setattr(dns_proxy, "_VHM_INSTANCE", vhm)
        result = vhm.add_host("target.htb", "10.10.11.5")
        assert "Added" in result

    def test_remove_virtual_host_tool(self, tmp_path, monkeypatch):
        from cai.tools.network import dns_proxy

        vhm = dns_proxy.VirtualHostsManager(str(tmp_path))
        vhm.add_host("target.htb", "10.10.11.5")
        monkeypatch.setattr(dns_proxy, "_VHM_INSTANCE", vhm)
        result = vhm.remove_host("target.htb")
        assert "Removed" in result

    def test_list_virtual_hosts_empty(self, tmp_path, monkeypatch):
        from cai.tools.network import dns_proxy

        vhm = dns_proxy.VirtualHostsManager(str(tmp_path))
        monkeypatch.setattr(dns_proxy, "_VHM_INSTANCE", vhm)
        entries = vhm.list_hosts()
        assert entries == []

    def test_list_virtual_hosts_with_entries(self, tmp_path, monkeypatch):
        from cai.tools.network import dns_proxy

        vhm = dns_proxy.VirtualHostsManager(str(tmp_path))
        vhm.add_host("box.htb", "10.10.20.1")
        monkeypatch.setattr(dns_proxy, "_VHM_INSTANCE", vhm)
        entries = vhm.list_hosts()
        hostnames = [e["hostname"] for e in entries]
        ips = [e["ip"] for e in entries]
        assert "box.htb" in hostnames
        assert "10.10.20.1" in ips

    def test_check_nss_wrapper_tool(self, tmp_path, monkeypatch):
        from cai.tools.network import dns_proxy

        vhm = dns_proxy.VirtualHostsManager(str(tmp_path))
        monkeypatch.setattr(dns_proxy, "_VHM_INSTANCE", vhm)
        # Just verify is_available returns a bool (library may or may not be installed)
        assert isinstance(vhm.is_available(), bool)
