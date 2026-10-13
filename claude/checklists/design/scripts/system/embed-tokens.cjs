#!/usr/bin/env node
/**
 * Prints the project's design tokens as one inline :root block.
 *
 * A slide deck or an infographic is a single HTML file that has to render from
 * anywhere, so it cannot link a stylesheet that only exists in the repo. This
 * is how the tokens travel inside it.
 *
 *   node embed-tokens.cjs
 *   node embed-tokens.cjs --minimal   # only the tokens a standalone page uses
 *   node embed-tokens.cjs --style     # wrapped in <style> tags
 */

const fs = require('fs');
const path = require('path');

// The token file is the marker: a standalone page is generated from wherever
// it happens to sit, which is not necessarily inside the project.
function findProjectRoot(startDir) {
  let dir = startDir;
  while (dir !== '/') {
    if (fs.existsSync(path.join(dir, 'assets', 'design-tokens.css'))) {
      return dir;
    }
    dir = path.dirname(dir);
  }
  return null;
}

const projectRoot = findProjectRoot(process.cwd());
if (!projectRoot) {
  console.error('Error: Could not find assets/design-tokens.css');
  process.exit(1);
}

const tokensPath = path.join(projectRoot, 'assets', 'design-tokens.css');

// Prefixes rather than names, so a whole scale is kept or dropped together.
// A standalone page uses spacing, type, and the semantic colours; the rest of
// the system is component tokens nothing on the page references.
const MINIMAL_TOKENS = [
  '--primitive-spacing-',
  '--primitive-fontSize-',
  '--primitive-fontWeight-',
  '--primitive-lineHeight-',
  '--primitive-radius-',
  '--primitive-shadow-glow-',
  '--primitive-gradient-',
  '--primitive-duration-',
  '--color-primary',
  '--color-secondary',
  '--color-accent',
  '--color-background',
  '--color-surface',
  '--color-foreground',
  '--color-border',
  '--typography-font-',
  '--card-',
];

function extractTokens(css, minimal = false) {
  // Every :root block, not the first: a token file declares its themes in
  // separate blocks and the later ones would otherwise be dropped.
  const rootMatch = css.match(/:root\s*\{([^}]+)\}/g);
  if (!rootMatch) return '';

  let allVars = [];
  for (const block of rootMatch) {
    const vars = block.match(/--[\w-]+:\s*[^;]+;/g) || [];
    allVars = allVars.concat(vars);
  }

  if (minimal) {
    allVars = allVars.filter(v =>
      MINIMAL_TOKENS.some(token => v.includes(token))
    );
  }

  // A token redeclared by a later theme block appears twice; the last write
  // wins in CSS anyway, so only the duplicate line is dropped.
  allVars = [...new Set(allVars)];

  return `:root {\n  ${allVars.join('\n  ')}\n}`;
}

const args = process.argv.slice(2);
const minimal = args.includes('--minimal');
const wrapStyle = args.includes('--style');

try {
  const css = fs.readFileSync(tokensPath, 'utf-8');
  let output = extractTokens(css, minimal);

  if (wrapStyle) {
    output = `<style>\n/* Design Tokens (embedded for standalone HTML) */\n${output}\n</style>`;
  } else {
    output = `/* Design Tokens (embedded for standalone HTML) */\n${output}`;
  }

  console.log(output);
} catch (err) {
  console.error(`Error reading tokens: ${err.message}`);
  process.exit(1);
}
