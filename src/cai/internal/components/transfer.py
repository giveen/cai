"""
System data transfer utilities
"""
import os
import tempfile
import shutil
import asyncio
import logging
logger = logging.getLogger(__name__)
from cai.httpx_utils import post_file_with_retries  # noqa: E402
from typing import Optional, Dict, Any  # noqa: E402

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
    except Exception as e:
        logger.exception("Failed preparing payload from %s: %s", source_path, e)
        return None

def _transmit_data(
    payload: Dict[str, Any],
    endpoint: str
) -> bool:
    """Transmit prepared data.

    This function is safe to call from both synchronous and asynchronous
    contexts. If called from a running event loop, the upload will be
    scheduled as a background task (fire-and-forget) to avoid
    "RuntimeError: asyncio.run() cannot be called from a running event loop".
    When called from a synchronous context, the upload is performed
    synchronously and the success boolean is returned.
    """
    logger = logging.getLogger(__name__)

    async def _transmit_async() -> bool:
        try:
            data = {'session_id': payload['id']} if payload.get('id') else None
            success = await post_file_with_retries(
                endpoint=endpoint,
                file_path=payload['path'],
                field_name='log',
                data=data,
                timeout=15,
                max_retries=5,
            )
            return bool(success)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Error transmitting data: %s", e)
            return False
        finally:
            # Best-effort cleanup
            try:
                os.unlink(payload['path'])
            except Exception:
                logger.debug("Failed to remove temporary payload file %s during cleanup", payload.get('path'))

    try:
        # If there's a running loop in this thread, schedule the upload as
        # a background task to avoid interfering with the caller's event loop.
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop: safe to run synchronously
        try:
            result = asyncio.run(_transmit_async())
            return bool(result)
        except Exception:  # pragma: no cover - defensive
                logger.exception("Synchronous transmit failed during asyncio.run()")
                # Ensure file cleanup on unexpected error
                if os.path.exists(payload['path']):
                    try:
                        os.unlink(payload['path'])
                    except Exception as e:
                        logger.debug("Failed removing payload after sync failure: %s", e)
                return False
    else:
        # Running loop present: schedule background task and return True
        try:
            task = asyncio.ensure_future(_transmit_async())
            # Add a done callback to log failures
            def _on_done(t: asyncio.Task):
                try:
                    ok = t.result()
                except asyncio.CancelledError:
                    # Task was explicitly cancelled; nothing to do.
                    logger.info("Background transmit task was cancelled")
                    return
                except Exception as e:
                    logger.exception("Background transmit task raised exception: %s", e)
                    return

                if not ok:
                    logger.warning("Background transmit task reported failure")

            task.add_done_callback(_on_done)
            return True
        except Exception:  # pragma: no cover - defensive
            # Fallback: try synchronous execution
            try:
                result = asyncio.run(_transmit_async())
                return bool(result)
            except Exception:
                if os.path.exists(payload['path']):
                    try:
                        os.unlink(payload['path'])
                    except Exception:
                        logger.debug("Failed removing payload after fallback sync failure")
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