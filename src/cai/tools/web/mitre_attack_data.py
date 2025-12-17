import requests
from bs4 import BeautifulSoup
from typing import Dict, List
from cai.sdk.agents.tool import function_tool

@function_tool
def fetch_mitre_attack_group_info(group_id: str) -> str:
    """
    Fetch information about a specific MITRE ATT&CK group.

    Args:
        group_id (str): The MITRE ATT&CK group ID (e.g., 'G0016')

    Returns:
        str: Formatted information about the threat group
    """
    url = f"https://attack.mitre.org/groups/{group_id}/"

    try:
        response = requests.get(url)
        if response.status_code != 200:
            return f"Failed to fetch data for group {group_id}"

        soup = BeautifulSoup(response.content, "html.parser")
        info: Dict[str, str] = {}

        # Group name: fallback to <h1> or <title>
        title_elem = soup.find("h1")
        if title_elem:
            info["name"] = title_elem.get_text(strip=True)
        else:
            title_tag = soup.find("title")
            if title_tag:
                info["name"] = title_tag.get_text(strip=True)

        # Info table: look for MITRE metadata tables
        info_table = soup.find("table", class_="table-mitre")
        if info_table:
            rows = info_table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).rstrip(":")
                    value = cells[1].get_text(strip=True)
                    info[key] = value

        # Techniques used: flexible match on headings
        techniques_section = soup.find("h2", id="techniques-used") \
            or soup.find("h2", string=lambda s: s and "Technique" in s)
        if techniques_section:
            techniques_table = techniques_section.find_next("table")
            if techniques_table:
                techniques: List[str] = []
                rows = techniques_table.find_all("tr")[1:]  # skip header
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 3:
                        technique_id = cols[1].get_text(strip=True)
                        technique_name = cols[2].get_text(strip=True)
                        use_desc = cols[3].get_text(strip=True) if len(cols) > 3 else ""
                        techniques.append(f"- {technique_id}: {technique_name} ({use_desc})")
                info["techniques_used"] = "\n".join(techniques)

        # Software used
        software_section = soup.find("h2", id="software") \
            or soup.find("h2", string=lambda s: s and "Software" in s)
        if software_section:
            software_table = software_section.find_next("table")
            if software_table:
                software: List[str] = []
                rows = software_table.find_all("tr")[1:]  # skip header
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        soft_id = cols[0].get_text(strip=True)
                        soft_name = cols[1].get_text(strip=True)
                        software.append(f"- {soft_id}: {soft_name}")
                info["software_used"] = "\n".join(software)

        # Format output
        result: List[str] = []
        for key, value in info.items():
            if value:
                result.append(f"{key}: {value}")

        return "\n\n".join(result) if result else f"No information found for group {group_id}"

    except Exception as e:
        return f"Error fetching MITRE ATT&CK data: {str(e)}"


@function_tool
def search_mitre_attack_groups(query: str) -> str:
    """
    Search for MITRE ATT&CK groups by name or description.

    Args:
        query (str): Search term

    Returns:
        str: Formatted information about matching groups
    """
    # Placeholder: requires implementation of search parsing
    return f"Search functionality for '{query}' requires additional implementation"
