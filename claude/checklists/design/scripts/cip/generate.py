#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Renders corporate-identity mockups through Gemini's image models.

Two modes, and the difference matters to the result. Given a logo file, the
call is image editing: the real mark is passed alongside the prompt and the
model places it. Without one, the model invents a mark that resembles the
brief, which is fine for judging a style and wrong for anything that will be
shown to a client.

The prompt is assembled from the same data search.py reads, so a mockup and the
brief describing it never disagree about the colours or the materials.

    flash  gemini-2.5-flash-image        the default
    pro    gemini-3-pro-image-preview    slower, renders small text legibly
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# core.py sits beside this file rather than on the path, because the scripts
# directory is not a package and this is run by path.
sys.path.insert(0, str(Path(__file__).parent))
from core import search, get_cip_brief

MODELS = {
    "flash": "gemini-2.5-flash-image",      # Nano Banana Flash - fast, default
    "pro": "gemini-3-pro-image-preview"      # Nano Banana Pro - quality, 4K text
}
DEFAULT_MODEL = "flash"


def load_logo_image(logo_path):
    """The logo as an RGB image, or None with the reason printed.

    Transparency is flattened onto white rather than dropped: the model is
    handed a rectangle either way, and an unflattened alpha channel arrives as
    black, which it then reproduces as a black plate behind the mark.
    """
    try:
        from PIL import Image
    except ImportError:
        print("Error: pillow package not installed.")
        print("Install with: pip install pillow")
        return None

    logo_path = Path(logo_path)
    if not logo_path.exists():
        print(f"Error: Logo file not found: {logo_path}")
        return None

    try:
        img = Image.open(logo_path)
        if img.mode in ('RGBA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[3])
            else:
                background.paste(img)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        return img
    except Exception as e:
        print(f"Error loading logo: {e}")
        return None

def load_env():
    """Read the API key out of whichever .env file carries it.

    Searched nearest first, and an existing environment variable is never
    overwritten, so a key exported for one run wins over every file.
    """
    env_paths = [
        Path(__file__).parent.parent.parent / ".env",
        Path.home() / ".claude" / "skills" / ".env",
        Path.home() / ".claude" / ".env"
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        if key not in os.environ:
                            os.environ[key] = value.strip('"\'')

load_env()


def build_cip_prompt(deliverable, brand_name, style=None, industry=None, mockup=None, use_logo_image=False):
    """The prompt for one mockup, and the data it was built from.

    Each of style, industry, and mockup context is looked up rather than taken
    literally, so "consulting" reaches the industry's own palette and typography.
    `use_logo_image` changes the prompt rather than the data: an editing prompt
    has to forbid the model from redrawing the mark it was given.
    """
    deliverable_info = search(deliverable, "deliverable", 1)
    deliverable_data = deliverable_info.get("results", [{}])[0] if deliverable_info.get("results") else {}

    style_info = search(style or "corporate minimal", "style", 1) if style else {}
    style_data = style_info.get("results", [{}])[0] if style_info.get("results") else {}

    industry_info = search(industry or "technology", "industry", 1) if industry else {}
    industry_data = industry_info.get("results", [{}])[0] if industry_info.get("results") else {}

    mockup_context = deliverable_data.get("Mockup Context", "clean professional")
    if mockup:
        mockup_info = search(mockup, "mockup", 1)
        if mockup_info.get("results"):
            mockup_data = mockup_info["results"][0]
            mockup_context = mockup_data.get("Scene Description", mockup_context)

    deliverable_name = deliverable_data.get("Deliverable", deliverable)
    description = deliverable_data.get("Description", "")
    dimensions = deliverable_data.get("Dimensions", "")
    logo_placement = deliverable_data.get("Logo Placement", "center")

    style_name = style_data.get("Style Name", style or "corporate")
    primary_colors = style_data.get("Primary Colors", industry_data.get("Primary Colors", "#0F172A #FFFFFF"))
    typography = style_data.get("Typography", industry_data.get("Typography", "clean sans-serif"))
    materials = style_data.get("Materials", "premium quality")
    finishes = style_data.get("Finishes", "professional")

    mood = style_data.get("Mood", industry_data.get("Mood", "professional"))

    if use_logo_image:
        prompt_parts = [
            f"Create a professional corporate identity mockup photograph of a {deliverable_name}",
            f"Use the EXACT logo from the provided image - do NOT modify or recreate the logo",
            f"The logo MUST appear exactly as shown in the input image",
            f"Place the logo on the {deliverable_name} at: {logo_placement}",
            f"Brand name: '{brand_name}'",
            f"{description}" if description else "",
            f"Design style: {style_name}",
            f"Color scheme matching the logo colors",
            f"Materials: {materials} with {finishes} finish",
            f"Setting: {mockup_context}",
            f"Mood: {mood}",
            "Photorealistic product photography",
            "Soft natural lighting, professional studio quality",
            "8K resolution, sharp details"
        ]
    else:
        prompt_parts = [
            f"Professional corporate identity mockup photograph",
            f"showing {deliverable_name} for brand '{brand_name}'",
            f"{description}" if description else "",
            f"{style_name} design style",
            f"using colors {primary_colors}",
            f"{typography} typography",
            f"logo placement: {logo_placement}",
            f"{materials} materials with {finishes} finish",
            f"{mockup_context} setting",
            f"{mood} mood",
            "photorealistic product photography",
            "soft natural lighting",
            "high quality professional shot",
            "8k resolution detailed"
        ]

    prompt = ", ".join([p for p in prompt_parts if p])

    return {
        "prompt": prompt,
        "deliverable": deliverable_name,
        "style": style_name,
        "brand": brand_name,
        "colors": primary_colors,
        "mockup_context": mockup_context,
        "logo_placement": logo_placement
    }


def generate_with_nano_banana(prompt_data, output_dir=None, model_key="flash", aspect_ratio="1:1", logo_image=None):
    """Render one mockup and write it out. Returns the path, or None.

    Passing `logo_image` switches the call from generation to editing. The file
    is named from the brand, the deliverable, and the time, so a set generated
    twice does not overwrite itself.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("Error: google-genai package not installed.")
        print("Install with: pip install google-genai")
        return None

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY or GOOGLE_API_KEY not set")
        return None

    client = genai.Client(api_key=api_key)

    prompt = prompt_data["prompt"]
    model_name = MODELS.get(model_key, MODELS[DEFAULT_MODEL])

    mode = "image-editing" if logo_image else "text-to-image"

    print(f"\nGenerating CIP mockup...")
    print(f"   Mode: {mode}")
    print(f"   Deliverable: {prompt_data['deliverable']}")
    print(f"   Brand: {prompt_data['brand']}")
    print(f"   Style: {prompt_data['style']}")
    print(f"   Model: {model_name}")
    print(f"   Context: {prompt_data['mockup_context']}")
    if logo_image:
        print(f"   Logo: Using provided image ({logo_image.size[0]}x{logo_image.size[1]})")

    try:
        if logo_image:
            contents = [prompt, logo_image]
        else:
            contents = prompt

        # Use generate_content with response_modalities=['IMAGE'] for Nano Banana
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                # The API rejects a lowercase modality name.
                response_modalities=['IMAGE'],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio
                )
            )
        )

        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    output_dir = output_dir or Path.cwd()
                    output_dir = Path(output_dir)
                    output_dir.mkdir(parents=True, exist_ok=True)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    brand_slug = prompt_data["brand"].lower().replace(" ", "-")
                    deliverable_slug = prompt_data["deliverable"].lower().replace(" ", "-")
                    filename = f"{brand_slug}-{deliverable_slug}-{timestamp}.png"
                    filepath = output_dir / filename

                    image_data = part.inline_data.data
                    with open(filepath, "wb") as f:
                        f.write(image_data)

                    print(f"\nGenerated: {filepath}")
                    return str(filepath)

        print("No image generated in response")
        return None

    except Exception as e:
        print(f"Error generating image: {e}")
        return None


def generate_cip_set(brand_name, industry, style=None, deliverables=None, output_dir=None, model_key="flash", logo_path=None, aspect_ratio="1:1"):
    """Render one mockup per deliverable, sharing one style across them all.

    The style is resolved once from the brief rather than per deliverable, or a
    card and a van would each pick their own and the set would not read as one
    identity. A deliverable that fails to render is left out rather than
    stopping the rest.
    """

    logo_image = None
    if logo_path:
        logo_image = load_logo_image(logo_path)
        if not logo_image:
            print("Warning: Could not load logo, falling back to text-to-image mode")

    brief = get_cip_brief(brand_name, industry, style)

    if not deliverables:
        deliverables = ["business card", "letterhead", "office signage", "vehicle", "polo shirt"]

    results = []
    for deliverable in deliverables:
        prompt_data = build_cip_prompt(
            deliverable=deliverable,
            brand_name=brand_name,
            style=brief.get("style", {}).get("Style Name"),
            industry=industry,
            use_logo_image=(logo_image is not None)
        )

        filepath = generate_with_nano_banana(
            prompt_data,
            output_dir,
            model_key=model_key,
            aspect_ratio=aspect_ratio,
            logo_image=logo_image
        )
        if filepath:
            results.append({
                "deliverable": deliverable,
                "filepath": filepath,
                "prompt": prompt_data["prompt"]
            })

    return results


def check_logo_required(brand_name, skip_prompt=False):
    """Ask what to do about a missing logo: 'continue', 'generate', or 'exit'.

    A closed stdin answers 'continue', so a scripted run does not hang on a
    prompt nobody is there to read.
    """
    if skip_prompt:
        return 'continue'

    print(f"\nNo logo image provided for '{brand_name}'")
    print("   Without a logo, AI will generate its own interpretation of the brand logo.")
    print("")
    print("   Options:")
    print("   1. Continue without a logo, letting the model invent one")
    print("   2. Generate a logo first, with the logo scripts in this skill")
    print("   3. Exit and provide a logo path with --logo")
    print("")

    try:
        choice = input("   Enter choice [1/2/3] (default: 1): ").strip()
        if choice == '2':
            return 'generate'
        elif choice == '3':
            return 'exit'
        return 'continue'
    except (EOFError, KeyboardInterrupt):
        return 'continue'


def main():
    parser = argparse.ArgumentParser(
        description="Generate CIP mockups using Gemini Nano Banana",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate with brand logo (RECOMMENDED)
  python generate.py --brand "TopGroup" --logo /path/to/logo.png --deliverable "business card"

  # Generate CIP set with logo
  python generate.py --brand "TopGroup" --logo /path/to/logo.png --industry "consulting" --set

  # Generate without logo (AI interprets brand)
  python generate.py --brand "TechFlow" --deliverable "business card" --no-logo-prompt

  # Generate with Pro model (higher quality, 4K text)
  python generate.py --brand "TechFlow" --logo logo.png --deliverable "business card" --model pro

  # Specify output directory and aspect ratio
  python generate.py --brand "MyBrand" --logo logo.png --deliverable "vehicle" --output ./mockups --ratio 16:9

Models:
  flash (default): gemini-2.5-flash-image - Fast, cost-effective
  pro: gemini-3-pro-image-preview - Quality, 4K text rendering

Image Editing Mode:
  When --logo is provided, uses Gemini's text-and-image-to-image capability
  to incorporate your ACTUAL logo into the CIP mockups.
        """
    )

    parser.add_argument("--brand", "-b", required=True, help="Brand name")
    parser.add_argument("--logo", "-l", help="Path to brand logo image (enables image editing mode)")
    parser.add_argument("--deliverable", "-d", help="Single deliverable to generate")
    parser.add_argument("--deliverables", help="Comma-separated list of deliverables")
    parser.add_argument("--industry", "-i", default="technology", help="Industry type")
    parser.add_argument("--style", "-s", help="Design style")
    parser.add_argument("--mockup", "-m", help="Mockup context")
    parser.add_argument("--set", action="store_true", help="Generate full CIP set")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--model", default="flash", choices=["flash", "pro"], help="Model: flash (fast) or pro (quality)")
    parser.add_argument("--ratio", default="1:1", help="Aspect ratio (1:1, 16:9, 4:3, etc.)")
    parser.add_argument("--prompt-only", action="store_true", help="Only show prompt, don't generate")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--no-logo-prompt", action="store_true", help="Skip logo prompt, proceed without logo")

    args = parser.parse_args()

    logo_image = None
    if args.logo:
        logo_image = load_logo_image(args.logo)
        if not logo_image:
            print("Error: Could not load logo image")
            sys.exit(1)
    elif not args.prompt_only:
        action = check_logo_required(args.brand, skip_prompt=args.no_logo_prompt)
        if action == 'generate':
            print("\nTo generate a logo, use the logo-design skill:")
            print(f"   python ~/.claude/checklists/design/scripts/logo/generate.py --brand \"{args.brand}\" --industry \"{args.industry}\"")
            print("\n   Then re-run this command with --logo <generated_logo.png>")
            sys.exit(0)
        elif action == 'exit':
            print("\n   Provide logo with: --logo /path/to/your/logo.png")
            sys.exit(0)

    use_logo = logo_image is not None

    if args.set or args.deliverables:
        deliverables = args.deliverables.split(",") if args.deliverables else None

        if args.prompt_only:
            results = []
            deliverables = deliverables or ["business card", "letterhead", "office signage", "vehicle", "polo shirt"]
            for d in deliverables:
                prompt_data = build_cip_prompt(d, args.brand, args.style, args.industry, args.mockup, use_logo_image=use_logo)
                results.append(prompt_data)
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                for r in results:
                    print(f"\n{r['deliverable']}:\n{r['prompt']}\n")
        else:
            results = generate_cip_set(
                args.brand, args.industry, args.style, deliverables, args.output,
                model_key=args.model, logo_path=args.logo, aspect_ratio=args.ratio
            )
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                print(f"\nGenerated {len(results)} CIP mockups")
    else:
        deliverable = args.deliverable or "business card"
        prompt_data = build_cip_prompt(deliverable, args.brand, args.style, args.industry, args.mockup, use_logo_image=use_logo)

        if args.prompt_only:
            if args.json:
                print(json.dumps(prompt_data, indent=2))
            else:
                print(f"\nPrompt:\n{prompt_data['prompt']}")
        else:
            filepath = generate_with_nano_banana(
                prompt_data, args.output, model_key=args.model,
                aspect_ratio=args.ratio, logo_image=logo_image
            )
            if args.json:
                print(json.dumps({"filepath": filepath, **prompt_data}, indent=2))


if __name__ == "__main__":
    main()
