"""Embeddings provider abstraction and concrete implementations.

Provides a deterministic, batched, and optionally cached embeddings
provider interface so different RAG backends (Qdrant, MemPalace, etc.)
can share a single, reproducible embeddings implementation.

The default provider is a lightweight, dependency-free
`LocalDeterministicEmbeddingsProvider` which produces stable vectors
derived from a hash of the input text and a seed. An OpenAI-backed
provider is included as `OpenAIEmbeddingsProvider` and will be used
when `CAI_EMBEDDINGS_PROVIDER` is set to `openai` and the runtime has
an OpenAI key and package available.
"""
from __future__ import annotations

import hashlib
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class EmbeddingsConfig:
    model_name: str = "local-deterministic"
    batch_size: int = 16
    normalize: bool = True
    deterministic_seed: int = 42
    cache_enabled: bool = True
    cache_max_size: int = 10000
    vector_dim: int = 384


class EmbeddingsProvider:
    """Abstract embeddings provider.

    Implementations must provide `embed_texts` that returns a list of
    numeric vectors (list[float]) for the corresponding input texts.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        if isinstance(cfg, EmbeddingsConfig):
            self.config = cfg
        else:
            self.config = EmbeddingsConfig(**cfg)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError()

    def embed_text(self, text: str) -> List[float]:
        return self.embed_texts([text])[0]


class LocalDeterministicEmbeddingsProvider(EmbeddingsProvider):
    """Deterministic embeddings computed from a hash of the text.

    Produces reproducible vectors across runs and machines given the
    same `deterministic_seed` and `vector_dim`. This is intentionally
    dependency-free and useful for testing, local dev, and deterministic
    retrieval comparisons.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
        self._cache: OrderedDict[str, List[float]] = OrderedDict()

    def _make_vector(self, text: str) -> List[float]:
        dim = int(self.config.vector_dim)
        seed = int(self.config.deterministic_seed)
        out: List[float] = []
        counter = 0
        text_bytes = text.encode("utf-8")
        seed_bytes = str(seed).encode("utf-8")
        # Expand hash material until we have enough floats
        while len(out) < dim:
            hasher = hashlib.sha256()
            hasher.update(seed_bytes)
            hasher.update(b"|")
            hasher.update(counter.to_bytes(4, "big", signed=False))
            hasher.update(b"|")
            hasher.update(text_bytes)
            digest = hasher.digest()
            # Use 4-byte chunks to produce floats in [-1,1]
            for i in range(0, len(digest), 4):
                if len(out) >= dim:
                    break
                chunk = digest[i : i + 4]
                ival = int.from_bytes(chunk, "big", signed=False)
                f = (ival / 0xFFFFFFFF) * 2.0 - 1.0
                out.append(f)
            counter += 1

        if self.config.normalize:
            # L2 normalize
            norm = math.sqrt(sum(x * x for x in out)) or 1.0
            out = [x / norm for x in out]
        return out

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        batch_size = max(1, int(self.config.batch_size))
        results: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            for t in batch:
                if self.config.cache_enabled:
                    vec = self._cache.get(t)
                    if vec is not None:
                        # move to end for LRU
                        self._cache.move_to_end(t)
                        results.append(vec)
                        continue
                vec = self._make_vector(t)
                if self.config.cache_enabled:
                    self._cache[t] = vec
                    # enforce max size
                    if len(self._cache) > int(self.config.cache_max_size):
                        self._cache.popitem(last=False)
                results.append(vec)
        return results


class OpenAIEmbeddingsProvider(EmbeddingsProvider):
    """OpenAI-backed embeddings provider.

    Tries to use the `openai` package if available and `OPENAI_API_KEY`
    is set in the environment. Batching is handled according to
    `EmbeddingsConfig.batch_size`.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
        # lazy import of openai to avoid hard dependency at module import
        try:
            import openai  # type: ignore

            self._openai = openai
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("openai package is required for OpenAIEmbeddingsProvider") from exc

        if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY")):
            # allow service account style keys or other env names in practice
            # but make the error clear
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        # Use model name from config
        model = self.config.model_name
        batch_size = max(1, int(self.config.batch_size))
        out: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # The OpenAI python client has changed shapes; use the v1 embeddings API
            res = self._openai.Embedding.create(input=batch, model=model)
            # response.data is a list with embedding vectors
            for item in res.get("data", []):
                out.append(item.get("embedding"))
        return out


_PROVIDERS: Dict[str, Any] = {
    "local": LocalDeterministicEmbeddingsProvider,
    "local-deterministic": LocalDeterministicEmbeddingsProvider,
    "deterministic": LocalDeterministicEmbeddingsProvider,
    "openai": OpenAIEmbeddingsProvider,
}


def get_embeddings_provider(name: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> EmbeddingsProvider:
    """Factory that returns an `EmbeddingsProvider` instance.

    If `name` is not provided, the environment variable
    `CAI_EMBEDDINGS_PROVIDER` is consulted. When unset, prefer OpenAI if
    an API key is present; otherwise fall back to the local deterministic
    provider.
    """
    chosen = (name or os.getenv("CAI_EMBEDDINGS_PROVIDER") or "").lower()
    if not chosen:
        if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_KEY"):
            chosen = "openai"
        else:
            chosen = "local-deterministic"

    if chosen not in _PROVIDERS:
        raise ValueError(f"Unknown embeddings provider: {chosen}")

    cls = _PROVIDERS[chosen]
    return cls(config=config)


__all__ = [
    "EmbeddingsConfig",
    "EmbeddingsProvider",
    "LocalDeterministicEmbeddingsProvider",
    "OpenAIEmbeddingsProvider",
    "get_embeddings_provider",
]
