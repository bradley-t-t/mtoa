"""Cases for shadcn_add.py.

Every case that would otherwise reach the network stubs subprocess.run: the
CLI resolves components against a registry, so a real call is slow, online, and
writes into the fixture project.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# The scripts directory is not a package, so the module under test is reached
# by path rather than by import.
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from shadcn_add import ShadcnInstaller


class TestShadcnInstaller:
    @pytest.fixture
    def temp_project(self, tmp_path):
        """A project the installer recognises: a components.json declaring the
        aliases it reads, and the empty ui directory those aliases point at."""
        project_root = tmp_path / "test-project"
        project_root.mkdir()

        components_json = project_root / "components.json"
        components_json.write_text(
            json.dumps({
                "style": "new-york",
                "aliases": {
                    "components": "@/components",
                    "utils": "@/lib/utils"
                }
            })
        )

        ui_dir = project_root / "components" / "ui"
        ui_dir.mkdir(parents=True)

        return project_root

    def test_init_default_project_root(self):
        installer = ShadcnInstaller()
        assert installer.project_root == Path.cwd()
        assert installer.dry_run is False

    def test_init_custom_project_root(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        assert installer.project_root == tmp_path

    def test_init_dry_run(self):
        installer = ShadcnInstaller(dry_run=True)
        assert installer.dry_run is True

    def test_check_shadcn_config_exists(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project)
        assert installer.check_shadcn_config() is True

    def test_check_shadcn_config_not_exists(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        assert installer.check_shadcn_config() is False

    def test_get_installed_components_empty(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project)
        installed = installer.get_installed_components()
        assert installed == []

    def test_get_installed_components_with_files(self, temp_project):
        ui_dir = temp_project / "components" / "ui"

        (ui_dir / "button.tsx").write_text("export const Button = () => {}")
        (ui_dir / "card.tsx").write_text("export const Card = () => {}")

        installer = ShadcnInstaller(project_root=temp_project)
        installed = installer.get_installed_components()

        assert sorted(installed) == ["button", "card"]

    def test_get_installed_components_no_config(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        installed = installer.get_installed_components()
        assert installed == []

    def test_add_components_no_components(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_components([])

        assert success is False
        assert "No components specified" in message

    def test_add_components_no_config(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        success, message = installer.add_components(["button"])

        assert success is False
        assert "not initialized" in message

    def test_add_components_already_installed(self, temp_project):
        ui_dir = temp_project / "components" / "ui"
        (ui_dir / "button.tsx").write_text("export const Button = () => {}")

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_components(["button"])

        assert success is False
        assert "already installed" in message
        assert "button" in message

    def test_add_components_with_overwrite(self, temp_project):
        ui_dir = temp_project / "components" / "ui"
        (ui_dir / "button.tsx").write_text("export const Button = () => {}")

        installer = ShadcnInstaller(project_root=temp_project)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Component added successfully",
                returncode=0
            )

            success, message = installer.add_components(["button"], overwrite=True)

            assert success is True
            assert "Successfully added" in message
            mock_run.assert_called_once()

            call_args = mock_run.call_args[0][0]
            assert "--overwrite" in call_args

    def test_add_components_dry_run(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project, dry_run=True)
        success, message = installer.add_components(["button", "card"])

        assert success is True
        assert "Would run:" in message
        assert "button" in message
        assert "card" in message

    @patch("subprocess.run")
    def test_add_components_success(self, mock_run, temp_project):
        mock_run.return_value = MagicMock(
            stdout="Components added successfully",
            stderr="",
            returncode=0
        )

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_components(["button", "card"])

        assert success is True
        assert "Successfully added" in message
        assert "button" in message
        assert "card" in message

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["npx", "shadcn@latest", "add"]
        assert "button" in call_args
        assert "card" in call_args

    @patch("subprocess.run")
    def test_add_components_subprocess_error(self, mock_run, temp_project):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "cmd", stderr="Error occurred"
        )

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_components(["button"])

        assert success is False
        assert "Failed to add" in message

    @patch("subprocess.run")
    def test_add_components_npx_not_found(self, mock_run, temp_project):
        mock_run.side_effect = FileNotFoundError()

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_components(["button"])

        assert success is False
        assert "npx not found" in message

    def test_add_all_components_no_config(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        success, message = installer.add_all_components()

        assert success is False
        assert "not initialized" in message

    def test_add_all_components_dry_run(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project, dry_run=True)
        success, message = installer.add_all_components()

        assert success is True
        assert "Would run:" in message
        assert "--all" in message

    @patch("subprocess.run")
    def test_add_all_components_success(self, mock_run, temp_project):
        mock_run.return_value = MagicMock(
            stdout="All components added",
            returncode=0
        )

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.add_all_components()

        assert success is True
        assert "Successfully added all" in message

        call_args = mock_run.call_args[0][0]
        assert "--all" in call_args

    def test_list_installed_no_config(self, tmp_path):
        installer = ShadcnInstaller(project_root=tmp_path)
        success, message = installer.list_installed()

        assert success is False
        assert "not initialized" in message

    def test_list_installed_empty(self, temp_project):
        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.list_installed()

        assert success is True
        assert "No components installed" in message

    def test_list_installed_with_components(self, temp_project):
        ui_dir = temp_project / "components" / "ui"
        (ui_dir / "button.tsx").write_text("export const Button = () => {}")
        (ui_dir / "card.tsx").write_text("export const Card = () => {}")

        installer = ShadcnInstaller(project_root=temp_project)
        success, message = installer.list_installed()

        assert success is True
        assert "button" in message
        assert "card" in message
