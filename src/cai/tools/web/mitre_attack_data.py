import requests                                                                                                                                                                     
from bs4 import BeautifulSoup                                                                                                                                                       
from typing import Dict, List, Optional                                                                                                                                             
from cai.sdk.agents.tool import function_tool                                                                                                                                       
                                                                                                                                                                                    
@function_tool                                                                                                                                                                      
def fetch_mitre_attack_group_info(group_id: str) -> str:                                                                                                                            
    """                                                                                                                                                                             
    Fetch information about a specific MITRE ATT&CK group.                                                                                                                          
                                                                                                                                                                                    
    Args:                                                                                                                                                                           
        group_id (str): The MITRE ATT&CK group ID (e.g., 'G0140')                                                                                                                   
                                                                                                                                                                                    
    Returns:                                                                                                                                                                        
        str: Formatted information about the threat group                                                                                                                           
    """                                                                                                                                                                             
    url = f"https://attack.mitre.org/groups/{group_id}/"                                                                                                                            
                                                                                                                                                                                    
    try:                                                                                                                                                                            
        response = requests.get(url)                                                                                                                                                
        if response.status_code != 200:                                                                                                                                             
            return f"Failed to fetch data for group {group_id}"                                                                                                                     
                                                                                                                                                                                    
        soup = BeautifulSoup(response.content, 'html.parser')                                                                                                                       
                                                                                                                                                                                    
        # Extract key information                                                                                                                                                   
        info = {}                                                                                                                                                                   
                                                                                                                                                                                    
        # Get group name and ID                                                                                                                                                     
        title_elem = soup.find('h1', class_='entry-title')                                                                                                                          
        if title_elem:                                                                                                                                                              
            info['name'] = title_elem.get_text(strip=True)                                                                                                                          
                                                                                                                                                                                    
        # Get basic info table                                                                                                                                                      
        info_table = soup.find('table', {'id': 'group-info'})                                                                                                                       
        if info_table:                                                                                                                                                              
            rows = info_table.find_all('tr')                                                                                                                                        
            for row in rows:                                                                                                                                                        
                cells = row.find_all(['td', 'th'])                                                                                                                                  
                if len(cells) >= 2:                                                                                                                                                 
                    key = cells[0].get_text(strip=True).rstrip(':')                                                                                                                 
                    value = cells[1].get_text(strip=True)                                                                                                                           
                    info[key] = value                                                                                                                                               
                                                                                                                                                                                    
        # Get techniques used                                                                                                                                                       
        techniques_section = soup.find('h2', string='Techniques Used')                                                                                                              
        if techniques_section:                                                                                                                                                      
            techniques_table = techniques_section.find_next_sibling('table')                                                                                                        
            if techniques_table:                                                                                                                                                    
                techniques = []                                                                                                                                                     
                rows = techniques_table.find_all('tr')[1:]  # Skip header                                                                                                           
                for row in rows:                                                                                                                                                    
                    cols = row.find_all('td')                                                                                                                                       
                    if len(cols) >= 3:                                                                                                                                              
                        technique_id = cols[1].get_text(strip=True)                                                                                                                 
                        technique_name = cols[2].get_text(strip=True)                                                                                                               
                        use_desc = cols[3].get_text(strip=True) if len(cols) > 3 else ""                                                                                            
                        techniques.append(f"- {technique_id}: {technique_name} ({use_desc})")                                                                                       
                info['techniques_used'] = '\n'.join(techniques)                                                                                                                     
                                                                                                                                                                                    
        # Get software used                                                                                                                                                         
        software_section = soup.find('h2', string='Software')                                                                                                                       
        if software_section:                                                                                                                                                        
            software_table = software_section.find_next_sibling('table')                                                                                                            
            if software_table:                                                                                                                                                      
                software = []                                                                                                                                                       
                rows = software_table.find_all('tr')[1:]  # Skip header                                                                                                             
                for row in rows:                                                                                                                                                    
                    cols = row.find_all('td')                                                                                                                                       
                    if len(cols) >= 2:                                                                                                                                              
                        soft_id = cols[0].get_text(strip=True)                                                                                                                      
                        soft_name = cols[1].get_text(strip=True)                                                                                                                    
                        software.append(f"- {soft_id}: {soft_name}")                                                                                                                
                info['software_used'] = '\n'.join(software)                                                                                                                         
                                                                                                                                                                                    
        # Format output                                                                                                                                                             
        result = []                                                                                                                                                                 
        for key, value in info.items():                                                                                                                                             
            if value:                                                                                                                                                               
                result.append(f"{key}: {value}")                                                                                                                                    
                                                                                                                                                                                    
        return "\n\n".join(result) if result else f"No information found for group {group_id}"                                                                                      
                                                                                                                                                                                    
    except Exception as e:                                                                                                                                                          
        return f"Error fetching MITRE ATT&CK data: {str(e)}"                                                                                                                        
                                                                                                                                                                                    
# Add a helper function to search by name                                                                                                                                           
@function_tool                                                                                                                                                                      
def search_mitre_attack_groups(query: str) -> str:                                                                                                                                  
    """                                                                                                                                                                             
    Search for MITRE ATT&CK groups by name or description.                                                                                                                          
                                                                                                                                                                                    
    Args:                                                                                                                                                                           
        query (str): Search term                                                                                                                                                    
                                                                                                                                                                                    
    Returns:                                                                                                                                                                        
        str: Formatted information about matching groups                                                                                                                            
    """                                                                                                                                                                             
    # This would require more complex parsing of the search results page                                                                                                            
    return f"Search functionality for '{query}' requires additional implementation"                                                                                                 
                                                                                   
