"""
Available Tools Registry - Maps tool names to their imports.
"""

AVAILABLE_TOOLS = {
    # ---------------------------------------------------------------
    # Core / execution
    # ---------------------------------------------------------------
    "generic_linux_command": {
        "import": "from cai.tools.reconnaissance.generic_linux_command import generic_linux_command",
        "category": "core",
        "description": "Execute any Linux command",
    },
    "execute_code": {
        "import": "from cai.tools.reconnaissance.exec_code import execute_code",
        "category": "core",
        "description": "Execute Python/other code",
    },
    "execute_python_code": {
        "import": "from cai.tools.misc.code_interpreter import execute_python_code",
        "category": "core",
        "description": "Execute Python code and capture output",
    },

    # ---------------------------------------------------------------
    # Reconnaissance
    # ---------------------------------------------------------------
    "nmap": {
        "import": "from cai.tools.reconnaissance.nmap import nmap",
        "category": "recon",
        "description": "Run nmap port/service scans",
    },
    "port_scanner": {
        "import": "from cai.tools.network.port_scanner import port_scanner",
        "category": "recon",
        "description": "Pure-Python TCP port scanner",
    },
    "dns_enum": {
        "import": "from cai.tools.reconnaissance.dns_enum import dns_enum",
        "category": "recon",
        "description": "DNS enumeration (A/MX/NS/TXT/zone transfer)",
    },
    "subdomain_bruteforce": {
        "import": "from cai.tools.reconnaissance.subdomain_bruteforce import subdomain_bruteforce",
        "category": "recon",
        "description": "Brute-force subdomain discovery via DNS",
    },
    "subdomain_takeover_checker": {
        "import": "from cai.tools.web.subdomain_takeover import subdomain_takeover_checker",
        "category": "recon",
        "description": "Detect dangling DNS / subdomain takeover opportunities",
    },
    "shodan_search": {
        "import": "from cai.tools.reconnaissance.shodan import shodan_search",
        "category": "recon",
        "description": "Search Shodan for exposed services",
    },
    "shodan_host_info": {
        "import": "from cai.tools.reconnaissance.shodan import shodan_host_info",
        "category": "recon",
        "description": "Get Shodan info for a specific host",
    },
    "secret_scanner": {
        "import": "from cai.tools.reconnaissance.secret_scanner import secret_scanner",
        "category": "recon",
        "description": "Scan files/directories for leaked secrets and credentials",
    },
    "hash_analyzer": {
        "import": "from cai.tools.misc.hash_analyzer import hash_analyzer",
        "category": "recon",
        "description": "Identify hash type and attempt cracking via hashcat/john",
    },

    # ---------------------------------------------------------------
    # Web application assessment
    # ---------------------------------------------------------------
    "fetch_url": {
        "import": "from cai.tools.web.fetch_url import fetch_url",
        "category": "web",
        "description": "Fetch a URL and return its content",
    },
    "web_request_framework": {
        "import": "from cai.tools.web.headers import web_request_framework",
        "category": "web",
        "description": "HTTP request analysis with security focus",
    },
    "dir_scanner": {
        "import": "from cai.tools.web.dir_scanner import dir_scanner",
        "category": "web",
        "description": "Web directory/file brute-force scanner",
    },
    "cors_checker": {
        "import": "from cai.tools.web.cors_checker import cors_checker",
        "category": "web",
        "description": "Check CORS misconfiguration",
    },
    "jwt_analyzer": {
        "import": "from cai.tools.web.jwt_analyzer import jwt_analyzer",
        "category": "web",
        "description": "Analyse and attack JWT tokens",
    },
    "ssl_inspector": {
        "import": "from cai.tools.web.ssl_inspector import ssl_inspector",
        "category": "web",
        "description": "Inspect TLS certificates and cipher configuration",
    },
    "tech_fingerprint": {
        "import": "from cai.tools.web.tech_fingerprint import tech_fingerprint",
        "category": "web",
        "description": "Fingerprint web technologies (CMS, framework, server)",
    },
    "waf_detector": {
        "import": "from cai.tools.web.waf_detector import waf_detector",
        "category": "web",
        "description": "Detect WAF products via HTTP fingerprinting",
    },
    "security_headers": {
        "import": "from cai.tools.web.security_headers import security_headers",
        "category": "web",
        "description": "Audit HTTP security response headers (HSTS, CSP, X-Frame-Options, etc.)",
    },
    "cookie_analyzer": {
        "import": "from cai.tools.web.cookie_analyzer import cookie_analyzer",
        "category": "web",
        "description": "Audit Set-Cookie security flags (Secure, HttpOnly, SameSite)",
    },
    "graphql_probe": {
        "import": "from cai.tools.web.graphql_probe import graphql_probe",
        "category": "web",
        "description": "Discover GraphQL endpoints and probe for introspection/batch queries",
    },
    "open_redirect": {
        "import": "from cai.tools.web.open_redirect import open_redirect",
        "category": "web",
        "description": "Test for open redirect vulnerabilities via canary injection",
    },
    "host_header_injection": {
        "import": "from cai.tools.web.host_header_injection import host_header_injection",
        "category": "web",
        "description": "Test for Host header injection (password-reset poisoning, cache poisoning)",
    },
    "http_methods": {
        "import": "from cai.tools.web.http_methods import http_methods",
        "category": "web",
        "description": "Enumerate accepted HTTP methods (PUT/DELETE/TRACE etc.)",
    },
    "path_traversal": {
        "import": "from cai.tools.web.path_traversal import path_traversal",
        "category": "web",
        "description": "Probe for path traversal / directory traversal / LFI vulnerabilities",
    },
    "s3_bucket_checker": {
        "import": "from cai.tools.web.s3_bucket_checker import s3_bucket_checker",
        "category": "web",
        "description": "Check S3 buckets for public misconfiguration (LIST, READ, ACL, policy)",
    },
    "ssrf_probe": {
        "import": "from cai.tools.web.ssrf_probe import ssrf_probe",
        "category": "web",
        "description": "Probe for SSRF by injecting cloud metadata URLs (AWS/GCP/Azure IMDS) into URL parameters",
    },
    "xxe_probe": {
        "import": "from cai.tools.web.xxe_probe import xxe_probe",
        "category": "web",
        "description": "Probe XML endpoints for XXE injection (file read, blind XXE, SSRF via entity resolution)",
    },
    "sqli_probe": {
        "import": "from cai.tools.web.sqli_probe import sqli_probe",
        "category": "web",
        "description": "Probe query-string parameters for SQL injection (error-based, boolean, time-based blind)",
    },

    # ---------------------------------------------------------------
    # Search
    # ---------------------------------------------------------------
    "make_web_search_with_explanation": {
        "import": "from cai.tools.web.search_web import make_web_search_with_explanation",
        "category": "search",
        "description": "AI-powered web search with explanation",
    },
    "google_search": {
        "import": "from cai.tools.web.google_search import google_search",
        "category": "search",
        "description": "Google search",
    },
    "google_dork_search": {
        "import": "from cai.tools.web.google_search import google_dork_search",
        "category": "search",
        "description": "Google dork search for advanced queries",
    },

    # ---------------------------------------------------------------
    # Network utilities
    # ---------------------------------------------------------------
    "netcat": {
        "import": "from cai.tools.reconnaissance.netcat import netcat",
        "category": "network",
        "description": "TCP/UDP netcat-style connection",
    },
    "netstat": {
        "import": "from cai.tools.reconnaissance.netstat import netstat",
        "category": "network",
        "description": "Display network connections",
    },
    "curl": {
        "import": "from cai.tools.reconnaissance.curl import curl",
        "category": "network",
        "description": "cURL HTTP requests",
    },
    "wget": {
        "import": "from cai.tools.reconnaissance.wget import wget",
        "category": "network",
        "description": "wget file download",
    },

    # ---------------------------------------------------------------
    # Filesystem / misc
    # ---------------------------------------------------------------
    "list_dir": {
        "import": "from cai.tools.reconnaissance.filesystem import list_dir",
        "category": "misc",
        "description": "List directory contents",
    },
    "cat_file": {
        "import": "from cai.tools.reconnaissance.filesystem import cat_file",
        "category": "misc",
        "description": "Read file contents",
    },
    "find_file": {
        "import": "from cai.tools.reconnaissance.filesystem import find_file",
        "category": "misc",
        "description": "Find files matching a pattern",
    },
    "thought": {
        "import": "from cai.tools.misc.reasoning import thought",
        "category": "misc",
        "description": "Internal reasoning scratch-pad (not shown to user)",
    },
    "write_key_findings": {
        "import": "from cai.tools.misc.reasoning import write_key_findings",
        "category": "misc",
        "description": "Persist key findings across turns",
    },
    "read_key_findings": {
        "import": "from cai.tools.misc.reasoning import read_key_findings",
        "category": "misc",
        "description": "Read previously written key findings",
    },

    # ---------------------------------------------------------------
    # C2 / lateral movement
    # ---------------------------------------------------------------
    "run_ssh_command_with_credentials": {
        "import": "from cai.tools.command_and_control.sshpass import run_ssh_command_with_credentials",
        "category": "c2",
        "description": "Run SSH command with explicit credentials",
    },
}

# Group tools by category for easy selection
TOOLS_BY_CATEGORY = {}
for tool_name, tool_info in AVAILABLE_TOOLS.items():
    category = tool_info["category"]
    if category not in TOOLS_BY_CATEGORY:
        TOOLS_BY_CATEGORY[category] = []
    TOOLS_BY_CATEGORY[category].append({
        "name": tool_name,
        "description": tool_info["description"]
    })