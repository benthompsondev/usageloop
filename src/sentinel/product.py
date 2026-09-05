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
    legacy_install_folder: str
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
    def bug_report_url(self) -> str:
        return f"{self.issues_url}/new?template=bug_report.yml"

    @property
    def feature_request_url(self) -> str:
        return f"{self.issues_url}/new?template=feature_request.yml"

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

    @property
    def app_id_guid(self) -> str:
        """The stable Inno identity as a normal registry-key GUID."""
        return "{" + self.app_id.strip("{}") + "}"

    def packaging_metadata(self) -> dict[str, str]:
        """Every name the Windows build script needs, derived values included.

        `dataclasses.asdict` silently omits properties. The build script reads
        this explicit mapping so derived packaging names cannot drift.
        """
        from dataclasses import asdict

        data = {key: str(value) for key, value in asdict(self).items()}
        data["dist_folder_name"] = self.dist_folder_name
        data["single_instance_name"] = self.single_instance_name
        data["app_id_guid"] = self.app_id_guid
        return data


# The repository was renamed from `codex-window-sentinel` to `usageloop` so the
# slug matches the product. Update discovery is
# `https://api.github.com/repos/<owner>/<repo>/releases/latest`, and copies built
# before the rename still request the old slug. GitHub answers those with a
# redirect to the same repository, and the updater's GET follows it, so 0.8.0
# installs keep updating. That redirect is the only thing keeping them working:
# do not rename again without checking it still resolves.
PRODUCT = ProductMetadata(
    display_name="UsageLoop",
    tagline="Keep your Codex reset clock running.",
    version="1.3.0",
    github_owner="benthompsondev",
    github_repo="usageloop",
    executable_name="UsageLoop.exe",
    icon_filename="usageloop.ico",
    version_resource_filename="version_info.txt",
    installer_filename="UsageLoop-Setup.exe",
    checksum_filename="UsageLoop-Setup.exe.sha256",
    app_data_folder="UsageLoop",
    # Local state written under the previous name. It carries the one-shot
    # provider guards, so it is migrated rather than abandoned.
    legacy_app_data_folder="CodexWindowSentinel",
    legacy_install_folder="Window Sentinel",
    publisher="UsageLoop",
    app_id="{{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}",
)
