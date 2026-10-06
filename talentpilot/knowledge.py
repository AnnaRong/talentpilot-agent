"""Source-cited local retrieval. Documents are data, never tool instructions."""
from __future__ import annotations

import math
import re
from collections import Counter

from .platform_store import digest, utcnow


def initialize(store):
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS knowledge (
          id TEXT PRIMARY KEY, department TEXT NOT NULL, payload BLOB NOT NULL, created_at TEXT NOT NULL)""")


def tokens(text):
    normalized = text.casefold()
    words = re.findall(r"[a-z0-9_]{2,}", normalized)
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        words.extend(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
    return words


def add(store, actor, department, source_id, title, text):
    if not all(isinstance(v, str) and v.strip() for v in (department, source_id, title, text)):
        raise ValueError("Department, source ID, title and source text are required")
    if len(text) > 100000 or len(title) > 300 or len(source_id) > 500:
        raise ValueError("Knowledge document exceeds local size limits")
    payload = {"source_id": source_id, "title": title, "text": text}
    document_id = digest([department, payload])
    with store.transaction() as cx:
        cx.execute("INSERT OR IGNORE INTO knowledge VALUES (?,?,?,?)",
                   (document_id, department, store.seal(payload), utcnow()))
        store.audit(cx, actor, "knowledge.add", document_id)
    return {"document_id": document_id}


def search(store, departments, query, limit=5):
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or not 1 <= limit <= 20:
        raise ValueError("Query and limit outside allowed bounds")
    with store.transaction() as cx:
        rows = cx.execute("SELECT * FROM knowledge ORDER BY created_at DESC LIMIT 1000").fetchall()
    chunks = []
    for row in rows:
        if "*" not in departments and row["department"] not in departments:
            continue
        document = store.unseal(row["payload"])
        for start in range(0, len(document["text"]), 700):
            text = document["text"][start:start + 900]
            chunks.append({"document_id": row["id"], "source_id": document["source_id"],
                           "title": document["title"], "start": start, "end": start + len(text),
                           "quote": text, "terms": Counter(tokens(text))})
    terms = set(tokens(query))
    df = {term: sum(term in c["terms"] for c in chunks) for term in terms}
    for c in chunks:
        c["score"] = sum((1 + math.log(c["terms"][t])) * math.log(1 + len(chunks) / (1 + df[t]))
                         for t in terms if c["terms"][t])
        del c["terms"]
    return {"method": "local_lexical_retrieval", "generated_answer": False,
            "results": sorted((c for c in chunks if c["score"] > 0),
                              key=lambda c: (-c["score"], c["document_id"], c["start"]))[:limit],
            "egress": False, "source_instructions_executed": False}
