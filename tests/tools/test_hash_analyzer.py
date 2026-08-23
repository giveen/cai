"""Tests for the hash_analyzer tool."""

import hashlib
import pytest
from unittest.mock import patch, MagicMock

from cai.tools.misc.hash_analyzer import (
    _identify_hash,
    _compute_hashes,
    _try_crack,
    _run_hash_analyze,
)


# ---------------------------------------------------------------------------
# _identify_hash
# ---------------------------------------------------------------------------

def test_identify_md5():
    # MD5 is 32 hex chars
    candidates = _identify_hash("d41d8cd98f00b204e9800998ecf8427e")
    assert "MD5" in candidates


def test_identify_sha1():
    candidates = _identify_hash("da39a3ee5e6b4b0d3255bfef95601890afd80709")
    assert "SHA-1" in candidates


def test_identify_sha256():
    candidates = _identify_hash("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert "SHA-256" in candidates


def test_identify_sha512():
    h = hashlib.sha512(b"").hexdigest()
    candidates = _identify_hash(h)
    assert "SHA-512" in candidates


def test_identify_bcrypt():
    bcrypt_hash = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"
    candidates = _identify_hash(bcrypt_hash)
    assert "bcrypt" in candidates


def test_identify_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    candidates = _identify_hash(jwt)
    assert "JWT" in candidates


def test_identify_unknown():
    candidates = _identify_hash("not-a-valid-hash-format!@#")
    assert candidates == []


def test_identify_strips_whitespace():
    # SHA-256 with leading/trailing spaces
    h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    candidates = _identify_hash(f"  {h}  ")
    assert "SHA-256" in candidates


# ---------------------------------------------------------------------------
# _compute_hashes
# ---------------------------------------------------------------------------

def test_compute_hashes_empty():
    hashes = _compute_hashes("")
    assert hashes["MD5"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert hashes["SHA-1"] == "da39a3ee5e6b4b0d3255bfef95601890afd80709"


def test_compute_hashes_known_text():
    hashes = _compute_hashes("hello")
    assert hashes["MD5"] == "5d41402abc4b2a76b9719d911017c592"
    assert hashes["SHA-256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_compute_hashes_keys():
    hashes = _compute_hashes("test")
    assert set(hashes.keys()) == {"MD5", "SHA-1", "SHA-256", "SHA-512"}


# ---------------------------------------------------------------------------
# _run_hash_analyze — identify mode
# ---------------------------------------------------------------------------

def test_run_identify_md5():
    result = _run_hash_analyze("d41d8cd98f00b204e9800998ecf8427e")
    assert "MD5" in result
    assert "Possible hash type" in result


def test_run_identify_unknown():
    result = _run_hash_analyze("not-a-hash")
    assert "not recognized" in result.lower() or "not recognized" in result


def test_run_identify_empty():
    result = _run_hash_analyze("")
    assert "Error" in result or "empty" in result


def test_run_identify_bcrypt():
    bcrypt_hash = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW"
    result = _run_hash_analyze(bcrypt_hash)
    assert "bcrypt" in result.lower()


# ---------------------------------------------------------------------------
# _run_hash_analyze — compute mode
# ---------------------------------------------------------------------------

def test_run_compute_mode():
    result = _run_hash_analyze("hello", mode="compute")
    assert "5d41402abc4b2a76b9719d911017c592" in result  # MD5 of "hello"
    assert "MD5" in result


def test_run_compute_mode_includes_sha256():
    result = _run_hash_analyze("world", mode="compute")
    assert "SHA-256" in result
    expected = hashlib.sha256(b"world").hexdigest()
    assert expected in result


# ---------------------------------------------------------------------------
# _try_crack — mocked
# ---------------------------------------------------------------------------

def test_try_crack_no_tools():
    with patch("shutil.which", return_value=None):
        result = _try_crack("d41d8cd98f00b204e9800998ecf8427e", "/wordlist.txt", 0)
    assert "not found" in result.lower() or "Neither" in result


def test_try_crack_missing_wordlist():
    # Even if hashcat is present, missing wordlist should return early
    result = _try_crack("d41d8cd98f00b204e9800998ecf8427e", "/nonexistent_wordlist.txt", 0)
    assert "not found" in result.lower() or "Wordlist" in result
