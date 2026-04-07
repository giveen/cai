"""
RAG (Retrieval Augmented Generation) utilities module for
querying and adding data to vector databases.
"""
import os
import uuid
import datetime as _dt
from cai.rag.vector_db_adapter import get_vector_db_adapter
from cai.sdk.agents import function_tool

# CTF BASED MEMORY
collection_name = os.getenv('CAI_MEMORY_COLLECTION', "default")


def _build_provenance(tool_name: str) -> dict:
    """Construct a standard provenance metadata dict.

    Fields: source (module), timestamp (UTC ISO), session_id (env if set),
    ingest_tool (function name).
    """
    ts = _dt.datetime.utcnow().isoformat() + "Z"
    session = os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID")
    return {"source": __name__, "timestamp": ts, "session_id": session, "ingest_tool": tool_name}


def _format_search_results(results, top_k: int = 3) -> str:
    """Best-effort formatting of search results, including provenance when present.

    This function is intentionally permissive because adapters return
    heterogeneous shapes (strings, lists, dicts). It tries common keys
    used by vector DB clients and falls back to stringifying the item.
    """
    if not results:
        return "No documents found in memory."

    if isinstance(results, str):
        return results

    if isinstance(results, dict):
        # single-result dict
        results = [results]

    if isinstance(results, (list, tuple)):
        lines = []
        for idx, item in enumerate(results[:top_k]):
            if isinstance(item, dict):
                # Common vector DB payload shapes
                text = item.get("text") or item.get("payload") or item.get("document") or item.get("content")
                metadata = item.get("metadata") or item.get("meta") or item.get("payload") or {}
                provenance = None
                if isinstance(metadata, dict):
                    provenance = metadata.get("provenance")
                # Also check top-level provenance
                if provenance is None:
                    provenance = item.get("provenance")

                display = text if text is not None else str(item)
                if provenance:
                    lines.append(f"- {display} (provenance: {provenance})")
                else:
                    lines.append(f"- {display}")
            else:
                lines.append(f"- {str(item)}")
        return "\n".join(lines)

    # Fallback
    return str(results)

@function_tool
def query_memory(query: str, top_k: int = 3) -> str:  # pylint: disable=line-too-long # noqa: E501
    """
    Query memory to retrieve relevant context. From Previous CTFs executions.

    Args:
        query (str): The search query to find relevant documents
        top_k (int): Number of top results to return (default: 3)

    Returns:
        str: Retrieved context from the vector database, formatted as a string
            with the most relevant matches
    """
    try:
        adapter = get_vector_db_adapter()

        # First try semantic search
        results = adapter.search(
            collection_name="_all_",
            query_text=query,
            limit=top_k,
        )

        # If no results, fall back to retrieving all documents
        return _format_search_results(results, top_k=top_k)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        return f"Error querying memory: {str(exc)}"

@function_tool
def add_to_memory_episodic(texts: str, step: int = 0) -> str:  # pylint: disable=line-too-long # noqa: E501
    """
    This is a persistent memory to add relevant context to our memory.
    Use this function to add relevant context to the memory.

    Args:
        texts: relevant data to add to memory
        step: step number of the current CTF
    Returns:
        str: Status message indicating success or failure
    """
    try:
        adapter = get_vector_db_adapter()
        try:
            adapter.create_collection(collection_name)
        except Exception:  # nosec # pylint: disable=broad-exception-caught
            pass

        prov = _build_provenance("add_to_memory_episodic")
        success = adapter.add_points(
            id_point=step,
            collection_name=collection_name,
            texts=[texts],
            metadata=[{"CTF": True, "provenance": prov}]
        )

        if success:
            return f"Successfully added document to collection {collection_name}"
        return "Failed to add documents to vector database"

    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"Error adding documents to vector database: {str(e)}"

@function_tool
def add_to_memory_semantic(texts: str, step: int = 0) -> str:  # pylint: disable=line-too-long # noqa: E501
    """
    This is a persistent memory to add relevant context to our memory.
    Use this function to add relevant context to the memory.

    Args:
        texts: relevant data to add to memory, no PII data about CTF env,
        only techniques and procedures
        do not include any information about IP
        be explicit with the tecnhiques and reasoning process
        step: step number of the current CTF
    Returns:
        str: Status message indicating success or failure
    """
    doc_id = str(uuid.uuid4())
    try:
        adapter = get_vector_db_adapter()
        try:
            adapter.create_collection("_all_")
        except Exception:  # nosec # pylint: disable=broad-exception-caught
            pass

        prov = _build_provenance("add_to_memory_semantic")
        success = adapter.add_points(
            id_point=doc_id,
            collection_name="_all_",
            texts=[texts],
            metadata=[{"CTF": collection_name, "step": step, "provenance": prov}]
        )

        if success:
            return f"Successfully added document to collection {collection_name}"
        return "Failed to add documents to vector database"

    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"Error adding documents to vector database: {str(e)}"
