#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ranked lookup over the slide data, and the decision layer built on it.

The BM25 half answers "which rows match these words". The contextual half above
it answers "what should this slide look like": layout, typography, colour
treatment, and where a deck should break its own rhythm. Both read CSVs under
data/system.
"""

import csv
import re
from pathlib import Path
from math import log
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "system"
MAX_RESULTS = 3

CSV_CONFIG = {
    "strategy": {
        "file": "slide-strategies.csv",
        "search_cols": ["strategy_name", "keywords", "goal", "audience", "narrative_arc"],
        "output_cols": ["strategy_name", "keywords", "slide_count", "structure", "goal", "audience", "tone", "narrative_arc", "sources"]
    },
    "layout": {
        "file": "slide-layouts.csv",
        "search_cols": ["layout_name", "keywords", "use_case", "recommended_for"],
        "output_cols": ["layout_name", "keywords", "use_case", "content_zones", "visual_weight", "cta_placement", "recommended_for", "avoid_for", "css_structure"]
    },
    "copy": {
        "file": "slide-copy.csv",
        "search_cols": ["formula_name", "keywords", "use_case", "emotion_trigger", "slide_type"],
        "output_cols": ["formula_name", "keywords", "components", "use_case", "example_template", "emotion_trigger", "slide_type", "source"]
    },
    "chart": {
        "file": "slide-charts.csv",
        "search_cols": ["chart_type", "keywords", "best_for", "when_to_use", "slide_context"],
        "output_cols": ["chart_type", "keywords", "best_for", "data_type", "when_to_use", "when_to_avoid", "max_categories", "slide_context", "css_implementation", "accessibility_notes"]
    }
}

AVAILABLE_DOMAINS = list(CSV_CONFIG.keys())


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
        "strategy": ["pitch", "deck", "investor", "yc", "seed", "series", "demo", "sales", "webinar",
                     "conference", "board", "qbr", "all-hands", "duarte", "kawasaki", "structure"],
        "layout": ["slide", "layout", "grid", "column", "title", "hero", "section", "cta",
                   "screenshot", "quote", "timeline", "comparison", "pricing", "team"],
        "copy": ["headline", "copy", "formula", "aida", "pas", "hook", "cta", "benefit",
                 "objection", "proof", "testimonial", "urgency", "scarcity"],
        "chart": ["chart", "graph", "bar", "line", "pie", "funnel", "metrics", "data",
                  "visualization", "kpi", "trend", "comparison", "heatmap", "gauge"]
    }

    scores = {domain: sum(1 for kw in keywords if kw in query_lower) for domain, keywords in domain_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "strategy"


def search(query, domain=None, max_results=MAX_RESULTS):
    """Main search function with auto-domain detection"""
    if domain is None:
        domain = detect_domain(query)

    config = CSV_CONFIG.get(domain, CSV_CONFIG["strategy"])
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
    """Search across all domains for comprehensive results"""
    all_results = {}

    for domain in AVAILABLE_DOMAINS:
        result = search(query, domain, max_results)
        if result.get("count", 0) > 0:
            all_results[domain] = result

    return all_results


# The decision tables. Unlike the corpora above, each of these is a lookup
# rather than a ranking: a goal, a content type, an emotion, or a slide type
# names exactly one row, and `key_col` is the column that names it.
DECISION_CSV_CONFIG = {
    "layout-logic": {
        "file": "slide-layout-logic.csv",
        "key_col": "goal"
    },
    "typography": {
        "file": "slide-typography.csv",
        "key_col": "content_type"
    },
    "color-logic": {
        "file": "slide-color-logic.csv",
        "key_col": "emotion"
    },
    "backgrounds": {
        "file": "slide-backgrounds.csv",
        "key_col": "slide_type"
    }
}


def _load_decision_csv(csv_type):
    """Load a decision CSV and return as dict keyed by primary column."""
    config = DECISION_CSV_CONFIG.get(csv_type)
    if not config:
        return {}

    filepath = DATA_DIR / config["file"]
    if not filepath.exists():
        return {}

    data = _load_csv(filepath)
    return {row[config["key_col"]]: row for row in data if config["key_col"] in row}


def get_layout_for_goal(goal, previous_emotion=None):
    """The layout one slide goal calls for.

    A goal the table does not carry falls back to the feature grid, which is
    the shape that reads acceptably for most content. When the row is marked
    as a pattern break and the previous slide's emotion is known, the caller
    is handed both so it can contrast against it.
    """
    layouts = _load_decision_csv("layout-logic")
    row = layouts.get(goal, layouts.get("features", {}))

    result = dict(row) if row else {}

    if result.get("break_pattern") == "true" and previous_emotion:
        result["_pattern_break"] = True
        result["_contrast_with"] = previous_emotion

    return result


def get_typography_for_slide(slide_type, has_metrics=False, has_quote=False):
    """The type treatment one slide calls for.

    Metrics and quotes decide it outright, because both need a size and weight
    that no slide goal would otherwise ask for. Everything else is decided by
    the goal.
    """
    typography = _load_decision_csv("typography")

    if has_metrics:
        return typography.get("metric-callout", {})
    if has_quote:
        return typography.get("quote-block", {})

    # Several goals want the same treatment - hero and hook are one statement
    # slide, proof and agitation are both a number - so the map is many-to-one.
    type_map = {
        "hero": "hero-statement",
        "hook": "hero-statement",
        "title": "title-only",
        "problem": "subtitle-heavy",
        "agitation": "metric-callout",
        "solution": "subtitle-heavy",
        "features": "feature-grid",
        "proof": "metric-callout",
        "traction": "data-insight",
        "social": "quote-block",
        "testimonial": "testimonial",
        "pricing": "pricing",
        "team": "team",
        "cta": "cta-action",
        "comparison": "comparison",
        "timeline": "timeline",
    }

    content_type = type_map.get(slide_type, "feature-grid")
    return typography.get(content_type, {})


def get_color_for_emotion(emotion):
    """The colour treatment one emotional beat calls for, falling back to the
    neutral one so an unlabelled slide still gets a coherent surface."""
    colors = _load_decision_csv("color-logic")
    return colors.get(emotion, colors.get("clarity", {}))


def get_background_config(slide_type):
    """The background configuration for one slide type, empty when it has none."""
    backgrounds = _load_decision_csv("backgrounds")
    return backgrounds.get(slide_type, {})


def should_use_full_bleed(slide_index, total_slides, emotion):
    """Whether this slide takes a full-bleed background.

    Two or three in a deck read as emphasis; more and they read as the deck's
    default, which is the failure this exists to prevent. So they are limited
    to high-emotion beats at four spaced positions, and a deck under three
    slides gets none at all.
    """
    high_emotion_beats = ["hope", "urgency", "fear", "curiosity"]

    if emotion not in high_emotion_beats:
        return False

    if total_slides < 3:
        return False

    third = total_slides // 3
    strategic_positions = [1, third, third * 2, total_slides - 1]

    return slide_index in strategic_positions


def calculate_pattern_break(slide_index, total_slides, previous_emotion=None):
    """Whether this slide breaks the deck's visual rhythm.

    A deck that never varies reads as one long slide, so the breaks land at the
    thirds and wherever the emotional beat turns over. Under five slides there
    is no rhythm to break.
    """
    if total_slides < 5:
        return False

    third = total_slides // 3
    if slide_index in [third, third * 2]:
        return True

    # A turn between opposed beats is a break whether or not it falls on a third.
    contrasting_emotions = {
        "frustration": ["hope", "relief"],
        "hope": ["frustration", "fear"],
        "fear": ["hope", "relief"],
    }

    if previous_emotion in contrasting_emotions:
        return True

    return False


def search_with_context(query, slide_position=1, total_slides=9, previous_emotion=None):
    """Matching slides, plus what this one should look like where it sits.

    A slide is not designed in isolation: the same content wants a different
    layout, colour, and animation as the second slide of nine than as the
    eighth. `slide_position` is 1-based, and `previous_emotion` is what the
    slide before it was labelled, which is what a contrast is measured against.
    """
    base_results = search_all(query, max_results=2)

    # The domain detector answers which corpus, not which goal, so the words that
    # name a goal outright override it.
    goal = detect_domain(query.lower())
    if "problem" in query.lower():
        goal = "problem"
    elif "solution" in query.lower():
        goal = "solution"
    elif "cta" in query.lower() or "call to action" in query.lower():
        goal = "cta"
    elif "hook" in query.lower() or "title" in query.lower():
        goal = "hook"
    elif "traction" in query.lower() or "metric" in query.lower():
        goal = "traction"

    context = {
        "slide_position": slide_position,
        "total_slides": total_slides,
        "previous_emotion": previous_emotion,
        "inferred_goal": goal,
    }

    layout = get_layout_for_goal(goal, previous_emotion)
    if layout:
        context["recommended_layout"] = layout.get("layout_pattern")
        context["layout_direction"] = layout.get("direction")
        context["visual_weight"] = layout.get("visual_weight")
        context["use_background_image"] = layout.get("use_bg_image") == "true"

    typography = get_typography_for_slide(goal)
    if typography:
        context["typography"] = {
            "primary_size": typography.get("primary_size"),
            "secondary_size": typography.get("secondary_size"),
            "weight_contrast": typography.get("weight_contrast"),
        }

    # The layout row carries the emotional beat, so colour follows from layout
    # rather than from the query.
    emotion = layout.get("emotion", "clarity") if layout else "clarity"
    color = get_color_for_emotion(emotion)
    if color:
        context["color_treatment"] = {
            "background": color.get("background"),
            "text_color": color.get("text_color"),
            "accent_usage": color.get("accent_usage"),
            "card_style": color.get("card_style"),
        }

    context["should_break_pattern"] = calculate_pattern_break(
        slide_position, total_slides, previous_emotion
    )
    context["should_use_full_bleed"] = should_use_full_bleed(
        slide_position, total_slides, emotion
    )

    if context.get("use_background_image"):
        bg_config = get_background_config(goal)
        if bg_config:
            context["background"] = {
                "image_category": bg_config.get("image_category"),
                "overlay_style": bg_config.get("overlay_style"),
                "search_keywords": bg_config.get("search_keywords"),
            }

    animation_map = {
        "hook": "animate-fade-up",
        "problem": "animate-fade-up",
        "agitation": "animate-count animate-stagger",
        "solution": "animate-scale",
        "features": "animate-stagger",
        "traction": "animate-chart animate-count",
        "proof": "animate-stagger-scale",
        "social": "animate-fade-up",
        "cta": "animate-pulse",
    }
    context["animation_class"] = animation_map.get(goal, "animate-fade-up")

    return {
        "query": query,
        "context": context,
        "base_results": base_results,
    }
