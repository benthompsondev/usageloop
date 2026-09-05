"""Linux update staging: what may be unpacked, and what may be replaced.

The checksum already proves an archive is the published one. These cases cover
what happens anyway, because "the publisher would not ship that" is not a
property worth trusting when the consequence is writing to arbitrary paths.
"""

import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sentinel.linux_update import (
    MAX_MEMBERS,
    LinuxUpdateError,
    apply_update,
    default_install_prefix,
    extract_bundle,
    install_command,
    is_managed_install,
    stage_update,
)
from sentinel.product import PRODUCT


BUNDLE = PRODUCT.linux_bundle_name("9.9.9")


def add_file(archive: tarfile.TarFile, name: str, data: bytes = b"x", mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    archive.addfile(info, io.BytesIO(data))


def add_link(archive: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    archive.addfile(info)


def build_archive(path: Path, populate) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        populate(archive)
    return path


def good_bundle(archive: tarfile.TarFile) -> None:
    add_file(archive, f"{BUNDLE}/{PRODUCT.dist_folder_name}/{PRODUCT.dist_folder_name}", b"app", 0o755)
    add_file(archive, f"{BUNDLE}/{PRODUCT.dist_folder_name}/_internal/base_library.zip")
    # The real bundle carries relative Qt aliases, so they must stay allowed.
    add_link(
        archive,
        f"{BUNDLE}/{PRODUCT.dist_folder_name}/_internal/libQt6Core.so.6",
        "PySide6/Qt/lib/libQt6Core.so.6",
    )
    add_file(archive, f"{BUNDLE}/install.sh", b"#!/bin/sh\n", 0o755)
    add_file(archive, f"{BUNDLE}/README.txt")


class ExtractionHardeningTests(unittest.TestCase):
    def extract(self, populate, *, expected: str = BUNDLE):
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", populate)
        return extract_bundle(archive, root / "out", expected_bundle=expected)

    def test_a_normal_bundle_unpacks_including_its_relative_links(self) -> None:
        bundle = self.extract(good_bundle)
        executable = bundle / PRODUCT.dist_folder_name / PRODUCT.dist_folder_name
        self.assertTrue(executable.is_file())
        self.assertTrue(os.access(executable, os.X_OK))
        link = bundle / PRODUCT.dist_folder_name / "_internal" / "libQt6Core.so.6"
        self.assertTrue(link.is_symlink())
        self.assertEqual("PySide6/Qt/lib/libQt6Core.so.6", os.readlink(link))

    def test_an_absolute_member_is_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            add_file(archive, "/etc/cron.d/pwn")

        with self.assertRaisesRegex(LinuxUpdateError, "absolute path|single"):
            self.extract(populate)

    def test_a_parent_traversal_member_is_refused(self) -> None:
        def populate(archive):
            add_file(archive, f"{BUNDLE}/../../.bashrc", b"pwn")

        with self.assertRaisesRegex(LinuxUpdateError, "outside the update folder"):
            self.extract(populate)

    def test_a_link_escaping_the_bundle_is_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            add_link(archive, f"{BUNDLE}/escape", "../../../../etc/passwd")

        with self.assertRaisesRegex(LinuxUpdateError, "outside the update folder"):
            self.extract(populate)

    def test_an_absolute_link_is_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            add_link(archive, f"{BUNDLE}/escape", "/etc/passwd")

        with self.assertRaisesRegex(LinuxUpdateError, "absolute link"):
            self.extract(populate)

    def test_a_device_node_is_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            info = tarfile.TarInfo(f"{BUNDLE}/dev")
            info.type = tarfile.CHRTYPE
            info.devmajor, info.devminor = 1, 3
            archive.addfile(info)

        with self.assertRaisesRegex(LinuxUpdateError, "not a file"):
            self.extract(populate)

    def test_a_second_top_level_folder_is_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            add_file(archive, "somewhere-else/payload")

        with self.assertRaisesRegex(LinuxUpdateError, "single"):
            self.extract(populate)

    def test_an_archive_for_a_different_version_is_refused(self) -> None:
        with self.assertRaisesRegex(LinuxUpdateError, "single"):
            self.extract(good_bundle, expected=PRODUCT.linux_bundle_name("0.0.1"))

    def test_too_many_members_are_refused(self) -> None:
        def populate(archive):
            good_bundle(archive)
            for index in range(MAX_MEMBERS + 1):
                add_file(archive, f"{BUNDLE}/filler/{index}")

        with self.assertRaisesRegex(LinuxUpdateError, "too many files"):
            self.extract(populate)

    def test_an_oversized_bundle_is_refused(self) -> None:
        # The cap is lowered rather than fabricating a 600 MB archive, so the
        # guard is exercised without writing most of a gigabyte to disk.
        def populate(archive):
            good_bundle(archive)
            add_file(archive, f"{BUNDLE}/payload", b"x" * 4096)

        with mock.patch("sentinel.linux_update.MAX_EXTRACTED_BYTES", 1024):
            with self.assertRaisesRegex(LinuxUpdateError, "unexpected size"):
                self.extract(populate)


class StagingTests(unittest.TestCase):
    def stage(self, populate, version="9.9.9"):
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", populate)
        return stage_update(archive, version=version, staging_root=root)

    def test_a_good_bundle_stages_with_its_installer_and_executable(self) -> None:
        staged = self.stage(good_bundle)
        self.assertTrue(staged.executable.is_file())
        self.assertTrue(staged.installer.is_file())
        self.assertTrue(os.stat(staged.installer).st_mode & stat.S_IXUSR)

    def test_a_bundle_without_a_runnable_app_is_refused(self) -> None:
        def populate(archive):
            add_file(archive, f"{BUNDLE}/install.sh", b"#!/bin/sh\n", 0o755)
            add_file(archive, f"{BUNDLE}/README.txt")

        with self.assertRaisesRegex(LinuxUpdateError, "runnable app"):
            self.stage(populate)

    def test_a_bundle_without_its_installer_is_refused(self) -> None:
        def populate(archive):
            add_file(
                archive,
                f"{BUNDLE}/{PRODUCT.dist_folder_name}/{PRODUCT.dist_folder_name}",
                b"app",
                0o755,
            )

        with self.assertRaisesRegex(LinuxUpdateError, "installer"):
            self.stage(populate)


class ApplyTests(unittest.TestCase):
    def test_the_installer_is_told_to_wait_for_this_process_and_relaunch(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", good_bundle)
        staged = stage_update(archive, version="9.9.9", staging_root=root)
        prefix = root / "installed"
        calls = []

        apply_update(
            staged,
            prefix=prefix,
            launcher=lambda command, cwd, stream: calls.append((command, cwd)),
        )

        self.assertEqual(1, len(calls))
        command, cwd = calls[0]
        self.assertEqual(os.fspath(staged.installer), command[1])
        self.assertIn("--prefix", command)
        self.assertEqual(os.fspath(prefix), command[command.index("--prefix") + 1])
        # Nothing may be replaced while this process still holds the bundle open.
        self.assertIn("--wait-for-pid", command)
        self.assertEqual(str(os.getpid()), command[command.index("--wait-for-pid") + 1])
        self.assertIn("--relaunch", command)
        # The launcher location travels with the prefix. Leaving it to the
        # inherited environment let one real run write a launcher pointing at
        # a different installation entirely.
        self.assertIn("--data-home", command)
        self.assertEqual(staged.bundle, cwd)
        self.assertTrue(command[0].endswith("bash"), command[0])

    def test_the_installer_runs_under_bash_not_plain_sh(self) -> None:
        # install.sh uses [[ ]] and BASH_SOURCE. Under dash, which is /bin/sh on
        # Debian and Ubuntu, it resolved its own directory wrongly and copied
        # from the wrong place.
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", good_bundle)
        staged = stage_update(archive, version="9.9.9", staging_root=root)
        calls = []
        apply_update(
            staged,
            prefix=root / "installed",
            launcher=lambda command, cwd, stream: calls.append(command),
        )
        self.assertTrue(calls[0][0].endswith("bash"), calls[0][0])
        self.assertNotEqual("/bin/sh", calls[0][0])

    def test_a_missing_installer_refuses_to_launch_anything(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", good_bundle)
        staged = stage_update(archive, version="9.9.9", staging_root=root)
        staged.installer.unlink()

        with self.assertRaisesRegex(LinuxUpdateError, "no longer available"):
            apply_update(
                staged,
                prefix=root / "installed",
                launcher=lambda *_args: self.fail("nothing may be launched"),
            )

    def test_the_shown_command_is_the_one_that_would_run(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        root = Path(directory)
        archive = build_archive(root / "bundle.tar.gz", good_bundle)
        staged = stage_update(archive, version="9.9.9", staging_root=root)
        prefix = root / "installed"

        shown = install_command(staged, prefix=prefix)
        calls = []
        apply_update(
            staged, prefix=prefix, launcher=lambda c, cwd, s: calls.append(c)
        )
        launched = calls[0]
        self.assertEqual(shown[0], launched[1])
        self.assertEqual(shown[1:3], launched[2:4])


class ManagedInstallTests(unittest.TestCase):
    """Only the installation install.sh manages may be replaced in place."""

    def test_a_copy_at_the_install_prefix_is_managed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "usageloop"
            prefix.mkdir()
            executable = prefix / "UsageLoop"
            executable.touch()
            with mock.patch(
                "sentinel.linux_update.default_install_prefix", return_value=prefix
            ):
                self.assertTrue(is_managed_install(executable=str(executable)))

    def test_a_copy_run_from_an_extracted_tarball_is_not_managed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "usageloop"
            prefix.mkdir()
            elsewhere = Path(directory) / "Downloads" / "UsageLoop"
            elsewhere.parent.mkdir(parents=True)
            elsewhere.touch()
            with mock.patch(
                "sentinel.linux_update.default_install_prefix", return_value=prefix
            ):
                self.assertFalse(is_managed_install(executable=str(elsewhere)))

    def test_the_prefix_follows_xdg_data_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
                self.assertEqual(Path(directory) / "usageloop", default_install_prefix())


if __name__ == "__main__":
    unittest.main()
