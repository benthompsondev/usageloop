"""Product identity and release metadata kept in one replaceable place."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductMetadata:
    display_name: str
    tagline: str
    version: str
    github_owner: str
    github_repo: str
    executable_name: str
    icon_filename: str
    version_resource_filename: str
    installer_filename: str
    checksum_filename: str
    app_data_folder: str
    legacy_app_data_folder: str
    publisher: str
    app_id: str

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.github_owner}/{self.github_repo}"

    @property
    def releases_url(self) -> str:
        return f"{self.github_url}/releases/latest"

    @property
    def issues_url(self) -> str:
        return f"{self.github_url}/issues"

    @property
    def release_api_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.github_owner}/"
            f"{self.github_repo}/releases/latest"
        )

    @property
    def dist_folder_name(self) -> str:
        """PyInstaller COLLECT directory, derived so packaging cannot drift."""
        return self.executable_name.removesuffix(".exe")

    @property
    def single_instance_name(self) -> str:
        identifier = self.app_id.strip("{}")
        return f"Local\\{self.display_name}-{identifier}"

    def packaging_metadata(self) -> dict[str, str]:
        """Every name the Windows build script needs, derived values included.

        `dataclasses.asdict` silently omits properties. The build script reads
        this explicit mapping so derived packaging names cannot drift.
        """
        from dataclasses import asdict

        data = {key: str(value) for key, value in asdict(self).items()}
        data["dist_folder_name"] = self.dist_folder_name
        data["single_instance_name"] = self.single_instance_name
        return data


# The GitHub owner and repo intentionally keep the original slug. Update
# discovery is `https://api.github.com/repos/<owner>/<repo>/releases/latest`, so
# renaming the repository would break every already-installed copy's updater.
# The user-facing product name is independent of that slug.
PRODUCT = ProductMetadata(
    display_name="UsageLoop",
    tagline="Keep your Codex reset clock running.",
    version="0.8.0",
    github_owner="benthompsondev",
    github_repo="codex-window-sentinel",
    executable_name="UsageLoop.exe",
    icon_filename="usageloop.ico",
    version_resource_filename="version_info.txt",
    installer_filename="UsageLoop-Setup.exe",
    checksum_filename="UsageLoop-Setup.exe.sha256",
    app_data_folder="UsageLoop",
    # Local state written under the previous name. It carries the one-shot
    # provider guards, so it is migrated rather than abandoned.
    legacy_app_data_folder="CodexWindowSentinel",
    publisher="Ben Thompson",
    app_id="{{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}",
)
