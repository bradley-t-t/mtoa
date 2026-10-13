#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command line over the logo data in core.py.

Either hits from one domain, or a brief that pulls an industry, a style, and a
palette together into the one document a generation prompt is written from.

    python search.py "<query>" [--domain style|color|industry] [--max-results 3]
    python search.py "<query>" --design-brief [-p "Brand Name"]
"""

import argparse
from core import CSV_CONFIG, MAX_RESULTS, search, search_all


def format_output(result):
    """Hits as Markdown. Long cells are cut at 300 characters: the descriptive
    columns run to paragraphs, and a brief is read for its shape rather than
    its prose."""
    if "error" in result:
        return f"Error: {result['error']}"

    output = []
    output.append(f"## Logo Design Search Results")
    output.append(f"**Domain:** {result['domain']} | **Query:** {result['query']}")
    output.append(f"**Source:** {result['file']} | **Found:** {result['count']} results\n")

    for i, row in enumerate(result['results'], 1):
        output.append(f"### Result {i}")
        for key, value in row.items():
            value_str = str(value)
            if len(value_str) > 300:
                value_str = value_str[:300] + "..."
            output.append(f"- **{key}:** {value_str}")
        output.append("")

    return "\n".join(output)


def generate_design_brief(query, brand_name=None):
    """One brief drawn from all three domains, so a single query answers what to
    draw, how to colour it, and what the industry expects."""
    results = search_all(query, max_results=2)

    output = []
    output.append("=" * 60)
    if brand_name:
        output.append(f"  LOGO DESIGN BRIEF: {brand_name.upper()}")
    else:
        output.append("  LOGO DESIGN BRIEF")
    output.append("=" * 60)
    output.append(f"  Query: {query}")
    output.append("=" * 60)
    output.append("")

    if "industry" in results:
        output.append("## INDUSTRY ANALYSIS")
        for r in results["industry"]:
            output.append(f"**Industry:** {r.get('Industry', 'N/A')}")
            output.append(f"- Recommended Styles: {r.get('Recommended Styles', 'N/A')}")
            output.append(f"- Colors: {r.get('Primary Colors', 'N/A')}")
            output.append(f"- Typography: {r.get('Typography', 'N/A')}")
            output.append(f"- Symbols: {r.get('Common Symbols', 'N/A')}")
            output.append(f"- Mood: {r.get('Mood', 'N/A')}")
            output.append(f"- Best Practices: {r.get('Best Practices', 'N/A')}")
            output.append(f"- Avoid: {r.get('Avoid', 'N/A')}")
            output.append("")

    if "style" in results:
        output.append("## STYLE RECOMMENDATIONS")
        for r in results["style"]:
            output.append(f"**{r.get('Style Name', 'N/A')}** ({r.get('Category', 'N/A')})")
            output.append(f"- Colors: {r.get('Primary Colors', 'N/A')} | {r.get('Secondary Colors', 'N/A')}")
            output.append(f"- Typography: {r.get('Typography', 'N/A')}")
            output.append(f"- Effects: {r.get('Effects', 'N/A')}")
            output.append(f"- Best For: {r.get('Best For', 'N/A')}")
            output.append(f"- Complexity: {r.get('Complexity', 'N/A')}")
            output.append("")

    if "color" in results:
        output.append("## COLOR PALETTE OPTIONS")
        for r in results["color"]:
            output.append(f"**{r.get('Palette Name', 'N/A')}**")
            output.append(f"- Primary: {r.get('Primary Hex', 'N/A')}")
            output.append(f"- Secondary: {r.get('Secondary Hex', 'N/A')}")
            output.append(f"- Accent: {r.get('Accent Hex', 'N/A')}")
            output.append(f"- Background: {r.get('Background Hex', 'N/A')}")
            output.append(f"- Psychology: {r.get('Psychology', 'N/A')}")
            output.append("")

    output.append("=" * 60)
    return "\n".join(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logo Design Search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--domain", "-d", choices=list(CSV_CONFIG.keys()), help="Search domain")
    parser.add_argument("--max-results", "-n", type=int, default=MAX_RESULTS, help="Max results (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--design-brief", "-db", action="store_true", help="Assemble a full design brief")
    parser.add_argument("--brand-name", "-p", type=str, default=None, help="Brand name for design brief")

    args = parser.parse_args()

    if args.design_brief:
        result = generate_design_brief(args.query, args.brand_name)
        print(result)
    else:
        result = search(args.query, args.domain, args.max_results)
        if args.json:
            import json
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(format_output(result))
