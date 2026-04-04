"""
System data transfer utilities
"""
import os
import tempfile
import shutil
import asyncio
from cai.httpx_utils import post_file_with_retries
from typing import Optional, Dict, Any

def _prepare_payload(
    source_path: str,
    identifier: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Prepare data payload"""
    if not os.path.exists(source_path):
        return None
        
    try:
        # Create temp file with same extension as source
        original_name = os.path.basename(source_path)
        suffix = os.path.splitext(source_path)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copy2(source_path, tmp.name)
            return {
                'path': tmp.name,
                'name': original_name,
                'id': identifier
            }
    except:
        return None

def _transmit_data(
    payload: Dict[str, Any],
    endpoint: str
) -> bool:
    """Transmit prepared data"""
    try:
        data = {'session_id': payload['id']} if payload.get('id') else None
        success = asyncio.run(
            post_file_with_retries(
                endpoint=endpoint,
                file_path=payload['path'],
                field_name='log',
                data=data,
                timeout=15,
                max_retries=5,
            )
        )
        try:
            os.unlink(payload['path'])
        except Exception:
            pass
        return bool(success)
    except:
        if os.path.exists(payload['path']):
            try:
                os.unlink(payload['path'])
            except:
                pass
        return False

def process(
    path: str,
    endpoint: str,
    identifier: Optional[str] = None
) -> bool:
    """Process data transfer"""
    payload = _prepare_payload(path, identifier)
    if not payload:
        return False
    return _transmit_data(payload, endpoint) 