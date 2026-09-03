from pathlib import Path
import re
import unittest

from sentinel.product import PRODUCT


class ProductMetadataTests(unittest.TestCase):
    def test_public_identity_is_centralized(self) -> None:
        self.assertEqual("UsageLoop", PRODUCT.display_name)
        self.assertEqual("UsageLoop", PRODUCT.publisher)
        self.assertEqual("Keep your Codex reset clock running.", PRODUCT.tagline)
        self.assertEqual("UsageLoop.exe", PRODUCT.executable_name)
        self.assertEqual("usageloop.ico", PRODUCT.icon_filename)
        self.assertEqual("UsageLoop", PRODUCT.dist_folder_name)

    def test_release_lookup_keeps_the_published_repository_slug(self) -> None:
        """Renaming the repository would break every installed copy's updater."""
        self.assertEqual("benthompsondev", PRODUCT.github_owner)
        self.assertEqual("usageloop", PRODUCT.github_repo)
        self.assertEqual(
            "https://api.github.com/repos/benthompsondev/usageloop/releases/latest",
            PRODUCT.release_api_url,
        )
        self.assertEqual(
            "https://github.com/benthompsondev/usageloop/releases/latest",
            PRODUCT.releases_url,
        )
        self.assertEqual(
            "https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml",
            PRODUCT.bug_report_url,
        )
        self.assertEqual(
            "https://github.com/benthompsondev/usageloop/issues/new?template=feature_request.yml",
            PRODUCT.feature_request_url,
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
        self.assertIn('name = "usageloop"', pyproject)

    def test_windows_package_reads_product_metadata_and_emits_checksum(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(encoding="utf-8")
        build = (root / "scripts" / "build-windows.ps1").read_text(encoding="utf-8")
        pyinstaller = (root / "packaging" / "UsageLoop.spec").read_text(encoding="utf-8")

        self.assertIn("#ifndef AppVersion", installer)
        self.assertIn(f'#define AppVersion "{PRODUCT.version}"', installer)
        self.assertIn("/DAppVersion=", build)
        self.assertIn("checksum_filename", build)
        self.assertNotIn("claude_status", build.lower())
        self.assertNotIn("--unregister", installer)
        self.assertIn("[InstallDelete]", installer)
        self.assertIn("Get-FileHash", build)
        self.assertIn("PRODUCT.icon_filename", pyinstaller)
        self.assertIn("PRODUCT.version_resource_filename", pyinstaller)
        self.assertIn("render_version_info.py", build)
        self.assertIn("Programs\\Inno Setup 6\\ISCC.exe", build)

    def test_in_place_update_keeps_the_same_app_identity_and_local_state(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(
            encoding="utf-8"
        )

        self.assertEqual("{{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}", PRODUCT.app_id)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\{#AppName}", installer)
        self.assertIn("UsePreviousAppDir=no", installer)
        self.assertIn("UsePreviousGroup=no", installer)
        self.assertIn("{#LegacyInstallFolder}", installer)
        self.assertIn("CurStepChanged", installer)
        self.assertIn("RegWriteStringValue", installer)
        self.assertIn("{userprograms}\\{#LegacyInstallFolder}", installer)
        self.assertNotIn(
            'Type: filesandordirs; Name: "{localappdata}\\Programs\\{#LegacyInstallFolder}"',
            installer,
        )
        self.assertNotIn("app-state.json", installer)
        self.assertNotIn("[UninstallDelete]", installer)
        self.assertNotIn("uninsdeletekey", installer.lower())

    def test_installer_uses_stable_windows_shell_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(
            encoding="utf-8"
        )

        self.assertIn("UninstallDisplayName={#AppName}", installer)
        self.assertIn(
            "UninstallDisplayIcon={app}\\{#AppExeName},0",
            installer,
        )
        self.assertIn(
            'Name: "{userprograms}\\{#AppName}\\{#AppName}"',
            installer,
        )
        self.assertIn('IconFilename: "{app}\\{#AppExeName}"', installer)
        self.assertNotIn('Name: "{group}\\{#AppName}"', installer)

    def test_installer_repairs_only_the_known_usage_loop_registration(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "UsageLoop.iss").read_text(
            encoding="utf-8"
        )

        self.assertIn("RepairUsageLoopRegistration", installer)
        self.assertIn("{#AppId}", installer)
        self.assertIn("{#LegacyInstallFolder}", installer)
        self.assertIn("FileExists", installer)
        self.assertIn("DisplayVersion", installer)
        self.assertIn("UninstallString", installer)
        self.assertNotIn("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*", installer)

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

    def test_codex_only_runtime_contains_no_retired_claude_integration(self) -> None:
        root = Path(__file__).resolve().parents[1]
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root / "src" / "sentinel").glob("*.py")
        )
        self.assertNotIn(".claude", runtime_text.lower())
        self.assertNotIn("claude-status", runtime_text.lower())

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

    def test_ci_keeps_visible_window_proof_out_of_headless_install_jobs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        verifier = (root / "scripts" / "verify-clean-install.ps1").read_text(
            encoding="utf-8"
        )
        workflow = (root / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("[switch]$SkipDesktopActivation", verifier)
        self.assertIn("if (-not $SkipDesktopActivation)", verifier)
        self.assertIn("verify-packaged-activation.ps1", verifier)
        self.assertIn("-SkipDesktopActivation", workflow)

    def test_recent_predecessors_are_in_the_upgrade_acceptance_matrix(self) -> None:
        root = Path(__file__).resolve().parents[1]
        verifier = (root / "scripts" / "verify-clean-install.ps1").read_text(
            encoding="utf-8"
        )
        workflow = (root / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )

        for version in ("1.1.2", "1.1.3"):
            with self.subTest(version=version):
                self.assertIn(f"'{version}'", verifier)
                self.assertIn(f'"{version}"', workflow)
                self.assertGreaterEqual(
                    workflow.count(f"'{version}' {{ @('{version}') }}"), 2
                )
        self.assertIn("'recent-chain'", verifier)
        self.assertEqual(
            2, workflow.count("'recent-chain' { @('1.1.2', '1.1.3') }")
        )

    def test_readme_opens_with_the_payoff_and_one_dashboard_screenshot(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        example = (
            "Your current window ends at 1:00 AM. UsageLoop starts the next one at "
            "4:00 AM. When you sit down at 7:00 AM, that reset clock has already been "
            "running for three hours, so your next full reset arrives around 9:00 AM "
            "instead of noon."
        )

        self.assertIn("**Plan when your Codex day starts.**", readme)
        self.assertIn(example, readme.replace("\n", " "))
        self.assertIn("UsageLoop does not add quota or bypass limits", readme)
        self.assertEqual(1, readme.count("docs/screenshots/dashboard.png"))
        self.assertIn("Consider starring the repository", readme)

    def test_readme_documents_every_schedule_mode_the_app_offers(self) -> None:
        """The README described only two modes for three releases after Weekly shipped."""
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        flat = readme.replace("\n", " ")

        self.assertIn("three local schedule choices", flat)
        for mode in (
            "Continuous",
            "Once each day",
            "Weekly routine",
        ):
            self.assertIn(mode, flat)
        self.assertIn("## Set your weekly routine", readme)
        self.assertEqual(
            1, readme.count("docs/screenshots/settings-weekly-expanded.png")
        )
        for weekly_term in ("Apply Mon", "Customize individual days", "Next routine"):
            self.assertIn(weekly_term, flat)

    def test_pages_site_reflects_the_current_product(self) -> None:
        """The public site framed UsageLoop as a quota tracker and omitted Weekly."""
        root = Path(__file__).resolve().parents[1]
        index = (root / "docs" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("quota tracker", index)
        self.assertIn("Plan when your Codex day starts.", index)
        self.assertIn('id="weekly"', index)
        self.assertIn("screenshots/settings-weekly-expanded.png", index)
        self.assertIn("screenshots/dashboard-1280x720.png", index)
        for weekly_term in ("Apply Mon", "Customize individual days", "Next routine"):
            self.assertIn(weekly_term, index)
        self.assertIn("does not add quota or bypass limits", index)
        self.assertIn("The installer is currently unsigned.", index)

    def test_release_checklist_forbids_reusing_an_installed_candidate_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        releasing = (root / "docs" / "RELEASING.md").read_text(encoding="utf-8")

        self.assertIn(
            "Never publish different bits under a version that has already been installed",
            releasing,
        )
        self.assertIn("prerelease or development version", releasing)
        self.assertIn("next patch version", releasing)




class PackagingMetadataTests(unittest.TestCase):
    """A property is invisible to asdict, which once broke the installer."""

    def test_derived_names_are_exported_for_the_build_script(self) -> None:
        data = PRODUCT.packaging_metadata()
        self.assertEqual(PRODUCT.dist_folder_name, data["dist_folder_name"])
        self.assertEqual(PRODUCT.executable_name, data["executable_name"])
        self.assertEqual(
            "{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}", data["app_id_guid"]
        )
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
