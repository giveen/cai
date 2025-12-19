"""
Google search utility for regular searches and Google dorking.

This module provides functions to perform Google searches in two modes:
1. Regular search - Returns URLs from standard Google search results
2. Google dorking - Returns URLs from searches using advanced Google search operators
"""
import os
import requests
import time
from typing import List, Optional, Dict, Tuple
from dotenv import load_dotenv
from cai.sdk.agents import function_tool


class LRUCache:
    def __init__(self, max_size=128, ttl=3600):  # 1 hour TTL
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl
    
    def get(self, key):
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None
    
    def put(self, key, value):
        if len(self.cache) >= self.max_size:
            # Remove oldest item (simple LRU implementation)
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        self.cache[key] = (value, time.time())


# Global cache instance
_search_cache = LRUCache(max_size=128, ttl=3600)


def google_search(query: str, num_results: int = 10) -> str:
    """
    Perform a regular Google search and return a formatted string with results.

    Args:
        query (str): The search query.
        num_results (int): Maximum number of results to return. Default is 10.

    Returns:
        str: A formatted string containing URLs, titles, and snippets from 
        the search results.
    """
    # Create cache key from parameters
    cache_key = f"{query}_{num_results}_regular"
    
    # Check cache first
    cached_result = _search_cache.get(cache_key)
    if cached_result is not None:
        return cached_result
    
    results = _perform_search(query, num_results, is_dork=False)
    formatted_results = ""
    
    for result in results:
        formatted_results += f"Title: {result['title']}\n"
        formatted_results += f"URL: {result['url']}\n"
        formatted_results += f"Snippet: {result['snippet']}\n\n"
    
    # Cache the result
    _search_cache.put(cache_key, formatted_results)
    return formatted_results


def google_dork_search(dork_query: str, num_results: int = 100) -> str:
    """
    Perform a Google dork search and return a formatted string with URLs.
    
    Google dorking uses advanced search operators to find specific information.
    Examples of operators: site:, filetype:, inurl:, intitle:, etc.

    Args:
        dork_query (str): The Google dork query with operators.
        num_results (int): Maximum number of results to return. Default is 10.

    Returns:
        str: A formatted string containing URLs from the dork search results.
    """
    results = _perform_search(dork_query, num_results, is_dork=True)
    formatted_results = ""
    
    for result in results:
        formatted_results += f"{result['url']}\n"
    
    return formatted_results

def _perform_search(query: str, num_results: int = 10, 
                   is_dork: bool = False) -> List[Dict[str, str]]:
    """
    Helper function to perform Google searches.

    Args:
        query (str): The search query.
        num_results (int): Maximum number of results to return.
        is_dork (bool): Whether this is a dork search.

    Returns:
        List[Dict[str, str]]: For regular searches, returns a list of dictionaries 
        with URLs, titles, and snippets. For dork searches, returns a list of 
        dictionaries with only URLs.
    """
    load_dotenv()
    api_key = os.getenv("GOOGLE_SEARCH_API_KEY") 
    cx = os.getenv("GOOGLE_SEARCH_CX")
    
    if not api_key or not cx:
        raise ValueError(
            "Google Search API key (GOOGLE_SEARCH_API_KEY) and Custom Search "
            "Engine ID (GOOGLE_SEARCH_CX) must be set in environment variables."
        )
    
    base_url = "https://www.googleapis.com/customsearch/v1"
    
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": min(num_results, 10)  # API limits to 10 results per request
    }
    
    results = []
    
    # Google API returns max 10 results per request, so we need to make multiple
    # requests with different start indices to get more results
    for start_index in range(1, min(num_results + 1, 101), 10):  # Google API limits to 100 results total
        if start_index > 1:
            params["start"] = start_index
            
        response = requests.get(base_url, params=params)
        
        if response.status_code != 200:
            break
            
        data = response.json()
        
        if "items" not in data:
            break
            
        for item in data["items"]:
            if len(results) >= num_results:
                break
                
            if is_dork:
                results.append({
                    "url": item["link"]
                })
            else:
                results.append({
                    "url": item["link"],
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", "")
                })
    
    return results
