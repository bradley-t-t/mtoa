#!/usr/bin/env python3
"""Adds shadcn/ui components to a project.

The work is done by `npx shadcn@latest`, not here: a component is source that
the CLI resolves against the project's own components.json, aliases, and
Tailwind config, so copying files would produce imports that do not resolve.
What this adds around it is refusing to overwrite silently, and answering
without side effects under --dry-run.

Every method returns (ok, message) rather than raising, because the caller is
usually a CLI printing whichever it got.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class ShadcnInstaller:
    """One project's component directory, and the CLI calls that add to it."""

    def __init__(self, project_root: Optional[Path] = None, dry_run: bool = False):
        """`project_root` defaults to the working directory. Under `dry_run`
        nothing is run and every call reports the command it would have run."""
        self.project_root = project_root or Path.cwd()
        self.dry_run = dry_run
        self.components_json = self.project_root / "components.json"

    def check_shadcn_config(self) -> bool:
        """Whether the project has a components.json, which is what the CLI
        reads its aliases and Tailwind paths out of."""
        return self.components_json.exists()

    def get_installed_components(self) -> List[str]:
        """The components already in the project, by file name under the ui
        directory that components.json points at.

        An unreadable or malformed config yields nothing rather than raising:
        the answer is only used to decide whether to warn about an overwrite.
        """
        if not self.check_shadcn_config():
            return []

        try:
            with open(self.components_json) as f:
                config = json.load(f)

            components_dir = self.project_root / config.get("aliases", {}).get(
                "components", "components"
            ).replace("@/", "")
            ui_dir = components_dir / "ui"

            if not ui_dir.exists():
                return []

            return [f.stem for f in ui_dir.glob("*.tsx") if f.is_file()]
        except (json.JSONDecodeError, KeyError, OSError):
            return []

    def add_components(
        self, components: List[str], overwrite: bool = False
    ) -> tuple[bool, str]:
        """Add the named components. Refuses when any of them is already there
        and `overwrite` is not set, because the CLI would replace a customised
        component without saying so."""
        if not components:
            return False, "No components specified"

        if not self.check_shadcn_config():
            return (
                False,
                "shadcn not initialized. Run 'npx shadcn@latest init' first",
            )

        installed = self.get_installed_components()
        already_installed = [c for c in components if c in installed]

        if already_installed and not overwrite:
            return (
                False,
                f"Components already installed: {', '.join(already_installed)}. "
                "Use --overwrite to reinstall",
            )

        cmd = ["npx", "shadcn@latest", "add"] + components

        if overwrite:
            cmd.append("--overwrite")

        if self.dry_run:
            return True, f"Would run: {' '.join(cmd)}"

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True,
            )

            success_msg = f"Successfully added components: {', '.join(components)}"
            if result.stdout:
                success_msg += f"\n\nOutput:\n{result.stdout}"

            return True, success_msg

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to add components: {e.stderr or e.stdout or str(e)}"
            return False, error_msg
        except FileNotFoundError:
            return False, "npx not found. Ensure Node.js is installed"

    def add_all_components(self, overwrite: bool = False) -> tuple[bool, str]:
        """Add every component the registry offers. There is no already-installed
        check here: --all is a deliberate request for the whole set."""
        if not self.check_shadcn_config():
            return (
                False,
                "shadcn not initialized. Run 'npx shadcn@latest init' first",
            )

        cmd = ["npx", "shadcn@latest", "add", "--all"]

        if overwrite:
            cmd.append("--overwrite")

        if self.dry_run:
            return True, f"Would run: {' '.join(cmd)}"

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=True,
            )

            success_msg = "Successfully added all components"
            if result.stdout:
                success_msg += f"\n\nOutput:\n{result.stdout}"

            return True, success_msg

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to add all components: {e.stderr or e.stdout or str(e)}"
            return False, error_msg
        except FileNotFoundError:
            return False, "npx not found. Ensure Node.js is installed"

    def list_installed(self) -> tuple[bool, str]:
        """The installed components, sorted, as a printable list."""
        if not self.check_shadcn_config():
            return False, "shadcn not initialized"

        installed = self.get_installed_components()

        if not installed:
            return True, "No components installed"

        return True, f"Installed components:\n" + "\n".join(f"  - {c}" for c in sorted(installed))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Add shadcn/ui components to your project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add single component
  python shadcn_add.py button

  # Add multiple components
  python shadcn_add.py button card dialog

  # Add all components
  python shadcn_add.py --all

  # Overwrite existing components
  python shadcn_add.py button --overwrite

  # Print the command instead of running it
  python shadcn_add.py button card --dry-run

  # List installed components
  python shadcn_add.py --list
        """,
    )

    parser.add_argument(
        "components",
        nargs="*",
        help="Component names to add (e.g., button, card, dialog)",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Add all available components",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing components",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List installed components",
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        help="Project root directory (default: current directory)",
    )

    args = parser.parse_args()

    installer = ShadcnInstaller(
        project_root=args.project_root,
        dry_run=args.dry_run,
    )

    if args.list:
        success, message = installer.list_installed()
        print(message)
        sys.exit(0 if success else 1)

    if args.all:
        success, message = installer.add_all_components(overwrite=args.overwrite)
        print(message)
        sys.exit(0 if success else 1)

    if not args.components:
        parser.print_help()
        sys.exit(1)

    success, message = installer.add_components(
        args.components,
        overwrite=args.overwrite,
    )

    print(message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
