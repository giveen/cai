"""Tests for the secret scanner tool."""

import pytest
from cai.tools.reconnaissance.secret_scanner import (
    _run_scan,
    _shannon_entropy,
    _find_high_entropy_strings,
)


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def test_shannon_entropy_empty():
    assert _shannon_entropy("") == 0.0


def test_shannon_entropy_uniform():
    assert _shannon_entropy("aaaa") == 0.0


def test_shannon_entropy_high():
    token = "A8kQxZpL3mNvR7bY2wCdJtHgF1oUeI5s"
    assert _shannon_entropy(token) > 4.0


# ---------------------------------------------------------------------------
# _run_scan on raw text strings
# ---------------------------------------------------------------------------

def test_detects_aws_access_key():
    text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    result = _run_scan(text)
    assert "AWS Access Key" in result
    assert "critical" in result.lower()


def test_detects_github_pat():
    # ghp_ + exactly 36 alphanumeric chars
    text = "token=ghp_AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
    result = _run_scan(text)
    assert "GitHub Personal Access Token" in result


def test_detects_private_key_header():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK..."
    result = _run_scan(text)
    assert "Private Key" in result


def test_detects_password_in_url():
    text = "postgres://admin:s3cr3tP@ssw0rd@db.example.com/mydb"
    result = _run_scan(text)
    assert "Password in URL" in result or "Database Connection String" in result


def test_detects_jwt():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    text = f"Authorization: Bearer {jwt}"
    result = _run_scan(text)
    assert "JWT" in result or "Bearer" in result


def test_detects_stripe_secret_key():
    # Deliberately split so push-protection scanners do not flag this test file.
    prefix = "sk_live_"
    fake_chars = "XXXXXXXXXXXXXXXXXXXXXXXX"
    text = f"STRIPE_SECRET_KEY={prefix}{fake_chars}"
    result = _run_scan(text)
    assert "Stripe" in result


def test_detects_generic_password_assignment():
    text = 'password="MySuperS3cret!@#"'
    result = _run_scan(text)
    assert "Password" in result or "password" in result.lower()


def test_no_findings_clean_text():
    text = "Hello world, this is a clean string with no secrets."
    result = _run_scan(text)
    assert "No secrets detected" in result


def test_high_entropy_flag():
    text = "token=A8kQxZpL3mNvR7bY2wCdJtHgF1oUeI5sXyZzPqRwMn"
    result_no_entropy = _run_scan(text, include_entropy=False)
    result_with_entropy = _run_scan(text, include_entropy=True)
    assert len(result_with_entropy) >= len(result_no_entropy)


def test_max_findings_cap():
    text = "\n".join([f"AKIA{'A' * 16}_{i}" for i in range(100)])
    result = _run_scan(text, max_findings=5)
    assert "capped at 5" in result


def test_nonexistent_path_treated_as_text():
    text = "/tmp/totally/fake/path/that/doesnt/exist"
    result = _run_scan(text)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _run_scan on actual files (tmp)
# ---------------------------------------------------------------------------

def test_scan_file(tmp_path):
    secret_file = tmp_path / "config.env"
    secret_file.write_text(
        'AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n'
        'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n'
        'NORMAL_VAR=hello\n'
    )
    result = _run_scan(str(secret_file))
    assert "AWS Access Key" in result
    assert str(secret_file) in result


def test_scan_directory(tmp_path):
    (tmp_path / "creds.txt").write_text("ghp_AbCdEfGhIjKlMnOpQrStUvWxYz1234567890\n")
    (tmp_path / "readme.txt").write_text("No secrets here.\n")
    result = _run_scan(str(tmp_path))
    assert "GitHub Personal Access Token" in result


def test_binary_files_skipped(tmp_path):
    bin_file = tmp_path / "data.bin"
    bin_file.write_bytes(b"\x00\x01\x02AKIAIOSFODNN7EXAMPLE\x03")
    result = _run_scan(str(tmp_path))
    assert "No secrets detected" in result or "AKIA" not in result


def test_large_file_skipped(tmp_path):
    large_file = tmp_path / "big.txt"
    large_file.write_bytes(b"AKIAIOSFODNN7EXAMPLE" * 30000)
    result = _run_scan(str(large_file))
    assert "too large" in result.lower() or "No secrets" in result
