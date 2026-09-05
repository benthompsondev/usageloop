"""Apply a verified Linux release archive using the per-user install model.

The Windows flow hands a verified setup executable to Windows and exits. This is
the same shape: verify, unpack under our own control, hand the unpacked bundle's
own installer the job, and exit so nothing is replaced underneath a running
process. Nothing here needs root, and nothing outside the user's XDG
directories is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from .host import xdg_data_home, xdg_state_home
from .product import PRODUCT, ProductMetadata


#: The bundle unpacks to about 160 MB across ~340 members. These are ceilings
#: that a normal release stays far below, not predictions of its size.
MAX_EXTRACTED_BYTES = 600 * 1024 * 1024
MAX_MEMBERS = 5_000

INSTALLER_SCRIPT_NAME = "install.sh"
UPDATE_LOG_NAME = "update.log"


class LinuxUpdateError(RuntimeError):
    """An update step failed without changing the installed app."""


@dataclass(frozen=True)
class StagedUpdate:
    """A verified archive unpacked and checked, ready for its own installer."""

    root: Path
    bundle: Path
    installer: Path
    executable: Path


def default_install_prefix(product: ProductMetadata = PRODUCT) -> Path:
    return xdg_data_home() / product.app_data_folder.lower()


def running_install_root(executable: str | None = None) -> Path:
    """The directory the running copy lives in, resolved through symlinks."""
    return Path(executable or sys.executable).resolve().parent


def is_managed_install(
    *, executable: str | None = None, product: ProductMetadata = PRODUCT
) -> bool:
    """True when this copy is the one `install.sh` manages.

    A copy run straight out of an extracted tarball, or installed somewhere
    else by hand, is not ours to replace. Those get manual guidance instead of
    an install button that would quietly create a second installation.
    """
    try:
        return running_install_root(executable) == default_install_prefix(product).resolve()
    except OSError:
        return False


def update_log_path(product: ProductMetadata = PRODUCT) -> Path:
    return xdg_state_home() / product.app_data_folder.lower() / UPDATE_LOG_NAME


def _rejected(reason: str) -> LinuxUpdateError:
    return LinuxUpdateError(f"The downloaded archive was rejected: {reason}.")


def _safe_member_path(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_absolute() or name.startswith("/"):
        raise _rejected("it contained an absolute path")
    if any(part == ".." for part in candidate.parts):
        raise _rejected("it tried to write outside the update folder")
    return candidate


def _check_symlink(member: tarfile.TarInfo, relative: Path) -> None:
    """Allow the Qt library aliases, refuse anything that leaves the bundle."""
    target = member.linkname
    if not target or Path(target).is_absolute():
        raise _rejected("it contained an absolute link")
    resolved = os.path.normpath(os.path.join(str(relative.parent), target))
    if resolved.startswith("..") or os.path.isabs(resolved):
        raise _rejected("it contained a link pointing outside the update folder")


def extract_bundle(archive: Path, destination: Path, *, expected_bundle: str) -> Path:
    """Unpack a verified archive, refusing anything that could escape.

    The checksum already proves the bytes are the published ones. This still
    validates every member, because "the publisher would not do that" is not a
    property worth relying on when the result is writing to arbitrary paths.
    """
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    members = 0
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                members += 1
                if members > MAX_MEMBERS:
                    raise _rejected("it contained too many files")
                relative = _safe_member_path(member.name)
                if relative.parts[:1] != (expected_bundle,):
                    raise _rejected(
                        f"it did not unpack into a single {expected_bundle} folder"
                    )
                if member.issym() or member.islnk():
                    _check_symlink(member, relative)
                elif member.isdir():
                    pass
                elif member.isfile():
                    total += max(member.size, 0)
                    if total > MAX_EXTRACTED_BYTES:
                        raise _rejected("it unpacked to an unexpected size")
                else:
                    raise _rejected("it contained something that is not a file")
                bundle.extract(member, destination, set_attrs=True)
    except LinuxUpdateError:
        raise
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise LinuxUpdateError("The downloaded archive could not be unpacked.") from exc
    return destination / expected_bundle


def stage_update(
    archive: Path,
    *,
    version: str,
    staging_root: Path,
    product: ProductMetadata = PRODUCT,
) -> StagedUpdate:
    """Unpack a verified archive and prove it looks like a UsageLoop bundle."""
    expected = product.linux_bundle_name(version)
    root = staging_root / f"unpacked-{version}"
    if root.exists():
        _remove_tree(root)
    bundle = extract_bundle(archive, root, expected_bundle=expected)
    installer = bundle / INSTALLER_SCRIPT_NAME
    executable = bundle / product.dist_folder_name / product.dist_folder_name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise LinuxUpdateError("The downloaded archive did not contain a runnable app.")
    if not installer.is_file():
        raise LinuxUpdateError("The downloaded archive did not contain its installer.")
    installer.chmod(installer.stat().st_mode | 0o100)
    return StagedUpdate(root=root, bundle=bundle, installer=installer, executable=executable)


def install_command(staged: StagedUpdate, *, prefix: Path | None = None) -> list[str]:
    """The exact command a user could run themselves. Shown, then run."""
    target = prefix or default_install_prefix()
    return [os.fspath(staged.installer), "--prefix", os.fspath(target)]


def apply_update(
    staged: StagedUpdate,
    *,
    prefix: Path | None = None,
    product: ProductMetadata = PRODUCT,
    launcher=None,
) -> None:
    """Hand the unpacked bundle's own installer the swap, then let the app exit.

    The installer waits for this process to go away before it replaces
    anything, so no file is pulled out from under a running app, and it
    relaunches the new copy afterwards.
    """
    target = prefix or default_install_prefix(product)
    if not staged.installer.is_file():
        raise LinuxUpdateError("The verified update is no longer available.")
    log = update_log_path(product)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open("wb")
    except OSError:
        stream = subprocess.DEVNULL
    # Explicitly bash: the installer is a bash script, and /bin/sh is dash on
    # Debian and Ubuntu, where it would resolve its own directory wrongly.
    command = [
        _bash_path(),
        os.fspath(staged.installer),
        "--prefix",
        os.fspath(target),
        # Passed explicitly so the launcher can never be written against a
        # different XDG_DATA_HOME than the one this install lives under.
        "--data-home",
        os.fspath(xdg_data_home()),
        "--wait-for-pid",
        str(os.getpid()),
        "--relaunch",
    ]
    spawn = launcher or _spawn_detached
    try:
        spawn(command, staged.bundle, stream)
    except OSError as exc:
        raise LinuxUpdateError("The update installer could not be started.") from exc
    finally:
        if stream is not subprocess.DEVNULL:
            stream.close()


def _bash_path() -> str:
    from shutil import which

    found = which("bash")
    if found is None:
        raise LinuxUpdateError(
            "bash is required to install the update and was not found."
        )
    return found


def _spawn_detached(command: list[str], cwd: Path, stream) -> None:
    subprocess.Popen(
        command,
        cwd=os.fspath(cwd),
        stdin=subprocess.DEVNULL,
        stdout=stream,
        stderr=subprocess.STDOUT,
        # A new session, so the installer outlives the app it is replacing.
        start_new_session=True,
    )


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
