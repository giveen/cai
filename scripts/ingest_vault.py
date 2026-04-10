#!/usr/bin/env python3
"""Cyber-Vault ingestion script.

Clones HackTricks and PayloadsAllTheThings into src/cai/knowledge/,
splits every .md file into overlapping chunks, and indexes them into a
local ChromaDB using the all-MiniLM-L6-v2 SentenceTransformer so no
API key or internet connection is required during query time.

Usage (run from the repo root once):
    python scripts/ingest_vault.py [--force] [--update]

Options:
    --force    Delete and re-create the ChromaDB collection before ingesting.
    --update   Only process markdown files whose mtime is newer than the last
               successful index run (saves CPU / disk I/O on incremental syncs).
    --help     Show this help text.

Dependencies (install via `pip install -e ".[vault]"`):
    chromadb>=1.0
    sentence-transformers>=2.7
    gitpython>=3.1

Return value (when imported and main() called):
    int — number of new chunks added during this run.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Union

# ── Resolve paths relative to the repo root ──────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
_KNOWLEDGE_DIR = _REPO_ROOT / "src" / "cai" / "knowledge"
_CHROMA_DIR = _KNOWLEDGE_DIR / "chroma_db"

SOURCES: list[tuple[str, str]] = [
    (
        "https://github.com/carlospolop/hacktricks.git",
        str(_KNOWLEDGE_DIR / "hacktricks"),
    ),
    (
        "https://github.com/swisskyrepo/PayloadsAllTheThings.git",
        str(_KNOWLEDGE_DIR / "PayloadsAllTheThings"),
    ),
]

COLLECTION_NAME = "cyber_vault"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
EMBED_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 128  # ChromaDB upsert batch size

# Timestamp file that records when the last successful index run completed.
_LAST_INDEXED_FILE = _KNOWLEDGE_DIR / ".last_indexed"

# ChromaDB Metadata-compatible row type
_MetadataRow = dict[str, Union[str, int, float, bool]]


def _import_or_abort(pkg: str) -> Any:
    try:
        import importlib
        return importlib.import_module(pkg)
    except ModuleNotFoundError:
        print(f"[ERROR] Missing package: {pkg}. Run: pip install {pkg}")
        sys.exit(1)


def _clone_or_pull(url: str, dest: str) -> None:
    import git  # type: ignore

    dest_path = Path(dest)
    if dest_path.exists() and (dest_path / ".git").exists():
        print(f"  Pulling updates: {dest_path.name} … ", end="", flush=True)
        try:
            repo = git.Repo(dest)
            repo.remotes.origin.pull()
            print("done")
        except Exception as exc:
            print(f"skipped ({exc})")
    else:
        dest_path.mkdir(parents=True, exist_ok=True)
        print(f"  Cloning {url} → {dest_path.name} … ", end="", flush=True)
        git.Repo.clone_from(url, dest, depth=1)
        print("done")


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split *text* into chunks of *size* characters with *overlap* carry-over."""
    if not text.strip():
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return [c for c in chunks if c.strip()]


def _read_last_indexed_time() -> float:
    """Return the mtime of the last successful index run, or 0.0 if unknown."""
    try:
        return float(_LAST_INDEXED_FILE.read_text().strip())
    except Exception:
        return 0.0


def _write_last_indexed_time() -> None:
    """Persist the current wall-clock time as the last-indexed timestamp."""
    _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    _LAST_INDEXED_FILE.write_text(str(time.time()))


def _iter_md_files(directory: str, since: float = 0.0):
    """Yield (absolute_path, relative_path_str) for every .md file.

    When *since* > 0 only files whose mtime is strictly newer than *since*
    are yielded — this is the incremental-update fast path.
    """
    base = Path(directory)
    for p in sorted(base.rglob("*.md")):
        if since > 0 and p.stat().st_mtime <= since:
            continue
        try:
            rel = str(p.relative_to(_REPO_ROOT))
        except ValueError:
            rel = str(p.relative_to(base))
        yield p, rel  # repo-root-relative, or source-dir-relative as fallback


class _SentenceTransformerEmbeddingFn:
    """ChromaDB-compatible embedding function using a local SentenceTransformer."""

    def __init__(self, model_name: str = EMBED_MODEL) -> None:
        import chromadb  # type: ignore  # noqa: F401 — ensure chromadb is importable
        from sentence_transformers import SentenceTransformer  # type: ignore
        print(f"  Loading embedding model '{model_name}' (first run will download ~80 MB) …",
              end="", flush=True)
        self._model = SentenceTransformer(model_name)
        print(" done")

    def __call__(self, input: Sequence[str]) -> list[list[float]]:  # noqa: A002
        return self._model.encode(  # type: ignore[return-value]
            list(input), convert_to_numpy=True, show_progress_bar=False
        ).tolist()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(__doc__ or ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--force", action="store_true",
                        help="Delete existing collection and re-index from scratch.")
    parser.add_argument("--update", action="store_true",
                        help="Only process files newer than the last successful index run.")
    args = parser.parse_args()

    # ── 1. Verify imports ────────────────────────────────────────────────────
    _import_or_abort("chromadb")
    _import_or_abort("sentence_transformers")
    _import_or_abort("git")

    import chromadb  # type: ignore

    # ── 2. Clone / pull repos ────────────────────────────────────────────────
    print("\n[1/4] Cloning / updating knowledge repositories …")
    _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    for url, dest in SOURCES:
        _clone_or_pull(url, dest)

    # ── 3. Initialise ChromaDB ────────────────────────────────────────────────
    print(f"\n[2/4] Opening ChromaDB at {_CHROMA_DIR} …")
    _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(_CHROMA_DIR))

    embed_fn = _SentenceTransformerEmbeddingFn(EMBED_MODEL)

    # Determine the cutoff timestamp for incremental updates.
    since_ts: float = 0.0
    if args.update and not args.force:
        since_ts = _read_last_indexed_time()
        if since_ts > 0:
            import datetime
            since_dt = datetime.datetime.fromtimestamp(since_ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  --update mode: only processing files modified after {since_dt}")
        else:
            print("  --update mode: no previous index found, processing all files")

    if args.force:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"  Deleted existing collection '{COLLECTION_NAME}'.")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embed_fn,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )

    # ── 4. Index chunks ───────────────────────────────────────────────────────
    print("\n[3/4] Indexing markdown chunks …")
    total_docs = 0
    total_chunks = 0
    new_chunks = 0  # chunks added/updated in this run
    skipped = 0

    # Collect all chunks before batching
    ids_buf: list[str] = []
    docs_buf: list[str] = []
    metas_buf: list[_MetadataRow] = []

    for _url, dest in SOURCES:
        source_name = Path(dest).name
        md_files = list(_iter_md_files(dest, since=since_ts))
        qualifier = "changed " if since_ts > 0 else ""
        print(f"  {source_name}: {len(md_files)} {qualifier}markdown files found")

        for abs_path, rel_path in md_files:
            total_docs += 1
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                print(f"    [WARN] Could not read {rel_path}: {exc}")
                skipped += 1
                continue

            chunks = _chunk_text(text)
            for i, chunk in enumerate(chunks):
                chunk_id = f"{source_name}::{rel_path}::{i}"
                ids_buf.append(chunk_id)
                docs_buf.append(chunk)
                metas_buf.append({"source": rel_path, "chunk_index": i, "repo": source_name})
                total_chunks += 1
                new_chunks += 1

                # Flush in batches
                if len(ids_buf) >= BATCH_SIZE:
                    collection.upsert(ids=ids_buf, documents=docs_buf, metadatas=metas_buf)  # type: ignore[arg-type]
                    ids_buf, docs_buf, metas_buf = [], [], []

    # Final flush
    if ids_buf:
        collection.upsert(ids=ids_buf, documents=docs_buf, metadatas=metas_buf)  # type: ignore[arg-type]

    print(f"  Indexed {total_chunks} chunks from {total_docs - skipped} files "
          f"({skipped} skipped). New/updated chunks this run: {new_chunks}.")

    # ── 5. Persist last-indexed timestamp ─────────────────────────────────────
    _write_last_indexed_time()
    print(f"  Saved last-indexed timestamp to {_LAST_INDEXED_FILE}")

    # ── 6. Verify with a test query ───────────────────────────────────────────
    print("\n[4/4] Running smoke query: 'SQL injection UNION bypass' …")
    results = collection.query(query_texts=["SQL injection UNION bypass"], n_results=3)
    docs_out: list[str] = (results.get("documents") or [[]])[0]
    metas_out: list[_MetadataRow] = (results.get("metadatas") or [[]])[0]  # type: ignore[assignment]
    for i, (doc, meta) in enumerate(zip(docs_out, metas_out), 1):
        src = meta.get("source", "?")
        snippet = doc[:120].replace("\n", " ")
        print(f"  [{i}] {src}: {snippet!r} …")

    print("\n✓ Cyber-Vault ready. Collection size:",
          collection.count(), "chunks.")
    print(f"  DB path: {_CHROMA_DIR}")
    return new_chunks


if __name__ == "__main__":
    main()
