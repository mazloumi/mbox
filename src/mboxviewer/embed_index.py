"""Two decoupled, resumable background passes for the semantic tier:
build_chunks (extract + store chunk text) then build_embeddings (vectorize).
Kept separate from the core indexer so its contracts are untouched."""
import sys

from .reader import read_message, iter_attachments
from .extract import extract_text
from .indexer import _body_text
from .chunker import iter_chunks

CHUNK_COMMIT_EVERY = 200
EMBED_BATCH = 64


def build_chunks(settings, store, status):
    """Walk indexed messages, re-extract body+attachment text, store chunk rows.
    Resumable: a no-op if chunks already exist."""
    if store.count_chunks() > 0:
        status.finish()  # already chunked; the embed pass re-checks vectors
        return
    messages = store.iter_messages_for_chunking()
    status.start_chunking(len(messages))
    done = 0
    for row in messages:
        mid, offset, length = row["id"], row["offset"], row["length"]
        try:
            msg = read_message(settings.mbox_path, offset, length)
            subject = msg["subject"] or ""
            from_addr = msg["from"] or ""
            date = msg["date"] or ""
            body = _body_text(msg)
            atts = []
            for idx, filename, mime, payload in iter_attachments(msg):
                atts.append((idx, filename or "", extract_text(filename, mime, payload)))
            for ch in iter_chunks(subject, from_addr, date, body, atts):
                store.add_chunk(mid, ch.kind, ch.ord, ch.source_idx, ch.text)
        except Exception as exc:  # skip a bad message; keep going
            sys.stderr.write(f"chunk pass: skipping message {mid}: {exc}\n")
        done += 1
        if done % CHUNK_COMMIT_EVERY == 0:
            store.commit()
            status.update_chunks(done)
    store.commit()
    status.update_chunks(done)


def build_embeddings(settings, store, embedder, status):
    """Vectorize chunks lacking a vector. Resumable; records embed model/dim/backend."""
    store.ensure_vector_schema(embedder.dim)
    total = store.count_chunks()
    pending = store.count_chunks_without_vectors()
    status.start_embedding(total)
    status.update_vectors(total - pending)
    while True:
        batch = store.chunks_without_vectors(EMBED_BATCH)
        if not batch:
            break
        ids = [cid for cid, _ in batch]
        texts = [t for _, t in batch]
        vectors = embedder.embed_texts(texts)
        for cid, vec in zip(ids, vectors):
            store.add_vector(cid, vec)
        store.commit()
        status.update_vectors(status.snapshot()["vectors_done"] + len(batch))
    store.embed_meta_set(embedder.model_name, embedder.dim, settings.embed_backend)
    status.finish()
