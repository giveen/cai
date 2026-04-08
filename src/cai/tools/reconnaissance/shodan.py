"""
Shodan search utility for reconnaissance.

This module provides functions to search Shodan for information about hosts,
services, and vulnerabilities using the Shodan API.
"""
import os
import requests
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from cai.sdk.agents import function_tool
import ipaddress


# Default HTTP timeout for Shodan API calls
_SHODAN_HTTP_TIMEOUT = 10


@function_tool
def shodan_search(query: str, limit: int = 10) -> str:
    """
    Search Shodan for information based on the provided query.

    Args:
        query (str): The Shodan search query.
        limit (int): Maximum number of results to return. Default is 10.

    Returns:
        str: A formatted string containing the search results.
    """
    # Basic validation
    if not query or not isinstance(query, str):
        return "Error: query must be a non-empty string"
    if '\n' in query or '\r' in query:
        return "Error: query contains invalid newline characters"
    if len(query) > 500:
        return "Error: query too long (max 500 chars)"

    try:
        results = _perform_shodan_search(query, limit)
    except Exception as e:
        return f"Shodan search error: {str(e)}"

    if not results:
        return "No results found."

    formatted_results = []
    for result in results:
        ip = result.get('ip_str', 'N/A')
        port = result.get('port', 'N/A')
        org = result.get('org', 'N/A')
        hostnames = ', '.join(result.get('hostnames', ['N/A']))
        country = result.get('location', {}).get('country_name', 'N/A')

        banner = ''
        if 'data' in result and result['data']:
            raw = result['data']
            banner = (raw[:400] + '...') if len(raw) > 400 else raw

        block = f"IP: {ip}\nPort: {port}\nOrganization: {org}\nHostnames: {hostnames}\nCountry: {country}\n"
        if banner:
            block += f"Banner: {banner}\n"
        formatted_results.append(block)

    return "\n\n".join(formatted_results)

@function_tool
def shodan_host_info(ip: str) -> str:
    """
    Get detailed information about a specific host from Shodan.

    Args:
        ip (str): The IP address of the host.

    Returns:
        str: A formatted string containing host information.
    """
    # Validate IP
    try:
        ipaddress.ip_address(ip)
    except Exception:
        return f"Error: invalid IP address '{ip}'"

    try:
        result = _get_shodan_host_info(ip)
    except Exception as e:
        return f"Shodan host lookup error: {str(e)}"

    if not result:
        return f"No information found for IP {ip}."

    formatted_result = [
        f"IP: {result.get('ip_str', 'N/A')}",
        f"Organization: {result.get('org', 'N/A')}",
        f"Operating System: {result.get('os', 'N/A')}",
        f"Country: {result.get('country_name', 'N/A')}",
        f"City: {result.get('city', 'N/A')}",
        f"ISP: {result.get('isp', 'N/A')}",
        f"Last Update: {result.get('last_update', 'N/A')}",
        f"Hostnames: {', '.join(result.get('hostnames', ['N/A']))}",
        f"Domains: {', '.join(result.get('domains', ['N/A']))}",
    ]

    if 'ports' in result:
        formatted_result.append(f"Open Ports: {', '.join(map(str, result['ports']))}")

    if 'vulns' in result:
        formatted_result.append("Vulnerabilities:")
        for vuln in result['vulns']:
            formatted_result.append(f"- {vuln}")

    return "\n".join(formatted_result)


def _perform_shodan_search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Helper function to perform Shodan searches.

    Args:
        query (str): The Shodan search query.
        limit (int): Maximum number of results to return.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing the search results.
    """
    load_dotenv()
    api_key = os.getenv("SHODAN_API_KEY")

    if not api_key:
        raise RuntimeError("Shodan API key (SHODAN_API_KEY) is not configured")

    base_url = "https://api.shodan.io/shodan/host/search"

    params = {
        "key": api_key,
        "query": query,
        "limit": min(max(1, int(limit)), 100)  # enforce sensible bounds
    }

    try:
        response = requests.get(base_url, params=params, timeout=_SHODAN_HTTP_TIMEOUT, headers={"User-Agent": "CAI-Shodan-Client/1.0"})

        if response.status_code != 200:
            try:
                err = response.json().get('error', '')
            except Exception:
                err = response.text or ''
            raise RuntimeError(f"Shodan API error {response.status_code}: {err}")

        data = response.json()

        matches = data.get("matches", [])
        return matches[: params["limit"]]

    except requests.RequestException as e:
        raise RuntimeError(f"Network error when contacting Shodan: {str(e)}")


def _get_shodan_host_info(ip: str) -> Optional[Dict[str, Any]]:
    """
    Helper function to get host information from Shodan.

    Args:
        ip (str): The IP address of the host.

    Returns:
        Optional[Dict[str, Any]]: A dictionary containing host information or None if an error occurs.
    """
    load_dotenv()
    api_key = os.getenv("SHODAN_API_KEY")

    if not api_key:
        raise RuntimeError("Shodan API key (SHODAN_API_KEY) is not configured")

    base_url = f"https://api.shodan.io/shodan/host/{ip}"

    params = {"key": api_key}

    try:
        response = requests.get(base_url, params=params, timeout=_SHODAN_HTTP_TIMEOUT, headers={"User-Agent": "CAI-Shodan-Client/1.0"})
        if response.status_code != 200:
            try:
                err = response.json().get('error', '')
            except Exception:
                err = response.text or ''
            raise RuntimeError(f"Shodan API error {response.status_code}: {err}")
        return response.json()
    except requests.RequestException as e:
        raise RuntimeError(f"Network error when contacting Shodan: {str(e)}")
