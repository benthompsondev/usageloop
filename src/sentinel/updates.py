"""User-initiated GitHub Release checks and checksum-gated installer downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import BinaryIO, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .product import PRODUCT, ProductMetadata


CHECK_TIMEOUT_SECONDS = 15
DOWNLOAD_TIMEOUT_SECONDS = 60
MAX_RELEASE_BYTES = 1_000_000
MAX_CHECKSUM_BYTES = 8_192
MAX_INSTALLER_BYTES = 300 * 1024 * 1024


class UpdateError(RuntimeError):
    """An update operation failed without changing the installed app."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: tuple[str, ...]
    page_url: str
    installer: ReleaseAsset
    checksum: ReleaseAsset


@dataclass(frozen=True)
class VerifiedInstaller:
    path: Path
    sha256: str


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if match is None:
        raise UpdateError("The release version was not understood.")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate: str, installed: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(installed)


def summarize_release_notes(body: object, *, limit: int = 4) -> tuple[str, ...]:
    if not isinstance(body, str):
        return ()
    notes: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", line)
        line = re.sub(r"[`*_]", "", line).strip()
        if not line:
            continue
        notes.append(line[:220])
        if len(notes) >= limit:
            break
    return tuple(notes)


def _safe_download_url(value: object) -> str:
    if not isinstance(value, str):
        raise UpdateError("A release download URL was missing.")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }:
        raise UpdateError("A release download URL was not trusted.")
    return value


def parse_release(
    payload: object,
    *,
    installed_version: str,
    product: ProductMetadata = PRODUCT,
) -> ReleaseInfo | None:
    if not isinstance(payload, dict) or payload.get("draft") is True:
        raise UpdateError("GitHub returned an unusable release record.")
    if payload.get("prerelease") is True:
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("The release did not include a version.")
    version = ".".join(str(part) for part in _version_tuple(tag))
    if not is_newer_version(version, installed_version):
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The release did not include downloadable files.")
    found: dict[str, ReleaseAsset] = {}
    expected = {product.installer_filename, product.checksum_filename}
    for item in assets:
        if not isinstance(item, dict) or item.get("name") not in expected:
            continue
        name = str(item["name"])
        if name in found:
            raise UpdateError(f"The release included duplicate {name} files.")
        found[name] = ReleaseAsset(name, _safe_download_url(item.get("browser_download_url")))
    missing = expected.difference(found)
    if missing:
        raise UpdateError("The release is missing its verified Windows installer.")
    page_url = _safe_download_url(payload.get("html_url"))
    return ReleaseInfo(
        version=version,
        notes=summarize_release_notes(payload.get("body")),
        page_url=page_url,
        installer=found[product.installer_filename],
        checksum=found[product.checksum_filename],
    )


Opener = Callable[[Request, int], BinaryIO]
ProcessLauncher = Callable[[list[str], str], object]


class GitHubReleaseUpdater:
    """One-shot update operations. The caller owns scheduling and user consent."""

    def __init__(
        self,
        *,
        product: ProductMetadata = PRODUCT,
        opener: Opener | None = None,
        process_launcher: ProcessLauncher | None = None,
    ):
        self.product = product
        self._open = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
        self._launch = process_launcher or (
            lambda args, cwd: subprocess.Popen(args, cwd=cwd)
        )

    def check(self) -> ReleaseInfo | None:
        request = self._request(self.product.release_api_url)
        try:
            with self._open(request, CHECK_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RELEASE_BYTES + 1)
        except (OSError, TimeoutError) as exc:
            raise UpdateError("GitHub could not be reached. Check your connection and try again.") from exc
        if len(raw) > MAX_RELEASE_BYTES:
            raise UpdateError("GitHub returned an unexpectedly large release record.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("GitHub returned a release record Sentinel could not read.") from exc
        return parse_release(
            payload, installed_version=self.product.version, product=self.product
        )

    def download(
        self,
        release: ReleaseInfo,
        *,
        destination_root: Path | None = None,
    ) -> VerifiedInstaller:
        root = destination_root or Path(tempfile.gettempdir()) / f"{self.product.app_data_folder}-updates"
        destination = root / release.version
        destination.mkdir(parents=True, exist_ok=True)
        expected = self._download_checksum(release.checksum)
        target = destination / self.product.installer_filename
        partial = target.with_suffix(f"{target.suffix}.part")
        digest = hashlib.sha256()
        total = 0
        try:
            with self._open(
                self._request(release.installer.download_url), DOWNLOAD_TIMEOUT_SECONDS
            ) as response, partial.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INSTALLER_BYTES:
                        raise UpdateError("The installer download was unexpectedly large.")
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest().lower() != expected:
                raise UpdateError("The installer checksum did not match the release.")
            os.replace(partial, target)
            return VerifiedInstaller(target, expected)
        except UpdateError:
            partial.unlink(missing_ok=True)
            raise
        except (OSError, TimeoutError) as exc:
            partial.unlink(missing_ok=True)
            raise UpdateError("The installer could not be downloaded. Try again.") from exc

    def launch_installer(self, installer: VerifiedInstaller) -> None:
        resolved = installer.path.resolve()
        if resolved.name != self.product.installer_filename or not resolved.is_file():
            raise UpdateError("The verified installer is no longer available.")
        if self._hash_file(resolved) != installer.sha256:
            raise UpdateError("The installer changed after download and will not be opened.")
        try:
            self._launch([os.fspath(resolved)], os.fspath(resolved.parent))
        except OSError as exc:
            raise UpdateError("Windows could not start the installer.") from exc

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INSTALLER_BYTES:
                        raise UpdateError("The verified installer is unexpectedly large.")
                    digest.update(chunk)
        except OSError as exc:
            raise UpdateError("The verified installer could not be read again.") from exc
        return digest.hexdigest().lower()

    def _download_checksum(self, asset: ReleaseAsset) -> str:
        try:
            with self._open(
                self._request(asset.download_url), DOWNLOAD_TIMEOUT_SECONDS
            ) as response:
                raw = response.read(MAX_CHECKSUM_BYTES + 1)
        except (OSError, TimeoutError) as exc:
            raise UpdateError("The release checksum could not be downloaded.") from exc
        if len(raw) > MAX_CHECKSUM_BYTES:
            raise UpdateError("The release checksum was unexpectedly large.")
        try:
            value = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise UpdateError("The release checksum could not be read.") from exc
        match = re.fullmatch(
            rf"([0-9a-fA-F]{{64}})\s+\*?{re.escape(self.product.installer_filename)}",
            value,
        )
        if match is None:
            raise UpdateError("The release checksum did not name the expected installer.")
        return match.group(1).lower()

    def _request(self, url: str) -> Request:
        return Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{self.product.github_repo}/{self.product.version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
