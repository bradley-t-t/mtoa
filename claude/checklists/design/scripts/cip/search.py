#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Command line over the corporate-identity data in core.py.

Three shapes of answer: hits from one domain, hits from every domain, or a
whole brief assembled from an industry and a style. Every one of them takes
--json, since the brief is as often read by a generator as by a person.
"""

import argparse
import json
import sys
from pathlib import Path

# core.py sits beside this file rather than on the path, because the scripts
# directory is not a package and this is run by path.
sys.path.insert(0, str(Path(__file__).parent))
from core import search, search_all, get_cip_brief, CSV_CONFIG


def format_results(results, domain):
    """Hits as plain text, one block per hit, empty columns left out."""
    if not results:
        return "No results found."

    output = []
    for i, item in enumerate(results, 1):
        output.append(f"\n{'='*60}")
        output.append(f"Result {i}:")
        for key, value in item.items():
            if value:
                output.append(f"  {key}: {value}")
    return "\n".join(output)


def format_brief(brief):
    """A brief as plain text, in the order it would be worked through: what the
    industry expects, then the style, then the systems, then the deliverables."""
    output = []
    output.append(f"\n{'='*60}")
    output.append(f"CIP DESIGN BRIEF: {brief['brand_name']}")
    output.append(f"{'='*60}")

    if brief.get("industry"):
        output.append(f"\nINDUSTRY: {brief['industry'].get('Industry', 'N/A')}")
        output.append(f"   Style: {brief['industry'].get('CIP Style', 'N/A')}")
        output.append(f"   Mood: {brief['industry'].get('Mood', 'N/A')}")

    if brief.get("style"):
        output.append(f"\nDESIGN STYLE: {brief['style'].get('Style Name', 'N/A')}")
        output.append(f"   Description: {brief['style'].get('Description', 'N/A')}")
        output.append(f"   Materials: {brief['style'].get('Materials', 'N/A')}")
        output.append(f"   Finishes: {brief['style'].get('Finishes', 'N/A')}")

    if brief.get("color_system"):
        output.append(f"\nCOLOR SYSTEM:")
        output.append(f"   Primary: {brief['color_system'].get('primary', 'N/A')}")
        output.append(f"   Secondary: {brief['color_system'].get('secondary', 'N/A')}")

    output.append(f"\nTYPOGRAPHY: {brief.get('typography', 'N/A')}")

    if brief.get("recommended_deliverables"):
        output.append(f"\nRECOMMENDED DELIVERABLES:")
        for d in brief["recommended_deliverables"]:
            output.append(f"   - {d.get('Deliverable', 'N/A')}: {d.get('Description', '')[:60]}...")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Search CIP design guidelines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deliverables
  python search.py "business card"

  # One named domain
  python search.py "luxury elegant" --domain style

  # A whole brief
  python search.py "tech startup" --cip-brief -b "TechFlow"

  # Every domain
  python search.py "corporate professional" --all

  # As JSON
  python search.py "vehicle branding" --json
        """
    )

    parser.add_argument("query", help="Search query")
    parser.add_argument("--domain", "-d", choices=list(CSV_CONFIG.keys()),
                        help="Search domain (auto-detected if not specified)")
    parser.add_argument("--max", "-m", type=int, default=3, help="Max results (default: 3)")
    parser.add_argument("--all", "-a", action="store_true", help="Search all domains")
    parser.add_argument("--cip-brief", "-c", action="store_true", help="Generate CIP brief")
    parser.add_argument("--brand", "-b", default="BrandName", help="Brand name for CIP brief")
    parser.add_argument("--style", "-s", help="Style override for CIP brief")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.cip_brief:
        brief = get_cip_brief(args.brand, args.query, args.style)
        if args.json:
            print(json.dumps(brief, indent=2))
        else:
            print(format_brief(brief))
    elif args.all:
        results = search_all(args.query, args.max)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for domain, items in results.items():
                print(f"\n{'#'*60}")
                print(f"# {domain.upper()}")
                print(format_results(items, domain))
    else:
        result = search(args.query, args.domain, args.max)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\nDomain: {result['domain']}")
            print(f"Query: {result['query']}")
            print(f"Results: {result['count']}")
            print(format_results(result.get("results", []), result["domain"]))


if __name__ == "__main__":
    main()
