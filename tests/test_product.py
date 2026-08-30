from pathlib import Path
import re
import unittest

from sentinel.product import PRODUCT


class ProductMetadataTests(unittest.TestCase):
    def test_public_identity_and_release_contract_are_centralized(self) -> None:
        self.assertEqual("Window Sentinel", PRODUCT.display_name)
        self.assertEqual("benthompsondev", PRODUCT.github_owner)
        self.assertEqual("codex-window-sentinel", PRODUCT.github_repo)
        self.assertEqual("WindowSentinel-Setup.exe", PRODUCT.installer_filename)
        self.assertEqual("windowsentinel.ico", PRODUCT.icon_filename)
        self.assertEqual(
            "WindowSentinel-Setup.exe.sha256", PRODUCT.checksum_filename
        )
        self.assertEqual("WindowSentinelStatus.exe", PRODUCT.claude_status_helper_name)
        self.assertEqual(
            "https://github.com/benthompsondev/codex-window-sentinel/releases/latest",
            PRODUCT.releases_url,
        )

    def test_python_and_package_versions_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(PRODUCT.version, match.group(1))

    def test_windows_package_reads_product_metadata_and_emits_checksum(self) -> None:
        root = Path(__file__).resolve().parents[1]
        installer = (root / "packaging" / "WindowSentinel.iss").read_text(
            encoding="utf-8"
        )
        build = (root / "scripts" / "build-windows.ps1").read_text(
            encoding="utf-8"
        )
        pyinstaller = (root / "packaging" / "WindowSentinel.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn("#ifndef AppVersion", installer)
        self.assertIn("/DAppVersion=", build)
        self.assertIn("checksum_filename", build)
        self.assertIn("claude_status_helper_name", build)
        self.assertIn("WindowSentinelStatus.exe", installer)
        self.assertIn("--unregister", installer)
        self.assertIn("Get-FileHash", build)
        self.assertIn("PRODUCT.icon_filename", pyinstaller)
        self.assertIn("PRODUCT.version_resource_filename", pyinstaller)
        self.assertIn("render_version_info.py", build)


if __name__ == "__main__":
    unittest.main()
