"""Linux desktop seams: Codex discovery, XDG paths, autostart, one scheduler.

These run on any host. Nothing here is skipped on Windows, because the point is
that one shared core answers both platforms and a Windows CI run must still
catch a Linux regression.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sentinel.host import platform_label, xdg_home, xdg_runtime_dir
from sentinel.product import PRODUCT
from sentinel.providers import platform_file_version
from sentinel.single_instance import SingleInstanceGuard
from sentinel.startup import XdgStartupManager, reconcile_startup_preference
from sentinel.transport import (
    CODEX_EXECUTABLE_ENV,
    CodexNotFoundError,
    _linux_desktop_app_roots,
    _linux_known_candidates,
    build_codex_command,
    find_codex_executable,
)


def make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path



def no_override(**environment):
    """Patch the environment with the discovery override explicitly cleared."""
    patch = mock.patch.dict(os.environ, environment, clear=False)

    class Guard:
        def __enter__(self):
            patch.start()
            os.environ.pop(CODEX_EXECUTABLE_ENV, None)
            return self

        def __exit__(self, *exc):
            patch.stop()
            return False

    return Guard()


class LinuxCodexDiscoveryTests(unittest.TestCase):
    """A Codex desktop install puts no `codex` on PATH, so PATH is not enough.

    Measured on Ubuntu with the Codex desktop app installed: `codex` was absent
    from PATH entirely, while `/usr/lib/chatgpt/resources/codex` was the binary
    the desktop app itself ran as `codex app-server`. Every case here passes an
    explicit `path_value`, so none of them read the real machine.
    """

    def test_the_codex_managed_app_server_binary_is_discovered_without_path(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            native = make_executable(
                codex_home / "plugins" / ".plugin-appserver" / "codex"
            )
            with no_override(CODEX_HOME=str(codex_home)):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(native.resolve(), found)

    def test_the_desktop_app_is_found_through_its_own_launcher(self):
        """The launcher on PATH is a symlink into the installation.

        Measured on this class of install: `/usr/bin/chatgpt` links to
        `/usr/lib/chatgpt/codex-launcher`, whose entire body is
        `exec "$(dirname "$(readlink -f "$0")")/ChatGPT" "$@"`. Following that
        symlink is the app's own rule for finding itself, so discovery works at
        whatever prefix it is installed under instead of only at guessed ones.
        """
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "opt" / "ChatGPT"
            bundled = make_executable(install / "resources" / "codex")
            launcher_target = make_executable(install / "codex-launcher")
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "chatgpt").symlink_to(launcher_target)

            with no_override(CODEX_HOME=str(Path(directory) / "none")):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    roots = _linux_desktop_app_roots(path_value=str(bin_dir))
                    candidates = _linux_known_candidates(path_value=str(bin_dir))
                    found = find_codex_executable(
                        path_value="", known_candidates=candidates
                    )

        self.assertEqual((install.resolve(),), roots)
        self.assertEqual(bundled.resolve(), found)

    def test_a_launcher_that_is_something_else_is_ignored(self):
        # A user's own script called `chatgpt` has no resources/codex beside it
        # and must never become a Codex candidate.
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            make_executable(bin_dir / "chatgpt")
            with no_override(CODEX_HOME=str(Path(directory) / "none")):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    candidates = _linux_known_candidates(path_value=str(bin_dir))
        self.assertEqual((), candidates)

    def test_the_measured_prefix_still_works_with_a_stripped_path(self):
        # An autostart session can start with almost no PATH. One measured
        # fallback prefix keeps an installed desktop app discoverable there.
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "usr" / "lib" / "chatgpt"
            bundled = make_executable(prefix / "resources" / "codex")
            with no_override(CODEX_HOME=str(Path(directory) / "none")):
                with mock.patch(
                    "sentinel.transport._LINUX_DESKTOP_PREFIXES", (str(prefix),)
                ):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(bundled.resolve(), found)

    def test_the_same_binary_reached_two_ways_is_offered_once(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "usr" / "lib" / "chatgpt"
            make_executable(install / "resources" / "codex")
            launcher_target = make_executable(install / "codex-launcher")
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "chatgpt").symlink_to(launcher_target)

            with no_override(CODEX_HOME=str(Path(directory) / "none")):
                with mock.patch(
                    "sentinel.transport._LINUX_DESKTOP_PREFIXES", (str(install),)
                ):
                    candidates = _linux_known_candidates(path_value=str(bin_dir))
        self.assertEqual(1, len(candidates), [str(c) for c in candidates])

    def test_an_explicit_override_outranks_every_discovered_install(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            make_executable(codex_home / "plugins" / ".plugin-appserver" / "codex")
            override = make_executable(Path(directory) / "nix" / "codex")
            environment = {
                "CODEX_HOME": str(codex_home),
                CODEX_EXECUTABLE_ENV: str(override),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(override.resolve(), found)

    def test_an_override_pointing_nowhere_falls_back_to_normal_discovery(self):
        # The override is a fallback, never a switch that can break discovery.
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            native = make_executable(
                codex_home / "plugins" / ".plugin-appserver" / "codex"
            )
            environment = {
                "CODEX_HOME": str(codex_home),
                CODEX_EXECUTABLE_ENV: str(Path(directory) / "missing" / "codex"),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(native.resolve(), found)

    def test_an_empty_override_is_treated_as_unset(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            native = make_executable(
                codex_home / "plugins" / ".plugin-appserver" / "codex"
            )
            environment = {"CODEX_HOME": str(codex_home), CODEX_EXECUTABLE_ENV: "   "}
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(native.resolve(), found)

    def test_the_override_never_names_a_directory_as_the_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            native = make_executable(
                codex_home / "plugins" / ".plugin-appserver" / "codex"
            )
            environment = {"CODEX_HOME": str(codex_home), CODEX_EXECUTABLE_ENV: directory}
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch("sentinel.transport._LINUX_DESKTOP_PREFIXES", ()):
                    found = find_codex_executable(
                        path_value="",
                        known_candidates=_linux_known_candidates(path_value=""),
                    )
        self.assertEqual(native.resolve(), found)

    def test_a_stale_desktop_bundle_does_not_shadow_a_newer_managed_binary(self):
        # Both installs expose the same executable, and either can be the newer
        # one. Discovery ranks by mtime for the same reason Windows prefers the
        # native app binary over an npm shim: app-server behavior moves.
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            managed = make_executable(
                codex_home / "plugins" / ".plugin-appserver" / "codex"
            )
            desktop_prefix = Path(directory) / "chatgpt"
            desktop = make_executable(desktop_prefix / "resources" / "codex")

            def ordered_with(newer: Path, older: Path):
                os.utime(older, (1_000_000, 1_000_000))
                os.utime(newer, (2_000_000, 2_000_000))
                with no_override(CODEX_HOME=str(codex_home)):
                    with mock.patch(
                        "sentinel.transport._LINUX_DESKTOP_PREFIXES",
                        (str(desktop_prefix),),
                    ):
                        return _linux_known_candidates(path_value="")

            self.assertEqual(managed, ordered_with(managed, desktop)[0])
            self.assertEqual(desktop, ordered_with(desktop, managed)[0])

    def test_path_is_still_searched_when_nothing_is_installed_natively(self):
        with tempfile.TemporaryDirectory() as directory:
            shim = make_executable(Path(directory) / ("codex.exe" if os.name == "nt" else "codex"))
            found = find_codex_executable(path_value=directory, known_candidates=())
        self.assertEqual(shim.resolve(), found)

    def test_no_codex_anywhere_reports_a_recognizable_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CodexNotFoundError) as caught:
                find_codex_executable(path_value=directory, known_candidates=())
        self.assertEqual("codex_not_found", caught.exception.category)

    def test_the_app_server_command_is_the_binary_itself_off_windows(self):
        command = build_codex_command(
            Path("/usr/lib/chatgpt/resources/codex"), "app-server", "--stdio"
        )
        self.assertEqual(
            [str(Path("/usr/lib/chatgpt/resources/codex")), "app-server", "--stdio"], command
        )


class XdgAutostartTests(unittest.TestCase):
    def test_enabling_writes_one_per_user_entry_and_disabling_removes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            executable = make_executable(Path(directory) / "UsageLoop")
            manager = XdgStartupManager(str(executable), config_home=config_home)

            self.assertFalse(manager.has_registration())
            manager.set_enabled(True)

            self.assertTrue(manager.is_enabled())
            self.assertEqual(config_home / "autostart" / "usageloop.desktop", manager.path)
            content = manager.path.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn(f"Name={PRODUCT.display_name}", content)
            self.assertIn("--background", content)
            self.assertIn("X-GNOME-Autostart-enabled=true", content)

            manager.set_enabled(False)
            self.assertFalse(manager.has_registration())
            self.assertFalse(manager.is_enabled())

    def test_no_temporary_file_is_left_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            manager = XdgStartupManager(
                str(make_executable(Path(directory) / "UsageLoop")),
                config_home=config_home,
            )
            manager.set_enabled(True)
            entries = sorted(p.name for p in (config_home / "autostart").iterdir())
        self.assertEqual(["usageloop.desktop"], entries)

    def test_an_upgrade_that_moves_the_executable_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            old = make_executable(Path(directory) / "old" / "UsageLoop")
            XdgStartupManager(str(old), config_home=config_home).set_enabled(True)

            new = make_executable(Path(directory) / "new" / "UsageLoop")
            manager = XdgStartupManager(str(new), config_home=config_home)
            self.assertFalse(manager.is_enabled())

            normalized = reconcile_startup_preference(True, manager)

            self.assertTrue(normalized)
            self.assertTrue(manager.is_enabled())
            self.assertIn(manager.command, manager.path.read_text(encoding="utf-8"))
            self.assertEqual(str(new.resolve()), manager.executable)

    def test_a_stale_entry_is_removed_when_the_saved_preference_is_off(self):
        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory) / "config"
            XdgStartupManager(
                str(Path(directory) / "gone" / "UsageLoop"), config_home=config_home
            ).set_enabled(True)
            manager = XdgStartupManager(
                str(Path(directory) / "current" / "UsageLoop"), config_home=config_home
            )

            normalized = reconcile_startup_preference(False, manager)

            self.assertFalse(normalized)
            self.assertFalse(manager.path.exists())

    def test_a_path_with_spaces_stays_one_quoted_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = make_executable(Path(directory) / "My Apps" / "UsageLoop")
            manager = XdgStartupManager(
                str(executable), config_home=Path(directory) / "config"
            )
        self.assertTrue(manager.command.startswith('"'))
        self.assertIn('" --background', manager.command)


@unittest.skipIf(os.name == "nt", "POSIX advisory locking")
class PosixSingleInstanceTests(unittest.TestCase):
    """One scheduler per user. The old Linux path always claimed success."""

    def test_an_unusable_lock_directory_never_grants_scheduler_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-directory"
            root.write_text("occupied", encoding="utf-8")
            guard = SingleInstanceGuard("usageloop-test", lock_root=root)
            with self.assertRaisesRegex(OSError, "instance lock"):
                guard.acquire()
            self.assertIsNone(guard._handle)
            guard.close()

    def test_a_second_copy_is_refused_while_the_first_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = SingleInstanceGuard("usageloop-test", lock_root=root)
            second = SingleInstanceGuard("usageloop-test", lock_root=root)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
            finally:
                first.close()
                second.close()

    def test_the_lock_is_released_for_the_next_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = SingleInstanceGuard("usageloop-test", lock_root=root)
            self.assertTrue(first.acquire())
            first.close()
            second = SingleInstanceGuard("usageloop-test", lock_root=root)
            try:
                self.assertTrue(second.acquire())
            finally:
                second.close()

    def test_different_names_do_not_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = SingleInstanceGuard("usageloop-a", lock_root=root)
            second = SingleInstanceGuard("usageloop-b", lock_root=root)
            try:
                self.assertTrue(first.acquire())
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()



class HostHelperTests(unittest.TestCase):
    def test_a_relative_xdg_value_is_rejected(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "not/absolute"}):
            resolved = xdg_home("XDG_CONFIG_HOME", Path("/fallback"))
        self.assertEqual(Path("/fallback"), resolved)

    def test_an_absolute_xdg_value_is_honored(self):
        absolute = Path("custom/config").resolve()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(absolute)}):
            resolved = xdg_home("XDG_CONFIG_HOME", Path("/fallback"))
        self.assertEqual(absolute, resolved)

    def test_a_missing_runtime_dir_is_reported_as_missing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            self.assertIsNone(xdg_runtime_dir())

    def test_the_platform_label_is_one_of_the_supported_hosts(self):
        self.assertIn(platform_label(), {"Windows", "Linux", "Desktop"})


@unittest.skipIf(os.name == "nt", "Windows reads its own version resource")
class PlatformVersionTests(unittest.TestCase):
    def test_no_windows_version_resource_is_read_off_windows(self):
        # runtime_identity (size and mtime) remains the compatibility key, so a
        # missing version string only thins the diagnostic summary.
        self.assertIsNone(platform_file_version(Path("/usr/lib/chatgpt/resources/codex")))


if __name__ == "__main__":
    unittest.main()
