#!/usr/bin/env node
/**
 * Finds colours and sizes written as literals where a design token exists.
 *
 * A literal is not wrong on sight - it is wrong because it will not move when
 * the theme does. So this reports rather than rewrites: the replacement token
 * is a judgement about what the value means, and only the author of the line
 * knows whether a 24px gap is spacing or a fixed icon size.
 *
 *   node validate-tokens.cjs --dir src/
 */

const fs = require('fs');
const path = require('path');

function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    dir: null,
    fix: false,
    ignore: ['node_modules', '.git', 'dist', 'build', '.next']
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--dir' || args[i] === '-d') {
      options.dir = args[++i];
    } else if (args[i] === '--fix') {
      options.fix = true;
    } else if (args[i] === '--ignore' || args[i] === '-i') {
      options.ignore.push(args[++i]);
    } else if (args[i] === '--help' || args[i] === '-h') {
      console.log(`
Usage: node validate-tokens.cjs [options]

Options:
  -d, --dir <path>      Directory to scan (required)
  --fix                 Show suggested fixes (no auto-fix)
  -i, --ignore <dir>    Additional directories to ignore
  -h, --help            Show this help

Checks for:
  - Hardcoded hex colors (#RGB, #RRGGBB)
  - Hardcoded pixel values (except 0, 1px)
  - Hardcoded rem values in CSS
      `);
      process.exit(0);
    }
  }

  return options;
}

/**
 * What a literal looks like, per kind, with the token family that replaces it.
 *
 * Each is deliberately conservative. Pixel values under two digits are left
 * alone: a 1px rule or a 4px nudge is usually a real constant rather than a
 * missed spacing step. Rem values inside a token definition are excluded, or
 * the token file would report every token it declares.
 */
const patterns = {
  hexColor: {
    regex: /#([0-9A-Fa-f]{3}){1,2}\b/g,
    message: 'Hardcoded hex color',
    suggestion: 'Use var(--color-*) token'
  },
  rgbColor: {
    regex: /rgb\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)/gi,
    message: 'Hardcoded RGB color',
    suggestion: 'Use var(--color-*) token'
  },
  pixelValue: {
    regex: /:\s*(\d{2,})px/g, // 2+ digit px values
    message: 'Hardcoded pixel value',
    suggestion: 'Use var(--space-*) or var(--radius-*) token'
  },
  remValue: {
    regex: /:\s*\d+\.?\d*rem(?![^{]*\$value)/g, // rem not in token definition
    message: 'Hardcoded rem value',
    suggestion: 'Use var(--space-*) or var(--font-size-*) token'
  }
};

const extensions = ['.css', '.scss', '.tsx', '.jsx', '.ts', '.js', '.vue', '.svelte'];

/**
 * Files whose literals are the point: the token declarations themselves, the
 * Tailwind config that maps them, and minified output nobody edits.
 */
const skipPatterns = [
  /\.min\.(css|js)$/,
  /tailwind\.config/,
  /globals\.css/,
  /tokens\.(css|json)/
];

function getFiles(dir, ignore, files = []) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      if (!ignore.includes(entry.name)) {
        getFiles(fullPath, ignore, files);
      }
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name);
      if (extensions.includes(ext)) {
        files.push(fullPath);
      }
    }
  }

  return files;
}

function shouldSkip(filePath) {
  return skipPatterns.some(pattern => pattern.test(filePath));
}

function scanFile(filePath) {
  const content = fs.readFileSync(filePath, 'utf-8');
  const lines = content.split('\n');
  const violations = [];

  lines.forEach((line, index) => {
    // A line-leading comment marker only. A trailing comment still carries the
    // code before it, which is where a literal would be.
    if (line.trim().startsWith('//') || line.trim().startsWith('/*')) {
      return;
    }

    // A line already reaching for a token is doing the right thing; the literal
    // beside it is almost always the fallback inside var().
    if (line.includes('var(--')) {
      return;
    }

    for (const [name, pattern] of Object.entries(patterns)) {
      const matches = line.match(pattern.regex);
      if (matches) {
        matches.forEach(match => {
          // Pure black and white are as often a deliberate absolute - a shadow,
          // an overlay, a print rule - as a missed token.
          if (name === 'hexColor' && ['#000', '#fff', '#FFF', '#000000', '#FFFFFF'].includes(match.toUpperCase())) {
            return;
          }

          violations.push({
            file: filePath,
            line: index + 1,
            column: line.indexOf(match) + 1,
            value: match,
            type: name,
            message: pattern.message,
            suggestion: pattern.suggestion,
            context: line.trim().substring(0, 80)
          });
        });
      }
    }
  });

  return violations;
}

function formatReport(violations) {
  if (violations.length === 0) {
    return 'No token violations found';
  }

  let report = `Found ${violations.length} potential token violations:\n\n`;

  const byFile = {};
  violations.forEach(v => {
    if (!byFile[v.file]) byFile[v.file] = [];
    byFile[v.file].push(v);
  });

  for (const [file, fileViolations] of Object.entries(byFile)) {
    report += `${file}\n`;
    fileViolations.forEach(v => {
      report += `   Line ${v.line}: ${v.message}\n`;
      report += `   Found: ${v.value}\n`;
      report += `   Suggestion: ${v.suggestion}\n`;
      report += `   Context: ${v.context}\n\n`;
    });
  }

  const byType = {};
  violations.forEach(v => {
    byType[v.type] = (byType[v.type] || 0) + 1;
  });

  report += `\nSummary:\n`;
  for (const [type, count] of Object.entries(byType)) {
    report += `   ${patterns[type].message}: ${count}\n`;
  }

  return report;
}

function main() {
  const options = parseArgs();

  if (!options.dir) {
    console.error('Error: --dir is required');
    process.exit(1);
  }

  const dirPath = path.resolve(process.cwd(), options.dir);

  if (!fs.existsSync(dirPath)) {
    console.error(`Error: Directory not found: ${dirPath}`);
    process.exit(1);
  }

  console.log(`Scanning ${dirPath} for token violations...\n`);

  const files = getFiles(dirPath, options.ignore);
  const allViolations = [];

  for (const file of files) {
    if (shouldSkip(file)) continue;

    const violations = scanFile(file);
    allViolations.push(...violations);
  }

  console.log(formatReport(allViolations));

  if (allViolations.length > 0) {
    process.exit(1);
  }
}

main();
