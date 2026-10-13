#!/usr/bin/env python3
"""Holds a standalone HTML asset to the project's design tokens.

A slide or an infographic is one self-contained file, which is exactly the
shape that drifts: nothing links it to the stylesheet, so a colour typed
straight into a <style> block renders correctly and never moves again when the
theme does. assets/design-tokens.css is what a value is measured against.

Three places a literal is allowed, because in each the token is unavailable
rather than unused: inside <script>, where a charting library needs a value
rather than a variable; in an external URL; and in an rgba() built from a
colour the tokens already declare, which CSS gives no other way to make
translucent.

  python html-token-validator.py                     # every HTML asset
  python html-token-validator.py --type slides
  python html-token-validator.py path/to/file.html
"""

import re
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Five levels up: scripts/system, scripts, the skill, skills, the config root.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
TOKENS_JSON_PATH = PROJECT_ROOT / 'assets' / 'design-tokens.json'
TOKENS_CSS_PATH = PROJECT_ROOT / 'assets' / 'design-tokens.css'

# Where a project keeps the standalone pages this applies to. A directory that
# does not exist validates as empty rather than failing.
ASSET_DIRS = {
    'slides': PROJECT_ROOT / 'assets' / 'designs' / 'slides',
    'infographics': PROJECT_ROOT / 'assets' / 'infographics',
}

# A literal colour or face, in each spelling CSS accepts. The font patterns
# reject a leading `var` character by character, which is what keeps
# `font-family: var(--typography-font-body), sans-serif` out of the net.
FORBIDDEN_PATTERNS = [
    (r'#[0-9A-Fa-f]{3,8}\b', 'hex color'),
    (r'rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)', 'rgb color'),
    (r'rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*[\d.]+\s*\)', 'rgba color'),
    (r'hsl\([^)]+\)', 'hsl color'),
    (r"font-family:\s*'[^v][^a][^r][^']*',", 'hardcoded font'),  # Exclude var()
    (r'font-family:\s*"[^v][^a][^r][^"]*",', 'hardcoded font'),
]

# Black and white at any alpha. Both are absolutes rather than brand colours -
# a scrim, a shadow - and no token system declares them as such.
NEUTRAL_RGBA = ((0, 0, 0), (255, 255, 255))

# Allowed exceptions (external images, etc.)
ALLOWED_EXCEPTIONS = [
    'picsum.photos', 'cdn.simpleicons.org', 'youtube.com', 'ytimg.com',
]


class ValidationResult:
    """One file's verdict. An error fails the file; a warning is reported and
    does not, so a low token count can be surfaced without blocking."""
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed = True

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)


def load_css_variables() -> Dict[str, str]:
    """Every CSS variable the token file declares, by name. Empty when the file
    is absent, which leaves the rgba allowance at black and white alone."""
    variables = {}
    if TOKENS_CSS_PATH.exists():
        content = TOKENS_CSS_PATH.read_text()
        for match in re.finditer(r'(--[\w-]+):\s*([^;]+);', content):
            variables[match.group(1)] = match.group(2).strip()
    return variables


def is_inside_block(content: str, match_pos: int, open_tag: str, close_tag: str) -> bool:
    """Whether a position sits inside an open tag of this kind.

    Decided by which of the two tags appeared last before it, which is right
    for the blocks here because <style> and <script> do not nest.
    """
    pre = content[:match_pos]
    tag_open = pre.rfind(open_tag)
    tag_close = pre.rfind(close_tag)
    return tag_open > tag_close


def is_allowed_exception(context: str) -> bool:
    """Whether a literal sits next to a third-party URL, where a hex is part of
    somebody else's query string rather than a colour this project chose."""
    context_lower = context.lower()
    return any(exc in context_lower for exc in ALLOWED_EXCEPTIONS)


def brand_rgb() -> set:
    """Every colour the tokens declare, as an (r, g, b) triple.

    Read from the token file rather than listed here, so the allowance follows
    whatever palette the project actually declares.
    """
    triples = set(NEUTRAL_RGBA)
    for value in load_css_variables().values():
        hexes = re.findall(r'#([0-9A-Fa-f]{6})\b', value)
        for h in hexes:
            triples.add((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    return triples


def is_allowed_rgba(match_text: str) -> bool:
    """Whether an rgba() is a declared colour made translucent.

    CSS has no way to put an alpha on a var() holding a hex, so the only way to
    fade a token colour is to restate its channels. That is the one restatement
    this permits.
    """
    nums = re.findall(r'\d+', match_text)
    if len(nums) < 3:
        return False
    return tuple(int(n) for n in nums[:3]) in brand_rgb()


def get_context(content: str, pos: int, chars: int = 100) -> str:
    """The text around a match, which is what the allowances are judged on."""
    start = max(0, pos - chars)
    end = min(len(content), pos + chars)
    return content[start:end]


def validate_html(content: str, file_path: Path, verbose: bool = False) -> ValidationResult:
    """One page's verdict: the token file is linked, nothing styling the page is
    written as a literal, and the page reaches for tokens often enough to have
    meant it. `verbose` records each allowance as a warning."""
    result = ValidationResult(file_path)

    if 'design-tokens.css' not in content:
        result.add_error("Missing design-tokens.css import")

    for pattern, description in FORBIDDEN_PATTERNS:
        for match in re.finditer(pattern, content):
            match_text = match.group()
            match_pos = match.start()
            context = get_context(content, match_pos)

            # A charting library is handed values, not variables, so a literal
            # inside <script> is the only way to give it the palette.
            if is_inside_block(content, match_pos, '<script', '</script>'):
                if verbose:
                    result.add_warning(f"Allowed in <script>: {match_text}")
                continue

            if is_allowed_exception(context):
                if verbose:
                    result.add_warning(f"Allowed external: {match_text}")
                continue

            if description == 'rgba color' and is_allowed_rgba(match_text):
                if verbose:
                    result.add_warning(f"Allowed brand rgba: {match_text}")
                continue

            # A literal inside var(--token, #fff) is the fallback, which is the
            # correct place for one.
            if 'var(' in context and match_text in context:
                var_pattern = rf'var\([^)]*{re.escape(match_text)}[^)]*\)'
                if re.search(var_pattern, context):
                    continue

            # Only a value that styles this page counts. The same hex in body
            # text or an attribute is content.
            if is_inside_block(content, match_pos, '<style', '</style>'):
                result.add_error(f"Hardcoded {description} in <style>: {match_text}")
            elif 'style="' in context:
                result.add_error(f"Hardcoded {description} in inline style: {match_text}")

    token_patterns = [
        r'var\(--color-',
        r'var\(--primitive-',
        r'var\(--typography-',
        r'var\(--card-',
        r'var\(--button-',
    ]
    token_count = sum(len(re.findall(p, content)) for p in token_patterns)

    if token_count < 5:
        result.add_warning(f"Low token usage ({token_count} var() references). Consider using more design tokens.")

    return result


def validate_file(file_path: Path, verbose: bool = False) -> ValidationResult:
    """Validate a single HTML file."""
    if not file_path.exists():
        result = ValidationResult(file_path)
        result.add_error("File not found")
        return result

    content = file_path.read_text()
    return validate_html(content, file_path, verbose)


def validate_directory(dir_path: Path, verbose: bool = False) -> List[ValidationResult]:
    """Validate all HTML files in a directory."""
    results = []
    if dir_path.exists():
        for html_file in sorted(dir_path.glob('*.html')):
            results.append(validate_file(html_file, verbose))
    return results


def print_result(result: ValidationResult, verbose: bool = False):
    """One file's line, with its first few errors under it."""
    status = "✓" if result.passed else "✗"
    print(f"  {status} {result.file_path.name}")

    if result.errors:
        # A page missing its token import fails on every colour in it, so the
        # list is cut and counted rather than printed whole.
        for error in result.errors[:5]:
            print(f"      ├─ {error}")
        if len(result.errors) > 5:
            print(f"      └─ ... and {len(result.errors) - 5} more errors")

    if verbose and result.warnings:
        for warning in result.warnings[:3]:
            print(f"      [warn] {warning}")


def print_summary(all_results: Dict[str, List[ValidationResult]]):
    """The whole run, grouped by asset type. Returns whether everything passed."""
    total_files = 0
    total_passed = 0
    total_errors = 0

    print("\n" + "=" * 60)
    print("HTML DESIGN TOKEN VALIDATION SUMMARY")
    print("=" * 60)

    for asset_type, results in all_results.items():
        if not results:
            continue

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        errors = sum(len(r.errors) for r in results)

        total_files += len(results)
        total_passed += passed
        total_errors += errors

        status = "✓" if failed == 0 else "✗"
        print(f"\n{status} {asset_type.upper()}: {passed}/{len(results)} passed")

        for result in results:
            if not result.passed:
                print_result(result)

    print("\n" + "-" * 60)
    if total_errors == 0:
        print(f"✓ ALL PASSED: {total_passed}/{total_files} files valid")
    else:
        print(f"✗ FAILED: {total_files - total_passed}/{total_files} files have issues ({total_errors} total errors)")
    print("-" * 60)

    return total_errors == 0


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Validate HTML assets for design token compliance',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # every HTML asset
  %(prog)s --type slides
  %(prog)s --type infographics
  %(prog)s path/to/file.html
  %(prog)s --colors                  # the tokens themselves
"""
    )
    parser.add_argument('files', nargs='*', help='Specific HTML files to validate')
    parser.add_argument('-t', '--type', choices=['slides', 'infographics', 'all'],
                        default='all', help='Asset type to validate')
    parser.add_argument('-v', '--verbose', action='store_true', help='Show warnings')
    parser.add_argument('--colors', action='store_true', help='Print CSS variables from tokens')

    args = parser.parse_args()

    if args.colors:
        variables = load_css_variables()
        print("\nDesign Tokens (from design-tokens.css):")
        print("-" * 40)
        for name, value in sorted(variables.items())[:30]:
            print(f"  {name}: {value}")
        if len(variables) > 30:
            print(f"  ... and {len(variables) - 30} more")
        return

    all_results: Dict[str, List[ValidationResult]] = {}

    if args.files:
        results = []
        for file_path in args.files:
            path = Path(file_path)
            if path.exists():
                results.append(validate_file(path, args.verbose))
            else:
                result = ValidationResult(path)
                result.add_error("File not found")
                results.append(result)
        all_results['specified'] = results
    else:
        types_to_check = ASSET_DIRS.keys() if args.type == 'all' else [args.type]

        for asset_type in types_to_check:
            if asset_type in ASSET_DIRS:
                results = validate_directory(ASSET_DIRS[asset_type], args.verbose)
                all_results[asset_type] = results

    success = print_summary(all_results)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
