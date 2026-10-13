---
name: ckm:brand
description: Brand voice, visual identity, messaging frameworks, asset management, brand consistency. Activate for branded content, tone of voice, marketing assets, brand compliance, style guides.
argument-hint: "[update|review|create] [args]"
metadata:
  author: claudekit
  version: "1.0.0"
---

# Brand

Brand identity, voice, messaging, asset management, and consistency frameworks.

## When to Use

- Brand voice definition and content tone guidance
- Visual identity standards and style guide development
- Messaging framework creation
- Brand consistency review and audit
- Asset organization, naming, and approval
- Color palette management and typography specs

## Quick Start

**Inject brand context into prompts:**
```bash
node scripts/brand/inject-brand-context.cjs
node scripts/brand/inject-brand-context.cjs --json
```

**Validate an asset:**
```bash
node scripts/brand/validate-asset.cjs <asset-path>
```

**Extract/compare colors:**
```bash
node scripts/brand/extract-colors.cjs --palette
node scripts/brand/extract-colors.cjs <image-path>
```

## Brand Sync Workflow

```bash
# 1. Edit docs/brand-guidelines.md (or use /brand update)
# 2. Sync to design tokens
node scripts/brand/sync-brand-to-tokens.cjs
# 3. Verify
node scripts/brand/inject-brand-context.cjs --json | head -20
```

**Files synced:**
- `docs/brand-guidelines.md` → Source of truth
- `assets/design-tokens.json` → Token definitions
- `assets/design-tokens.css` → CSS variables

## Subcommands

| Subcommand | Description | Reference |
|------------|-------------|-----------|
| `update` | Update brand identity and sync to all design systems | `references/brand/update.md` |

## References

| Topic | File |
|-------|------|
| Voice Framework | `references/brand/voice-framework.md` |
| Visual Identity | `references/brand/visual-identity.md` |
| Messaging | `references/brand/messaging-framework.md` |
| Consistency | `references/brand/consistency-checklist.md` |
| Guidelines Template | `references/brand/brand-guideline-template.md` |
| Asset Organization | `references/brand/asset-organization.md` |
| Color Management | `references/brand/color-palette-management.md` |
| Typography | `references/brand/typography-specifications.md` |
| Logo Usage | `references/brand/logo-usage-rules.md` |
| Approval Checklist | `references/brand/approval-checklist.md` |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/brand/inject-brand-context.cjs` | Extract brand context for prompt injection |
| `scripts/brand/sync-brand-to-tokens.cjs` | Sync brand-guidelines.md → design-tokens.json/css |
| `scripts/brand/validate-asset.cjs` | Validate asset naming, size, format |
| `scripts/brand/extract-colors.cjs` | Extract and compare colors against palette |

## Templates

| Template | Purpose |
|----------|---------|
| `templates/brand/brand-guidelines-starter.md` | Complete starter template for new brands |

## Routing

1. Parse subcommand from `$ARGUMENTS` (first word)
2. Load corresponding `references/brand/{subcommand}.md`
3. Execute with remaining arguments
