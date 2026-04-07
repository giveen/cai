"""Small LongMemEval-style retrieval evaluation harness.

Usage:
  python benchmarks/longmem_eval.py --num-docs 100 --vector-dim 64

This script builds a synthetic dataset using deterministic embeddings
and reports Recall@K for several pipeline variants: dense-only,
sparse-only, combiner, and combiner+rerank. Results are reproducible
when using the default `LocalDeterministicEmbeddingsProvider`.
"""
from __future__ import annotations

import argparse
import json
from typing import List, Dict, Any

from cai.rag.vector_db_adapter import LocalFallbackAdapter
from cai.rag.embeddings import LocalDeterministicEmbeddingsProvider
from cai.rag.retriever_pipeline import DenseRetriever, SimpleBM25, RetrieverCombiner, Reranker


def build_dataset(num_docs: int, num_topics: int, vector_dim: int):
    provider = LocalDeterministicEmbeddingsProvider({"vector_dim": vector_dim})
    adapter = LocalFallbackAdapter(config={"options": {}}, embeddings_provider=provider)
    coll = "eval_coll"
    adapter.create_collection(coll)

    ids = []
    texts = []
    metas = []
    topics = [f"topic_{i}" for i in range(num_topics)]
    for i in range(num_docs):
        topic = topics[i % num_topics]
        doc_id = f"doc_{i}"
        text = f"This document covers {topic}. Unique marker: {doc_id}. More context about {topic} to make semantic signals."
        ids.append(doc_id)
        texts.append(text)
        metas.append({"topic": topic})

    adapter.add_points(ids, coll, texts, metas)
    docs = adapter.export_collection(coll)

    queries = []
    gt_ids = []
    for i in range(num_docs):
        topic = topics[i % num_topics]
        if i % 3 == 0:
            q = f"details about {topic} and context"
        elif i % 3 == 1:
            q = f"what is known about {topic}"
        else:
            q = f"tell me about {topic} in the documents"
        queries.append(q)
        gt_ids.append(ids[i])

    return adapter, docs, queries, gt_ids, provider


def evaluate(adapter, docs, queries, gt_ids, provider, top_ks=(1, 3, 5)):
    max_k = max(top_ks)
    dense = DenseRetriever(adapter, collection_name="eval_coll")
    sparse = SimpleBM25(docs)
    combiner = RetrieverCombiner()
    reranker = Reranker(embeddings_provider=provider)

    pipelines = {
        "dense": lambda q: dense.retrieve(q, top_k=max_k),
        "sparse": lambda q: sparse.retrieve(q, top_k=max_k),
        "combiner": lambda q: combiner.combine([dense.retrieve(q, top_k=max_k), sparse.retrieve(q, top_k=max_k)], top_k=max_k),
        "combiner_rerank": lambda q: reranker.rerank(q, combiner.combine([dense.retrieve(q, top_k=max_k), sparse.retrieve(q, top_k=max_k)], top_k=max_k), top_k=max_k),
    }

    results: Dict[str, List[float]] = {name: [0.0 for _ in top_ks] for name in pipelines.keys()}
    total = len(queries)

    for q, gt in zip(queries, gt_ids):
        for name, fn in pipelines.items():
            out = fn(q)
            ids = [str(it.get("id") or it.get("key") or it.get("text")) for it in out]
            for idx_k, k in enumerate(top_ks):
                if gt in ids[:k]:
                    results[name][idx_k] += 1.0

    for name in results.keys():
        results[name] = [v / total for v in results[name]]

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-docs", type=int, default=100)
    parser.add_argument("--num-topics", type=int, default=10)
    parser.add_argument("--vector-dim", type=int, default=64)
    parser.add_argument("--top-ks", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--out", type=str, default=None, help="Write JSON results to file")
    args = parser.parse_args()

    adapter, docs, queries, gt_ids, provider = build_dataset(args.num_docs, args.num_topics, args.vector_dim)
    res = evaluate(adapter, docs, queries, gt_ids, provider, top_ks=tuple(args.top_ks))

    print("Retrieval Recall@K results")
    for name, vals in res.items():
        ks = ",".join(str(k) for k in args.top_ks)
        print(f"{name}: K=[{ks}] -> {vals}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"config": vars(args), "results": res}, fh, indent=2)


if __name__ == "__main__":
    main()
