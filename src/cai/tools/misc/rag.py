"""
RAG (Retrieval Augmented Generation) utilities module for
querying and adding data to vector databases.
"""
import os
import uuid
from cai.rag.vector_db_adapter import get_vector_db_adapter
from cai.sdk.agents import function_tool

# CTF BASED MEMORY
collection_name = os.getenv('CAI_MEMORY_COLLECTION', "default")

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
        if not results:
            return "No documents found in memory."

        return results

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

        success = adapter.add_points(
            id_point=step,
            collection_name=collection_name,
            texts=[texts],
            metadata=[{"CTF": True}]
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

        success = adapter.add_points(
            id_point=doc_id,
            collection_name="_all_",
            texts=[texts],
            metadata=[{"CTF": collection_name}, {"step": step}]
        )

        if success:
            return f"Successfully added document to collection {collection_name}"
        return "Failed to add documents to vector database"

    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"Error adding documents to vector database: {str(e)}"
