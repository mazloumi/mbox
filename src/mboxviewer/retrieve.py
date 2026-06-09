"""Hybrid retrieval shared by both tiers: vector KNN + FTS5 BM25, fused with
reciprocal-rank fusion (RRF). `search` feeds the search UI; `retrieve_context`
feeds the assistant."""
from dataclasses import dataclass
from typing import List, Optional

RRF_K = 60          # standard RRF damping constant
POOL = 50           # candidates pulled from each ranker before fusion


@dataclass
class Snippet:
    message_id: int
    subject: Optional[str]
    from_addr: Optional[str]
    date: Optional[str]
    text: str           # the best matching chunk's text


def _fused_message_order(store, embedder, query):
    """Return [(message_id, best_chunk_id_or_None)] ranked by RRF over vector+FTS."""
    qvec = embedder.embed_texts([query])[0]
    knn = store.knn_search(qvec, POOL)          # [(chunk_id, message_id, distance)]
    # Vector ranking at message level: first time a message appears = its best rank.
    vec_rank = {}
    best_chunk = {}
    for rank, (chunk_id, mid, _dist) in enumerate(knn):
        if mid not in vec_rank:
            vec_rank[mid] = rank
            best_chunk[mid] = chunk_id
    fts_ids = store.search_fts_messages(query, POOL)
    fts_rank = {mid: rank for rank, mid in enumerate(fts_ids)}

    scores = {}
    for mid, rank in vec_rank.items():
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    for mid, rank in fts_rank.items():
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)

    order = sorted(scores, key=lambda m: scores[m], reverse=True)
    return [(mid, best_chunk.get(mid)) for mid in order]


def search(store, embedder, query, limit):
    """Ranked message rows for the semantic search UI."""
    fused = _fused_message_order(store, embedder, query)[:limit]
    out = []
    for mid, _chunk in fused:
        row = store.get_message_row(mid)
        if row is not None:
            out.append(row)
    return out


def retrieve_context(store, embedder, query, budget_chars=12000, max_snippets=20):
    """Snippets (best chunk per message) for the assistant, within a char budget."""
    fused = _fused_message_order(store, embedder, query)
    snippets = []
    used = 0
    for mid, chunk_id in fused:
        if len(snippets) >= max_snippets:
            break
        text = store.get_chunk_text(chunk_id) if chunk_id is not None else None
        if not text:
            row = store.get_message_row(mid)
            text = (row["preview"] or "") if row is not None else ""
        if not text:
            continue
        if used and used + len(text) > budget_chars:
            break
        row = store.get_message_row(mid)
        snippets.append(Snippet(
            message_id=mid,
            subject=row["subject"] if row else None,
            from_addr=row["from_addr"] if row else None,
            date=row["date"] if row else None,
            text=text))
        used += len(text)
    return snippets
