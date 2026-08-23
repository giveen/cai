"""Tests for GraphQL introspection and misconfiguration probe."""
import unittest
from unittest.mock import patch, call

from cai.tools.web.graphql_probe import (
    GraphQLFinding,
    GraphQLResult,
    _check_batch_queries,
    _check_depth_limit,
    _check_error_verbosity,
    _check_introspection,
    _find_endpoint,
    _probe,
    _run_graphql_probe,
)


class TestCheckIntrospection(unittest.TestCase):
    def test_vulnerable_when_schema_returned(self):
        schema_body = (
            '{"data":{"__schema":{"queryType":{"name":"Query"},'
            '"mutationType":null,"types":[{"name":"Query","kind":"OBJECT"}]}}}'
        )
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, schema_body)):
            f = _check_introspection("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "HIGH")
        self.assertIn("schema exposed", f.detail)

    def test_safe_when_introspection_blocked(self):
        body = '{"errors":[{"message":"GraphQL introspection is not allowed"}]}'
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, body)):
            f = _check_introspection("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_safe_on_403(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(403, "Forbidden")):
            f = _check_introspection("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_error_on_connection_failure(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(-1, "Connection refused")):
            f = _check_introspection("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "ERROR")

    def test_mutation_mention_when_mutation_type_present(self):
        schema_body = (
            '{"data":{"__schema":{"queryType":{"name":"Query"},'
            '"mutationType":{"name":"Mutation"},'
            '"types":[{"name":"Query","kind":"OBJECT"},{"name":"Mutation","kind":"OBJECT"}]}}}'
        )
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, schema_body)):
            f = _check_introspection("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertIn("Mutations", f.detail)


class TestCheckBatchQueries(unittest.TestCase):
    def test_vulnerable_when_batch_accepted(self):
        body = '[{"data":{"__typename":"Query"}},{"data":{"__typename":"Query"}}]'
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, body)):
            f = _check_batch_queries("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "VULNERABLE")
        self.assertEqual(f.severity, "MEDIUM")

    def test_safe_when_batch_rejected(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(400, '{"error":"batch not supported"}')):
            f = _check_batch_queries("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_error_on_connection_failure(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(-1, "timeout")):
            f = _check_batch_queries("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "ERROR")


class TestCheckErrorVerbosity(unittest.TestCase):
    def test_exposed_when_stack_trace_in_response(self):
        body = '{"errors":[{"message":"Exception at line 42 in resolver.py","extensions":{"stacktrace":["..."]}]}]}'
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, body)):
            f = _check_error_verbosity("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "EXPOSED")
        self.assertEqual(f.severity, "LOW")

    def test_safe_when_generic_error(self):
        body = '{"errors":[{"message":"Something went wrong"}]}'
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, body)):
            f = _check_error_verbosity("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "SAFE")

    def test_error_on_connection_failure(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(-1, "err")):
            f = _check_error_verbosity("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "ERROR")


class TestCheckDepthLimit(unittest.TestCase):
    def test_vulnerable_when_nested_query_accepted(self):
        body = '{"data":{"__type":{"fields":[{"type":{"fields":[{"type":{"fields":[{"name":"id"}]}}]}}]}}}'
        with patch("cai.tools.web.graphql_probe._post", return_value=(200, body)):
            f = _check_depth_limit("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "VULNERABLE")

    def test_safe_when_depth_rejected(self):
        with patch("cai.tools.web.graphql_probe._post", return_value=(400, '{"errors":[{"message":"Max depth exceeded"}]}')):
            f = _check_depth_limit("https://api.example.com/graphql", 5.0)
        self.assertEqual(f.status, "SAFE")


class TestFindEndpoint(unittest.TestCase):
    def test_finds_endpoint_via_get(self):
        def fake_get(url, timeout=8.0):
            if url.endswith("/graphql"):
                return 200, "Welcome to GraphiQL"
            return 404, ""

        def fake_post(url, body, timeout=8.0):
            return 404, ""

        with patch("cai.tools.web.graphql_probe._get", side_effect=fake_get), \
             patch("cai.tools.web.graphql_probe._post", side_effect=fake_post):
            result = _find_endpoint("https://api.example.com", ["/graphql", "/gql"], 5.0)
        self.assertEqual(result, "https://api.example.com/graphql")

    def test_finds_endpoint_via_post(self):
        def fake_get(url, timeout=8.0):
            return 404, ""

        def fake_post(url, body, timeout=8.0):
            if url.endswith("/graphql"):
                return 200, '{"data":{"__typename":"Query"}}'
            return 404, ""

        with patch("cai.tools.web.graphql_probe._get", side_effect=fake_get), \
             patch("cai.tools.web.graphql_probe._post", side_effect=fake_post):
            result = _find_endpoint("https://api.example.com", ["/graphql", "/gql"], 5.0)
        self.assertEqual(result, "https://api.example.com/graphql")

    def test_returns_none_when_no_endpoint_found(self):
        with patch("cai.tools.web.graphql_probe._get", return_value=(404, "")), \
             patch("cai.tools.web.graphql_probe._post", return_value=(404, "")):
            result = _find_endpoint("https://api.example.com", ["/graphql"], 5.0)
        self.assertIsNone(result)


class TestProbe(unittest.TestCase):
    def test_not_found_when_no_endpoint(self):
        with patch("cai.tools.web.graphql_probe._find_endpoint", return_value=None):
            result = _probe("https://api.example.com")
        self.assertFalse(result.endpoint_found)
        self.assertEqual(result.findings[0].status, "NOT_FOUND")

    def test_scheme_added_when_missing(self):
        captured = []

        def fake_find(base_url, paths, timeout):
            captured.append(base_url)
            return None

        with patch("cai.tools.web.graphql_probe._find_endpoint", side_effect=fake_find):
            _probe("api.example.com")
        self.assertTrue(captured[0].startswith("https://"))

    def test_endpoint_found_runs_checks(self):
        safe_finding = GraphQLFinding("X", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.graphql_probe._find_endpoint", return_value="https://api.example.com/graphql"), \
             patch("cai.tools.web.graphql_probe._check_introspection", return_value=safe_finding), \
             patch("cai.tools.web.graphql_probe._check_batch_queries", return_value=safe_finding), \
             patch("cai.tools.web.graphql_probe._check_error_verbosity", return_value=safe_finding), \
             patch("cai.tools.web.graphql_probe._check_depth_limit", return_value=safe_finding):
            result = _probe("https://api.example.com")
        self.assertTrue(result.endpoint_found)
        self.assertEqual(len(result.findings), 4)


class TestRunGraphQLProbe(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_graphql_probe("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_graphql_probe("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        with patch("cai.tools.web.graphql_probe._probe") as mock_probe:
            mock_probe.return_value = GraphQLResult(
                url="https://api.example.com/graphql",
                endpoint_found=False,
                findings=[GraphQLFinding("Endpoint discovery", "INFO", "NOT_FOUND", "not found")],
            )
            out = _run_graphql_probe("https://api.example.com")
        self.assertIn("Summary:", out)

    def test_comma_separated_targets(self):
        not_found = GraphQLResult(
            url="", endpoint_found=False,
            findings=[GraphQLFinding("X", "INFO", "NOT_FOUND", "none")]
        )
        with patch("cai.tools.web.graphql_probe._probe", return_value=not_found) as mock_probe:
            _run_graphql_probe("https://a.com, https://b.com")
        self.assertEqual(mock_probe.call_count, 2)

    def test_custom_path_syntax(self):
        safe_finding = GraphQLFinding("X", "INFO", "SAFE", "ok")
        result = GraphQLResult(
            url="https://api.example.com/internal/gql",
            endpoint_found=True,
            findings=[safe_finding],
        )
        with patch("cai.tools.web.graphql_probe._probe", return_value=result) as mock_probe:
            _run_graphql_probe("https://api.example.com|/internal/gql")
        call_kwargs = mock_probe.call_args
        self.assertEqual(call_kwargs[0][2], "/internal/gql")

    def test_note_shown_when_vulnerable(self):
        vuln_finding = GraphQLFinding("Introspection", "HIGH", "VULNERABLE", "exposed")
        result = GraphQLResult(
            url="https://api.example.com/graphql",
            endpoint_found=True,
            findings=[vuln_finding],
        )
        with patch("cai.tools.web.graphql_probe._probe", return_value=result):
            out = _run_graphql_probe("https://api.example.com")
        self.assertIn("Note:", out)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("graphql_probe", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
