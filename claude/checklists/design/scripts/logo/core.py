#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ranked lookup over the logo data: styles, colour palettes, and industries.

Each domain is one CSV under data/logo and one entry in CSV_CONFIG saying which
of its columns are searched and which come back. Adding a domain is adding a
file and an entry; nothing below knows the domains by name.
"""

import csv
import re
from pathlib import Path
from math import log
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "logo"
MAX_RESULTS = 3

CSV_CONFIG = {
    "style": {
        "file": "styles.csv",
        "search_cols": ["Style Name", "Category", "Keywords", "Best For"],
        "output_cols": ["Style Name", "Category", "Keywords", "Primary Colors", "Secondary Colors", "Typography", "Effects", "Best For", "Avoid For", "Complexity", "Era"]
    },
    "color": {
        "file": "colors.csv",
        "search_cols": ["Palette Name", "Category", "Keywords", "Psychology", "Best For"],
        "output_cols": ["Palette Name", "Category", "Keywords", "Primary Hex", "Secondary Hex", "Accent Hex", "Background Hex", "Text Hex", "Psychology", "Best For", "Avoid For"]
    },
    "industry": {
        "file": "industries.csv",
        "search_cols": ["Industry", "Keywords", "Recommended Styles", "Mood"],
        "output_cols": ["Industry", "Keywords", "Recommended Styles", "Primary Colors", "Typography", "Common Symbols", "Mood", "Best Practices", "Avoid"]
    }
}


class BM25:
    """Okapi BM25 over a fixed corpus, so a query ranks rows of a CSV.

    `k1` caps how much repeating a term in one row can help it, and `b` sets
    how hard a long row is penalised for its length. The defaults are the
    values the algorithm is usually published with.

    Call `fit` once with the corpus, then `score` per query.
    """

    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.corpus = []
        self.doc_lengths = []
        self.avgdl = 0
        self.idf = {}
        self.doc_freqs = defaultdict(int)
        self.N = 0

    def tokenize(self, text):
        """The terms of one document. Anything under three characters is
        dropped: the corpora here are keyword columns, where the short tokens
        are articles and colour codes rather than anything a query means."""
        text = re.sub(r'[^\w\s]', ' ', str(text).lower())
        return [w for w in text.split() if len(w) > 2]

    def fit(self, documents):
        """Index the corpus. An empty corpus leaves the index empty and every
        later score at zero, rather than dividing by an average length of nought."""
        self.corpus = [self.tokenize(doc) for doc in documents]
        self.N = len(self.corpus)
        if self.N == 0:
            return
        self.doc_lengths = [len(doc) for doc in self.corpus]
        self.avgdl = sum(self.doc_lengths) / self.N

        for doc in self.corpus:
            seen = set()
            for word in doc:
                if word not in seen:
                    self.doc_freqs[word] += 1
                    seen.add(word)

        for word, freq in self.doc_freqs.items():
            self.idf[word] = log((self.N - freq + 0.5) / (freq + 0.5) + 1)

    def score(self, query):
        """(index, score) for every document, best first. A document sharing no
        term with the query scores zero, which is what callers cut on."""
        query_tokens = self.tokenize(query)
        scores = []

        for idx, doc in enumerate(self.corpus):
            score = 0
            doc_len = self.doc_lengths[idx]
            term_freqs = defaultdict(int)
            for word in doc:
                term_freqs[word] += 1

            for token in query_tokens:
                if token in self.idf:
                    tf = term_freqs[token]
                    idf = self.idf[token]
                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                    score += idf * numerator / denominator

            scores.append((idx, score))

        return sorted(scores, key=lambda x: x[1], reverse=True)


def _load_csv(filepath):
    """One dict per row, keyed by the header line."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def _search_csv(filepath, search_cols, output_cols, query, max_results):
    """Rank one CSV against a query.

    `search_cols` are joined into the text each row is matched on and
    `output_cols` are what a hit carries back, so a column can be searchable
    without being returned, or returned without being searched.
    """
    if not filepath.exists():
        return []

    data = _load_csv(filepath)

    documents = [" ".join(str(row.get(col, "")) for col in search_cols) for row in data]

    bm25 = BM25()
    bm25.fit(documents)
    ranked = bm25.score(query)

    results = []
    for idx, score in ranked[:max_results]:
        if score > 0:
            row = data[idx]
            results.append({col: row.get(col, "") for col in output_cols if col in row})

    return results


def detect_domain(query):
    """Which CSV a query is about, by counting its words against each domain's
    vocabulary. A query matching nothing falls to the domain most queries mean."""
    query_lower = query.lower()

    domain_keywords = {
        "style": ["style", "minimalist", "vintage", "modern", "retro", "geometric", "abstract", "emblem", "badge", "wordmark", "mascot", "luxury", "playful", "corporate"],
        "color": ["color", "palette", "hex", "#", "rgb", "blue", "red", "green", "gold", "warm", "cool", "vibrant", "pastel"],
        "industry": ["tech", "healthcare", "finance", "legal", "restaurant", "food", "fashion", "beauty", "education", "sports", "fitness", "real estate", "crypto", "gaming"]
    }

    scores = {domain: sum(1 for kw in keywords if kw in query_lower) for domain, keywords in domain_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "style"


def search(query, domain=None, max_results=MAX_RESULTS):
    """Hits from one domain, chosen from the query when none is named."""
    if domain is None:
        domain = detect_domain(query)

    config = CSV_CONFIG.get(domain, CSV_CONFIG["style"])
    filepath = DATA_DIR / config["file"]

    if not filepath.exists():
        return {"error": f"File not found: {filepath}", "domain": domain}

    results = _search_csv(filepath, config["search_cols"], config["output_cols"], query, max_results)

    return {
        "domain": domain,
        "query": query,
        "file": config["file"],
        "count": len(results),
        "results": results
    }


def search_all(query, max_results=2):
    """Hits from every domain, keyed by domain, empty domains left out."""
    all_results = {}
    for domain in CSV_CONFIG.keys():
        result = search(query, domain, max_results)
        if result.get("results"):
            all_results[domain] = result["results"]
    return all_results
