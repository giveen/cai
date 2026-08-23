"""Tests for S3 bucket misconfiguration checker."""
import unittest
from unittest.mock import MagicMock, patch

from cai.tools.web.s3_bucket_checker import (
    _check_acl_exposure,
    _check_policy_exposure,
    _check_public_list,
    _check_public_read,
    _check_public_write,
    _run_s3_bucket_check,
)


class TestPublicList(unittest.TestCase):
    def _patch_http(self, status, body):
        return patch("cai.tools.web.s3_bucket_checker._http", return_value=(status, body))

    def test_vulnerable_when_list_returns_200_with_xml(self):
        body = "<ListBucketResult><Key>file.txt</Key></ListBucketResult>"
        with self._patch_http(200, body):
            f = _check_public_list("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "HIGH")

    def test_safe_when_403(self):
        with self._patch_http(403, "AccessDenied"):
            f = _check_public_list("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_when_400(self):
        with self._patch_http(400, "MalformedXML"):
            f = _check_public_list("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_when_redirect(self):
        with self._patch_http(301, ""):
            f = _check_public_list("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_error_on_unexpected_status(self):
        with self._patch_http(500, "InternalError"):
            f = _check_public_list("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "ERROR")


class TestPublicRead(unittest.TestCase):
    def _patch_http(self, status, body):
        return patch("cai.tools.web.s3_bucket_checker._http", return_value=(status, body))

    def test_vulnerable_when_object_readable(self):
        with self._patch_http(200, "file content"):
            f = _check_public_read("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")

    def test_safe_when_403(self):
        with self._patch_http(403, "AccessDenied"):
            f = _check_public_read("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_when_404_nosuchkey(self):
        with self._patch_http(404, "<Error><Code>NoSuchKey</Code></Error>"):
            f = _check_public_read("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_when_404_nosuchbucket(self):
        with self._patch_http(404, "<Error><Code>NoSuchBucket</Code></Error>"):
            f = _check_public_read("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")


class TestPublicWrite(unittest.TestCase):
    def test_vulnerable_when_put_returns_200(self):
        def fake_http(method, url, body=None, timeout=8.0):
            if method == "PUT":
                return 200, ""
            return 204, ""  # DELETE
        with patch("cai.tools.web.s3_bucket_checker._http", side_effect=fake_http):
            f = _check_public_write("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "CRITICAL")

    def test_vulnerable_when_put_returns_204(self):
        def fake_http(method, url, body=None, timeout=8.0):
            if method == "PUT":
                return 204, ""
            return 204, ""
        with patch("cai.tools.web.s3_bucket_checker._http", side_effect=fake_http):
            f = _check_public_write("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")

    def test_safe_when_403(self):
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(403, "AccessDenied")):
            f = _check_public_write("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_when_405(self):
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(405, "MethodNotAllowed")):
            f = _check_public_write("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")


class TestAclExposure(unittest.TestCase):
    def test_vulnerable_with_allusers(self):
        body = (
            "<AccessControlPolicy>"
            "<Grant><Grantee><URI>http://acs.amazonaws.com/groups/global/AllUsers</URI>"
            "</Grantee><Permission>READ</Permission></Grant>"
            "</AccessControlPolicy>"
        )
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(200, body)):
            f = _check_acl_exposure("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "HIGH")

    def test_vulnerable_without_allusers_is_medium(self):
        body = "<AccessControlPolicy><Owner><ID>abc</ID></Owner></AccessControlPolicy>"
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(200, body)):
            f = _check_acl_exposure("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "MEDIUM")

    def test_safe_when_403(self):
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(403, "AccessDenied")):
            f = _check_acl_exposure("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")


class TestPolicyExposure(unittest.TestCase):
    def test_vulnerable_when_json_policy_returned(self):
        body = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":"*","Action":"s3:GetObject"}]}'
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(200, body)):
            f = _check_policy_exposure("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "VULNERABLE")

    def test_safe_when_403(self):
        with patch("cai.tools.web.s3_bucket_checker._http", return_value=(403, "AccessDenied")):
            f = _check_policy_exposure("https://test.s3.amazonaws.com/", 5.0)
        self.assertEqual(f.status, "SAFE")


class TestRunS3BucketCheck(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_s3_bucket_check("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_s3_bucket_check("   \n  ")
        self.assertIn("Error", out)

    def test_nonexistent_bucket_detected(self):
        def fake_reachable(bucket, timeout):
            return "https://test.s3.amazonaws.com/", 404, "<Error><Code>NoSuchBucket</Code></Error>"
        with patch("cai.tools.web.s3_bucket_checker._first_reachable", side_effect=fake_reachable):
            out = _run_s3_bucket_check("nonexistent-xyz-bucket")
        self.assertIn("NoSuchBucket", out)
        self.assertIn("SAFE", out)

    def test_unreachable_bucket_shows_error(self):
        with patch(
            "cai.tools.web.s3_bucket_checker._first_reachable",
            return_value=("", -1, "connection refused"),
        ):
            out = _run_s3_bucket_check("unreachable-bucket")
        self.assertIn("ERROR", out)

    def test_summary_line_present(self):
        def fake_reachable(bucket, timeout):
            return "https://test.s3.amazonaws.com/", 403, "AccessDenied"
        mock_finding = MagicMock(
            check="Public LIST", severity="HIGH", status="SAFE", detail="Denied"
        )
        with patch("cai.tools.web.s3_bucket_checker._first_reachable", side_effect=fake_reachable), \
             patch("cai.tools.web.s3_bucket_checker._check_public_list", return_value=mock_finding), \
             patch("cai.tools.web.s3_bucket_checker._check_public_read", return_value=mock_finding), \
             patch("cai.tools.web.s3_bucket_checker._check_acl_exposure", return_value=mock_finding), \
             patch("cai.tools.web.s3_bucket_checker._check_policy_exposure", return_value=mock_finding):
            out = _run_s3_bucket_check("test-bucket")
        self.assertIn("Summary:", out)

    def test_comma_separated_buckets(self):
        def fake_reachable(bucket, timeout):
            return "", -1, "unreachable"
        with patch("cai.tools.web.s3_bucket_checker._first_reachable", side_effect=fake_reachable):
            out = _run_s3_bucket_check("bucket-a, bucket-b")
        self.assertIn("bucket-a", out)
        self.assertIn("bucket-b", out)

    def test_vulnerable_shows_recommendation(self):
        def fake_reachable(bucket, timeout):
            return "https://test.s3.amazonaws.com/", 200, "<ListBucketResult/>"
        vuln = MagicMock(check="Public LIST", severity="HIGH", status="VULNERABLE", detail="Listed!")
        safe = MagicMock(check="X", severity="LOW", status="SAFE", detail="ok")
        with patch("cai.tools.web.s3_bucket_checker._first_reachable", side_effect=fake_reachable), \
             patch("cai.tools.web.s3_bucket_checker._check_public_list", return_value=vuln), \
             patch("cai.tools.web.s3_bucket_checker._check_public_read", return_value=safe), \
             patch("cai.tools.web.s3_bucket_checker._check_acl_exposure", return_value=safe), \
             patch("cai.tools.web.s3_bucket_checker._check_policy_exposure", return_value=safe):
            out = _run_s3_bucket_check("test-bucket")
        self.assertIn("Recommendation", out)

    def test_skip_write_probe_by_default(self):
        """_check_public_write should NOT be called unless skip_write=False."""
        def fake_reachable(bucket, timeout):
            return "", -1, "unreachable"
        with patch("cai.tools.web.s3_bucket_checker._first_reachable", side_effect=fake_reachable), \
             patch("cai.tools.web.s3_bucket_checker._check_public_write") as mock_write:
            _run_s3_bucket_check("test-bucket", skip_write=True)
        mock_write.assert_not_called()

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("s3_bucket_checker", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
