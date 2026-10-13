"""Repairs the stack gates make to a project rather than report.

The plumbing gates fail in one of two ways. Some describe a service that is not
answering, and no edit here changes that - the fix is a setting on the service.
The rest describe a file the project is missing or carrying wrongly: a formatter
with no configuration, a workflow that drifted from the standard, a version that
never moved to CalVer, an editor directory nobody ignored. Those have one correct
form, held in this repository already, so the tool writes it.

Every fixer re-derives the fault from the same inputs its checker read, and
returns the paths it changed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
STANDARD_CI = SKILL / "references/ci.yml"

PRETTIER = {
    "semi": False,
    "singleQuote": True,
    "printWidth": 120,
    "tabWidth": 2,
    "arrowParens": "always",
}

ESLINT_FLAT = """import js from '@eslint/js'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'build/**', 'node_modules/**'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx,mjs}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'always'],
      'prefer-const': 'error',
      'no-var': 'error',
    },
  },
]
"""

EDITOR_IGNORES = (".idea/", ".vscode/", "*.iml", ".fleet/")


def _write_package(root, package):
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n")


def fix_prettier_config(gate, project, run, ctx):
    """A formatter with no configuration settles nothing, so it gets the house one."""
    root = project["root"]
    target = root / ".prettierrc"
    existing = [p for p in root.iterdir()
                if p.is_file() and re.match(r"^\.prettierrc|^prettier\.config\.", p.name)]
    if existing and all(p.read_text().strip() in ("", "{}", "[]") for p in existing):
        target = existing[0]
    elif existing:
        return []
    target.write_text(json.dumps(PRETTIER, indent=2) + "\n")
    ignore = root / ".prettierignore"
    if not ignore.is_file():
        ignore.write_text("dist\nbuild\nnode_modules\npackage-lock.json\n")
    return [target.name]


def fix_eslint_config(gate, project, run, ctx):
    root = project["root"]
    if any((root / n).is_file() for n in ("eslint.config.js", "eslint.config.mjs",
                                          ".eslintrc", ".eslintrc.json", ".eslintrc.cjs")):
        return []
    (root / "eslint.config.js").write_text(ESLINT_FLAT)
    return ["eslint.config.js"]


def fix_scripts(gate, project, run, ctx):
    """lint, format and format:check, so CI has something to call."""
    root, package = project["root"], project["package"]
    scripts = package.setdefault("scripts", {})
    wanted = {"lint": "eslint .", "format": "prettier --write .",
              "format:check": "prettier --check ."}
    added = [k for k, v in wanted.items() if k not in scripts]
    if not added:
        return []
    for k in added:
        scripts[k] = wanted[k]
    _write_package(root, package)
    return ["package.json: " + ", ".join(added)]


def fix_ci_workflow(gate, project, run, ctx):
    """The standard workflow, whose job is named check because protection names it."""
    if not STANDARD_CI.is_file():
        return []
    root = project["root"]
    target = root / ".github/workflows/ci.yml"
    if target.is_file() and target.read_text() == STANDARD_CI.read_text():
        return []
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(STANDARD_CI, target)
    return [".github/workflows/ci.yml"]


def fix_editor_ignored(gate, project, run, ctx):
    root = project["root"]
    path = root / ".gitignore"
    text = path.read_text() if path.is_file() else ""
    missing = [p for p in EDITOR_IGNORES if p.strip("/*") not in text]
    if not missing:
        return []
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(missing) + "\n")
    return [".gitignore: " + ", ".join(missing)]


def fix_calver(gate, project, run, ctx):
    """bump-version.py knows how this project moves its version; it is the one caller."""
    tool = Path.home() / ".claude/tools/bump-version.py"
    if not tool.is_file():
        return []
    before = str(project["package"].get("version") or "")
    done = subprocess.run(["python3", str(tool)], cwd=str(project["root"]),
                          capture_output=True, text=True)
    if done.returncode != 0:
        return []
    after = json.loads((project["root"] / "package.json").read_text()).get("version", "")
    return ["package.json: %s -> %s" % (before, after)] if after != before else []
