from pathlib import Path
import re
import unittest

from sentinel.product import PRODUCT


class ProductMetadataTests(unittest.TestCase):
    def test_public_identity_is_centralized(self) -> None:
        self.assertEqual("UsageLoop", PRODUCT.display_name)
        self.assertEqual("Keep your Codex reset clock running.", PRODUCT.tagline)
        self.assertEqual("UsageLoop.exe", PRODUCT.executable_name)
        self.assertEqual("usageloop.ico", PRODUCT.icon_filename)
        self.assertEqual("UsageLoop", PRODUCT.dist_folder_name)

    def test_release_lookup_keeps_the_published_repository_slug(self) -> None:
        """Renaming the repository would break every installed copy's updater."""
        self.assertEqual("benthompsondev", PRODUCT.github_owner)
        self.assertEqual("codex-window-sentinel", PRODUCT.github_repo)
        self.assertEqual(
            "https://api.github.com/repos/benthompsondev/codex-window-sentinel/releases/latest",
            PRODUCT.release_api_url,
        )
        self.assertEqual(
            "https://github.com/benthompsondev/codex-window-sentinel/releases/latest",
            PRODUCT.releases_url,
        )

    def test_release_assets_are_named_from_the_product(self) -> None:
        self.assertEqual("UsageLoop-Setup.exe", PRODUCT.installer_filename)
        self.assertEqual(
            f"{PRODUCT.installer_filename}.sha256", PRODUCT.checksum_filename
        )

    def test_legacy_state_folder_is_recorded_for_migration(self) -> None:
        self.assertEqual("UsageLoop", PRODUCT.app_data_folder)
        self.assertEqual("CodexWindowSentinel", PRODUCT.legacy_app_data_folder)
        self.assertNotEqual(PRODUCT.app_data_folder, PRODUCT.legacy_app_data_folder)

    def test_python_and_package_versions_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(PRODUCT.version, match.group(1))

    def test_windows_package_reads_product_metadata_and_emits_checksum(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(encoding="utf-8")
        build = (root / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")
        pyinstaller = (root / "packaging" / "UsageLoop.spec").read_text(encoding="utf-8")

        self.assertIn("#ifndef AppVersion", installer)
        self.assertIn("/DAppVersion=", build)
        self.assertIn("checksum_filename", build)
        self.assertNotIn("claude_status", build.lower())
        self.assertNotIn("--unregister", installer)
        self.assertIn("[InstallDelete]", installer)
        self.assertIn("Get-FileHash", build)
        self.assertIn("PRODUCT.icon_filename", pyinstaller)
        self.assertIn("PRODUCT.version_resource_filename", pyinstaller)
        self.assertIn("render_version_info.py", build)

    def test_packaging_paths_are_not_hardcoded_to_the_old_name(self) -> None:
        """The rebrand must not leave a stale path that silently breaks a build."""
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "packaging/UsageLoop.iss",
            "packaging/UsageLoop.spec",
            "scripts/build-windows.ps1",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertNotIn("WindowSentinel", text, relative)
            self.assertNotIn("windowsentinel", text, relative)

    def test_local_setup_and_verification_use_the_current_desktop_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in ("scripts/setup.ps1", "scripts/verify.ps1"):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("usageloop.exe", text.lower(), relative)
            self.assertNotIn("window-sentinel.exe", text.lower(), relative)

    def test_installer_placeholders_are_driven_by_the_build_script(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(encoding="utf-8")
        build = (root / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")
        for define in ("AppIconFile", "DistFolder"):
            self.assertIn(f"#ifndef {define}", installer)
            self.assertIn(f"/D{define}=", build)




class PackagingMetadataTests(unittest.TestCase):
    """A property is invisible to asdict, which once broke the installer."""

    def test_derived_names_are_exported_for_the_build_script(self) -> None:
        data = PRODUCT.packaging_metadata()
        self.assertEqual(PRODUCT.dist_folder_name, data["dist_folder_name"])
        self.assertEqual(PRODUCT.executable_name, data["executable_name"])
        self.assertNotIn("claude_status_helper_name", data)

    def test_every_name_the_build_script_reads_is_exported(self) -> None:
        root = Path(__file__).resolve().parents[1]
        build = (root / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")
        exported = PRODUCT.packaging_metadata()
        referenced = set(re.findall(r"\$product\.(\w+)", build))
        self.assertTrue(referenced, "the build script should read product metadata")
        missing = referenced.difference(exported)
        self.assertEqual(set(), missing, f"build script reads unexported keys: {missing}")

    def test_exported_values_are_all_strings(self) -> None:
        for key, value in PRODUCT.packaging_metadata().items():
            with self.subTest(key=key):
                self.assertIsInstance(value, str)
                self.assertNotEqual("", value)


if __name__ == "__main__":
    unittest.main()
