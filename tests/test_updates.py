from io import BytesIO
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from sentinel.updates import (
    GitHubReleaseUpdater,
    UpdateCheckResult,
    UpdateError,
    VerifiedInstaller,
    is_newer_version,
    parse_release,
    summarize_release_notes,
)
from sentinel.product import PRODUCT


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def release_payload(*, version: str = "0.5.0", include_checksum: bool = True):
    assets = [
        {
            "name": PRODUCT.installer_filename,
            "browser_download_url": (
                "https://github.com/benthompsondev/usageloop/"
                f"releases/download/v0.5.0/{PRODUCT.installer_filename}"
            ),
        }
    ]
    if include_checksum:
        assets.append(
            {
                "name": PRODUCT.checksum_filename,
                "browser_download_url": (
                    "https://github.com/benthompsondev/usageloop/"
                    f"releases/download/v0.5.0/{PRODUCT.checksum_filename}"
                ),
            }
        )
    return {
        "tag_name": f"v{version}",
        "html_url": "https://github.com/benthompsondev/usageloop/releases/tag/v0.5.0",
        "body": "## Changes\n- Cleaner dashboard\n- Safer updater\n\nLonger detail.",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }


class UpdateParsingTests(unittest.TestCase):
    def test_version_comparison_never_treats_equal_or_older_as_update(self) -> None:
        self.assertTrue(is_newer_version("0.5.0", "0.4.0"))
        self.assertFalse(is_newer_version("0.4.0", "0.4.0"))
        self.assertFalse(is_newer_version("0.3.9", "0.4.0"))
        with self.assertRaises(UpdateError):
            is_newer_version("latest", "0.4.0")

    def test_installed_091_accepts_100_as_a_normal_update(self) -> None:
        self.assertTrue(is_newer_version("1.0.0", "0.9.1"))

    def test_release_requires_exact_installer_and_checksum_assets(self) -> None:
        release = parse_release(release_payload(), installed_version="0.4.0")
        self.assertIsNotNone(release)
        self.assertEqual("0.5.0", release.version)
        self.assertEqual(PRODUCT.installer_filename, release.installer.name)
        self.assertEqual(PRODUCT.checksum_filename, release.checksum.name)

        with self.assertRaises(UpdateError):
            parse_release(
                release_payload(include_checksum=False), installed_version="0.4.0"
            )

    def test_release_notes_are_plain_concise_lines(self) -> None:
        self.assertEqual(
            ("Cleaner dashboard", "Safer updater", "Longer detail."),
            summarize_release_notes(release_payload()["body"]),
        )


class GitHubReleaseUpdaterTests(unittest.TestCase):
    def test_check_uses_one_public_github_request_and_returns_update(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout, dict(request.header_items())))
            return FakeResponse(json.dumps(release_payload()).encode("utf-8"))

        updater = GitHubReleaseUpdater(product=replace(PRODUCT, version="0.4.0"), opener=opener)
        result = updater.check()

        self.assertEqual("update_available", result.status)
        self.assertEqual("0.5.0", result.release.version)
        self.assertEqual(1, len(calls))
        self.assertIn("api.github.com/repos/benthompsondev/usageloop", calls[0][0])

    def test_download_verifies_checksum_before_returning_installer(self) -> None:
        installer = b"safe installer bytes"
        checksum = (
            "15c7bd0073705a92a42091875c976f622ee8ac13602127b9bd72d2c9d74ff3ba"
            f"  {PRODUCT.installer_filename}\n"
        ).encode("ascii")
        responses = iter([checksum, installer])

        def opener(_request, timeout):
            return FakeResponse(next(responses))

        release = parse_release(release_payload(), installed_version="0.4.0")
        with tempfile.TemporaryDirectory() as directory:
            result = GitHubReleaseUpdater(opener=opener).download(
                release, destination_root=Path(directory)
            )
            self.assertIsInstance(result, VerifiedInstaller)
            self.assertEqual(installer, result.path.read_bytes())

    def test_installer_is_reverified_immediately_before_launch(self) -> None:
        installer = b"safe installer bytes"
        checksum = (
            "15c7bd0073705a92a42091875c976f622ee8ac13602127b9bd72d2c9d74ff3ba"
            f"  {PRODUCT.installer_filename}\n"
        ).encode("ascii")
        responses = iter([checksum, installer])
        launches = []

        def opener(_request, timeout):
            return FakeResponse(next(responses))

        release = parse_release(release_payload(), installed_version="0.4.0")
        with tempfile.TemporaryDirectory() as directory:
            updater = GitHubReleaseUpdater(
                opener=opener,
                process_launcher=lambda args, cwd: launches.append((args, cwd)),
            )
            verified = updater.download(release, destination_root=Path(directory))
            verified.path.write_bytes(b"replaced after verification")

            with self.assertRaisesRegex(UpdateError, "changed after download"):
                updater.launch_installer(verified)
            self.assertEqual([], launches)

    def test_checksum_mismatch_refuses_installer(self) -> None:
        responses = iter(
            [
                ("0" * 64 + f"  {PRODUCT.installer_filename}\n").encode("ascii"),
                b"tampered",
            ]
        )

        def opener(_request, timeout):
            return FakeResponse(next(responses))

        release = parse_release(release_payload(), installed_version="0.4.0")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(UpdateError, "checksum"):
                GitHubReleaseUpdater(opener=opener).download(
                    release, destination_root=Path(directory)
                )
            self.assertEqual([], list(Path(directory).rglob("*.exe")))




class ReleaseLookupFailureTests(unittest.TestCase):
    """GitHub answering is not the same as GitHub being unreachable."""

    def _updater(self, error):
        def opener(_request, _timeout):
            raise error

        return GitHubReleaseUpdater(opener=opener)

    def test_repository_without_a_published_release_is_not_an_error(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError("https://api.github.com", 404, "Not Found", {}, None)
        result = self._updater(error).check()
        self.assertEqual(UpdateCheckResult("no_release"), result)

    def test_current_release_is_distinct_from_repository_without_a_release(self) -> None:
        payload = release_payload(version=PRODUCT.version)

        def opener(_request, _timeout):
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        result = GitHubReleaseUpdater(opener=opener).check()
        self.assertEqual(UpdateCheckResult("latest"), result)

    def test_other_http_errors_say_github_replied(self) -> None:
        from urllib.error import HTTPError

        error = HTTPError("https://api.github.com", 503, "Unavailable", {}, None)
        with self.assertRaises(UpdateError) as caught:
            self._updater(error).check()
        self.assertIn("503", str(caught.exception))
        self.assertNotIn("connection", str(caught.exception).lower())

    def test_real_connectivity_failure_still_mentions_the_connection(self) -> None:
        with self.assertRaises(UpdateError) as caught:
            self._updater(OSError("no route to host")).check()
        self.assertIn("connection", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
