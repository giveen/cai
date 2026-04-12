"""Smoke tests for scripts/ingest_vault.py.

These tests avoid heavy network/ML downloads by patching the external
dependencies (chromadb, git) and replacing the embedding function with a
lightweight dummy.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
from pathlib import Path


def _load_module():
    # Find repository root by searching upwards for a marker file.
    p = Path(__file__).resolve()
    repo_root = None
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            repo_root = parent
            break
    if repo_root is None:
        # Fallback to a sensible ancestor
        try:
            repo_root = p.parents[3]
        except Exception:
            repo_root = p.parents[-1]

    script_path = str(repo_root / "scripts" / "ingest_vault.py")
    spec = importlib.util.spec_from_file_location("ingest_vault", script_path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_read_write_last_indexed_time(tmp_path: Path):
    mod = _load_module()
    # Point the module to an isolated knowledge directory
    kd = tmp_path / "src" / "cai" / "knowledge"
    mod._KNOWLEDGE_DIR = kd  # type: ignore[attr-defined]
    # Ensure module writes/reads the last-indexed file inside the test dir
    mod._LAST_INDEXED_FILE = kd / ".last_indexed"  # type: ignore[attr-defined]

    # Write then read
    mod._write_last_indexed_time()
    assert (kd / ".last_indexed").exists()
    t = mod._read_last_indexed_time()
    assert isinstance(t, float) and t > 0


def test_iter_md_files_since(tmp_path: Path):
    mod = _load_module()
    repo_root = tmp_path
    knowledge_dir = repo_root / "src" / "cai" / "knowledge"
    hack_dir = knowledge_dir / "hacktricks"
    hack_dir.mkdir(parents=True, exist_ok=True)

    old = hack_dir / "old.md"
    new = hack_dir / "new.md"
    old.write_text("old content")
    new.write_text("new content")

    # Set mtimes so only `new` is newer than the cutoff
    now = time.time()
    os.utime(old, (now - 10000, now - 10000))
    os.utime(new, (now, now))

    since = now - 5000
    results = list(mod._iter_md_files(str(hack_dir), since=since))
    assert len(results) == 1
    abs_path, rel_path = results[0]
    assert Path(abs_path).name == "new.md"


def test_main_update_mode(tmp_path: Path, monkeypatch):
    mod = _load_module()

    # Create a fake repo layout and two small markdown files
    repo_root = tmp_path
    knowledge_dir = repo_root / "src" / "cai" / "knowledge"
    hack_dir = knowledge_dir / "hacktricks"
    payload_dir = knowledge_dir / "PayloadsAllTheThings"
    hack_dir.mkdir(parents=True, exist_ok=True)
    payload_dir.mkdir(parents=True, exist_ok=True)

    (hack_dir / "a.md").write_text("hello world")
    (payload_dir / "b.md").write_text("payload content")

    # Mark as git repos so _clone_or_pull will take the pull path
    (hack_dir / ".git").mkdir(exist_ok=True)
    (payload_dir / ".git").mkdir(exist_ok=True)

    # Point module to our test knowledge dir and sources
    mod._KNOWLEDGE_DIR = knowledge_dir  # type: ignore[attr-defined]
    mod._CHROMA_DIR = knowledge_dir / "chroma_db"  # type: ignore[attr-defined]
    # Ensure module writes/reads the last-indexed file inside the test dir
    mod._LAST_INDEXED_FILE = knowledge_dir / ".last_indexed"  # type: ignore[attr-defined]
    mod.SOURCES = [  # type: ignore[attr-defined]
        ("https://example/hacktricks.git", str(hack_dir)),
        ("https://example/payloads.git", str(payload_dir)),
    ]

    # Fake chromadb client/collection
    class FakeCollection:
        def __init__(self):
            self.count_val = 0
            self.upserts = []

        def upsert(self, ids, documents, metadatas):
            self.upserts.append((ids, documents, metadatas))
            self.count_val += len(ids)

        def query(self, query_texts, n_results):
            return {
                "documents": [["doc"]],
                "metadatas": [[{"source": "x.md", "chunk_index": 0}]],
                "distances": [[0.1]],
            }

        def count(self):
            return self.count_val

    class FakeClient:
        def __init__(self, path):
            self._col = FakeCollection()

        def get_or_create_collection(self, name, embedding_function=None, metadata=None):
            return self._col

        def delete_collection(self, name):
            return None

    chromadb_mod = types.SimpleNamespace(PersistentClient=lambda path: FakeClient(path))
    monkeypatch.setitem(sys.modules, "chromadb", chromadb_mod)

    # Replace the heavy embedder with a tiny deterministic one
    class DummyEmbed:
        def __init__(self, model_name: str | None = None):
            pass

        def __call__(self, input: list[str]):
            return [[0.1, 0.2, 0.3] for _ in input]

    monkeypatch.setattr(mod, "_SentenceTransformerEmbeddingFn", DummyEmbed)

    # Minimal fake git module
    class FakeRepoCls:
        def __init__(self, path):
            self.remotes = types.SimpleNamespace(origin=types.SimpleNamespace(pull=lambda: None))

        @classmethod
        def clone_from(cls, url, dest, depth):
            os.makedirs(dest, exist_ok=True)
            os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
            return cls(dest)

    monkeypatch.setitem(sys.modules, "git", types.SimpleNamespace(Repo=FakeRepoCls))

    # Run in update mode (no --force)
    monkeypatch.setattr(sys, "argv", ["ingest_vault.py", "--update"])
    new_chunks = mod.main()

    assert isinstance(new_chunks, int) and new_chunks >= 2
