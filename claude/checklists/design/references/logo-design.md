# Logo Design Reference

AI-powered logo design with 55+ styles, 30 color palettes, 25 industry guides. Uses Gemini Nano Banana models.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/logo/search.py` | Search styles, colors, industries; generate design briefs |
| `scripts/logo/generate.py` | Generate logos with Gemini Nano Banana |
| `scripts/logo/core.py` | BM25 search engine for logo data |

## Commands

### Design Brief (Start Here)

```bash
python3 ~/.claude/checklists/design/scripts/logo/search.py "tech startup modern" --design-brief -p "BrandName"
```

### Search Domains

```bash
# Styles
python3 ~/.claude/checklists/design/scripts/logo/search.py "minimalist clean" --domain style

# Color palettes
python3 ~/.claude/checklists/design/scripts/logo/search.py "tech professional" --domain color

# Industry guidelines
python3 ~/.claude/checklists/design/scripts/logo/search.py "healthcare medical" --domain industry
```

### Generate Logo

Generate against white, deliver with alpha. White gives the model a clean field to draw a
mark on; a white box behind the mark on every surface that is not white is a broken
deliverable. `scripts/logo/transparent.py` floods the background in from the border, so
white inside the mark — a counter, a knockout, a highlight — survives. Generation runs it
automatically; run it by hand on anything that arrived another way.

```bash
python3 ~/.claude/checklists/design/scripts/logo/generate.py --brand "TechFlow" --style minimalist --industry tech
python3 ~/.claude/checklists/design/scripts/logo/generate.py --prompt "coffee shop vintage badge" --style vintage
```

Options: `--style`, `--industry`, `--prompt`

## Available Styles

| Category | Styles |
|----------|--------|
| General | Minimalist, Wordmark, Lettermark, Pictorial Mark, Abstract Mark, Mascot, Emblem, Combination Mark |
| Aesthetic | Vintage/Retro, Art Deco, Luxury, Playful, Corporate, Organic, Neon, Grunge, Watercolor |
| Modern | Gradient, Flat Design, 3D/Isometric, Geometric, Line Art, Duotone, Motion-Ready |
| Clever | Negative Space, Monoline, Split/Fragmented, Responsive/Adaptive |

## Color Psychology

| Color | Psychology | Best For |
|-------|------------|----------|
| Blue | Trust, stability | Finance, tech, healthcare |
| Green | Growth, natural | Eco, wellness, organic |
| Red | Energy, passion | Food, sports, entertainment |
| Gold | Luxury, premium | Fashion, jewelry, hotels |
| Purple | Creative, innovative | Beauty, creative, tech |

## Industry Defaults

| Industry | Style | Colors | Typography |
|----------|-------|--------|------------|
| Tech | Minimalist, Abstract | Blues, purples, gradients | Geometric sans |
| Healthcare | Professional, Line Art | Blues, greens, teals | Clean sans |
| Finance | Corporate, Emblem | Navy, gold | Serif or clean sans |
| Food | Vintage Badge, Mascot | Warm reds, oranges | Friendly, script |
| Fashion | Wordmark, Luxury | Black, gold, white | Elegant serif |

## Workflow

1. Generate design brief → `scripts/logo/search.py --design-brief`
2. Generate logo variations → `scripts/logo/generate.py --brand --style --industry`
3. Ask user about HTML preview → `AskUserQuestion` tool
4. If yes, build the HTML gallery from `references/intelligence.md` and `references/polish.md`

## Detailed References

- `references/logo-style-guide.md` - Detailed style descriptions
- `references/logo-color-psychology.md` - Color meanings and combinations
- `references/logo-prompt-engineering.md` - AI generation prompts

## Setup

```bash
export GEMINI_API_KEY="your-key"
pip install google-genai
```
