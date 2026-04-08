"""
RAG (Retrieval Augmented Generation) utilities module for
querying and adding data to vector databases.
"""
import os
import uuid
import hashlib
import datetime as _dt
from cai.rag.vector_db_adapter import get_vector_db_adapter
from cai.rag.ingestion import get_ingestor
from cai.rag.metrics import collector
from cai.rag.chunking import chunk_text, fingerprint_chunks
from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)

# CTF BASED MEMORY
collection_name = os.getenv('CAI_MEMORY_COLLECTION', "default")


def _build_provenance(
    tool_name: str,
    original_text: str | None = None,
    chunk_id: str | None = None,
    embed_fingerprint: str | None = None,
) -> dict:
    """Construct a standard provenance metadata dict.

    Fields: source (module), timestamp (UTC ISO), session_id (env if set),
    tool_name (function name), original_text (raw content), chunk_id (unique
    chunk identifier), content_hash (sha256 hex), embed_fingerprint, fingerprint.
    """
    ts = _dt.datetime.utcnow().isoformat() + "Z"
    session = os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID")
    ch = None
    if original_text is not None:
        try:
            ch = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
        except Exception:
            ch = None
    cid = chunk_id or str(uuid.uuid4())
    fingerprint = None
    if ch and embed_fingerprint:
        fingerprint = f"{ch}:{embed_fingerprint}"
    elif ch:
        fingerprint = ch

    out = {
        "source": __name__,
        "timestamp": ts,
        "session_id": session,
        "tool_name": tool_name,
        "original_text": original_text,
        "chunk_id": cid,
        "content_hash": ch,
    }
    if embed_fingerprint is not None:
        out["embed_fingerprint"] = embed_fingerprint
    if fingerprint is not None:
        out["fingerprint"] = fingerprint
    return out


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

        # Metrics: record queries and hits (best-effort)
        try:
            collector().incr("search_queries")
            if results is None:
                collector().incr("search_hits", 0)
            elif isinstance(results, (list, tuple)):
                collector().incr("search_hits", len(results))
            else:
                collector().incr("search_hits", 1)
        except Exception:
            pass

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

        # Chunk settings (configurable via env)
        try:
            chunk_size = int(os.getenv("CAI_RAG_CHUNK_SIZE", "1000"))
            overlap = int(os.getenv("CAI_RAG_CHUNK_OVERLAP", "200"))
        except Exception:
            chunk_size, overlap = 1000, 200

        chunks = chunk_text(texts, chunk_size=chunk_size, overlap=overlap)
        # If chunking produced no pieces (empty input), fall back to single-add
        if not chunks:
            prov = _build_provenance("add_to_memory_episodic", original_text=texts, chunk_id=f"episodic-{step}-{uuid.uuid4()}")
            try:
                ing = get_ingestor(adapter)
                ing.enqueue(collection_name, step, [texts], [{"CTF": True, "provenance": prov}])
                if os.getenv("CAI_RAG_INGEST_SYNC", "0") in ("1", "true", "True"):
                    try:
                        ing.flush_sync()
                    except Exception:
                        pass
                return f"Queued 1 document for ingestion to collection {collection_name}"
            except Exception:
                # fallback to direct add
                success = adapter.add_points(
                    id_point=step,
                    collection_name=collection_name,
                    texts=[texts],
                    metadata=[{"CTF": True, "provenance": prov}],
                )
                if success:
                    return f"Successfully added document to collection {collection_name}"
                return "Failed to add documents to vector database"

        chunk_texts = [c["text"] for c in chunks]
        try:
            embeddings = adapter.embed_texts(chunk_texts)
        except Exception:
            embeddings = [None for _ in chunk_texts]

        fp_chunks = fingerprint_chunks(chunks, embeddings)

        # Build existing fingerprint sets when adapter supports exporting
        existing_content_hashes = set()
        existing_embed_hashes = set()
        existing_fingerprints = set()
        if hasattr(adapter, "export_collection"):
            try:
                existing = adapter.export_collection(collection_name)
                for d in (existing or []):
                    meta = d.get("metadata") or {}
                    prov = None
                    if isinstance(meta, dict):
                        prov = meta.get("provenance") or d.get("provenance")
                    else:
                        prov = d.get("provenance")
                    if isinstance(prov, dict):
                        ch = prov.get("content_hash")
                        ef = prov.get("embed_fingerprint")
                        fp = prov.get("fingerprint")
                        if ch:
                            existing_content_hashes.add(ch)
                        if ef:
                            existing_embed_hashes.add(ef)
                        if fp:
                            existing_fingerprints.add(fp)
            except Exception:
                pass

        ids_to_add = []
        texts_to_add = []
        metas_to_add = []
        for item in fp_chunks:
            ch = item.get("content_hash")
            ef = item.get("embed_fingerprint")
            fp = item.get("fingerprint")

            # Skip duplicates if fingerprint or hashes already present
            if (fp and fp in existing_fingerprints) or (ch and ch in existing_content_hashes) or (ef and ef in existing_embed_hashes):
                continue

            cid = f"episodic-{step}-{item.get('index')}"
            prov = _build_provenance("add_to_memory_episodic", original_text=item.get("text"), chunk_id=cid, embed_fingerprint=ef)
            meta = {"CTF": True, "provenance": prov}
            if fp:
                meta["fingerprint"] = fp

            ids_to_add.append(f"{step}-{item.get('index')}")
            texts_to_add.append(item.get("text"))
            metas_to_add.append(meta)

        if not texts_to_add:
            return "No new chunks to add (duplicates)"

        try:
            ing = get_ingestor(adapter)
            ing.enqueue(collection_name, ids_to_add if len(ids_to_add) > 1 else ids_to_add[0], texts_to_add, metas_to_add)
            if os.getenv("CAI_RAG_INGEST_SYNC", "0") in ("1", "true", "True"):
                try:
                    ing.flush_sync()
                except Exception:
                    pass
            return f"Queued {len(texts_to_add)} chunk(s) for ingestion to collection {collection_name}"
        except Exception as e:  # fallback to direct add if ingestion manager fails
            try:
                success = adapter.add_points(
                    id_point=ids_to_add if len(ids_to_add) > 1 else ids_to_add[0],
                    collection_name=collection_name,
                    texts=texts_to_add,
                    metadata=metas_to_add,
                )
                if success:
                    return f"Successfully added {len(texts_to_add)} chunk(s) to collection {collection_name}"
            except Exception:
                pass
            return f"Failed to add documents to vector database: {str(e)}"

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error adding documents to vector database (episodic)")
        return f"Error adding documents to vector database: {e.__class__.__name__}: {str(e)}"

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

        # Chunk settings
        try:
            chunk_size = int(os.getenv("CAI_RAG_CHUNK_SIZE", "1000"))
            overlap = int(os.getenv("CAI_RAG_CHUNK_OVERLAP", "200"))
        except Exception:
            chunk_size, overlap = 1000, 200

        chunks = chunk_text(texts, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            prov = _build_provenance("add_to_memory_semantic", original_text=texts, chunk_id=f"semantic-{doc_id}-0")
            try:
                ing = get_ingestor(adapter)
                ing.enqueue("_all_", doc_id, [texts], [{"CTF": collection_name, "step": step, "provenance": prov}])
                if os.getenv("CAI_RAG_INGEST_SYNC", "0") in ("1", "true", "True"):
                    try:
                        ing.flush_sync()
                    except Exception:
                        pass
                return f"Queued 1 document for ingestion to collection _all_"
            except Exception:
                success = adapter.add_points(
                    id_point=doc_id,
                    collection_name="_all_",
                    texts=[texts],
                    metadata=[{"CTF": collection_name, "step": step, "provenance": prov}],
                )
                if success:
                    return f"Successfully added document to collection {collection_name}"
                return "Failed to add documents to vector database"

        chunk_texts = [c["text"] for c in chunks]
        try:
            embeddings = adapter.embed_texts(chunk_texts)
        except Exception:
            embeddings = [None for _ in chunk_texts]

        fp_chunks = fingerprint_chunks(chunks, embeddings)

        existing_content_hashes = set()
        existing_embed_hashes = set()
        existing_fingerprints = set()
        if hasattr(adapter, "export_collection"):
            try:
                existing = adapter.export_collection("_all_")
                for d in (existing or []):
                    meta = d.get("metadata") or {}
                    prov = None
                    if isinstance(meta, dict):
                        prov = meta.get("provenance") or d.get("provenance")
                    else:
                        prov = d.get("provenance")
                    if isinstance(prov, dict):
                        ch = prov.get("content_hash")
                        ef = prov.get("embed_fingerprint")
                        fp = prov.get("fingerprint")
                        if ch:
                            existing_content_hashes.add(ch)
                        if ef:
                            existing_embed_hashes.add(ef)
                        if fp:
                            existing_fingerprints.add(fp)
            except Exception:
                pass

        ids_to_add = []
        texts_to_add = []
        metas_to_add = []
        for item in fp_chunks:
            ch = item.get("content_hash")
            ef = item.get("embed_fingerprint")
            fp = item.get("fingerprint")

            if (fp and fp in existing_fingerprints) or (ch and ch in existing_content_hashes) or (ef and ef in existing_embed_hashes):
                continue

            cid = f"semantic-{doc_id}-{item.get('index')}"
            prov = _build_provenance("add_to_memory_semantic", original_text=item.get("text"), chunk_id=cid, embed_fingerprint=ef)
            meta = {"CTF": collection_name, "step": step, "provenance": prov}
            if fp:
                meta["fingerprint"] = fp

            ids_to_add.append(f"{doc_id}-{item.get('index')}")
            texts_to_add.append(item.get("text"))
            metas_to_add.append(meta)

        if not texts_to_add:
            return "No new chunks to add (duplicates)"

        try:
            ing = get_ingestor(adapter)
            ing.enqueue("_all_", ids_to_add if len(ids_to_add) > 1 else ids_to_add[0], texts_to_add, metas_to_add)
            if os.getenv("CAI_RAG_INGEST_SYNC", "0") in ("1", "true", "True"):
                try:
                    ing.flush_sync()
                except Exception:
                    pass
            return f"Queued {len(texts_to_add)} chunk(s) for ingestion to collection _all_"
        except Exception as e:  # fallback to direct add if ingestion manager fails
            try:
                success = adapter.add_points(
                    id_point=ids_to_add if len(ids_to_add) > 1 else ids_to_add[0],
                    collection_name="_all_",
                    texts=texts_to_add,
                    metadata=metas_to_add,
                )
                if success:
                    return f"Successfully added {len(texts_to_add)} chunk(s) to collection {collection_name}"
            except Exception:
                pass
            return f"Failed to add documents to vector database: {str(e)}"

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error adding documents to vector database (semantic)")
        return f"Error adding documents to vector database: {e.__class__.__name__}: {str(e)}"
