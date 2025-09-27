"""The main command-line entrypoint."""

import logging
import os
import pathlib
from typing import TYPE_CHECKING

import jproperties
import requests
from defusedxml import ElementTree

from jmullan.cmd import cmd
from jmullan.logging import easy_logging
from jmullan.logging.helpers import logging_context

from jmullan.artificer.sonatype import (
    POM_NAMESPACE,
    ArtifactVersion,
    ArtifactVersionExtension,
    GradleSettings,
    Group,
    Repo,
    RepoArtifact,
    find_poms,
    find_properties,
    get_artifact_versions_from_repo_artifact_metadata,
    guess_repo,
    read_gradle_settings,
    text_of,
    validate_not_none,
)

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

logger = logging.getLogger(__name__)

REPOS = {"snapshots": "repositories/snapshots", "releases": "repositories/releases", "public": "groups/public"}
OK_RESPONSE_CODE = 200
ERROR_RESPONSE_CODE_THRESHOLD = 400


def resolve_filename(filename: str) -> str:
    """Turn a filename into a resolved Path's string."""
    return resolve_path(pathlib.Path(filename))


def resolve_path(file_path: pathlib.Path) -> str:
    """Turn a Path into a resolved string."""
    return str(file_path.resolve())


class Main(cmd.Main):
    """Interact with sonatype nexus."""

    def __init__(self) -> None:
        super().__init__()
        self.parser.add_argument(
            "--version",
            dest="version",
            default=None,
            help="Force this version",
        )
        self.parser.add_argument(
            "--group",
            dest="group_id",
            default=None,
            help="Force this group",
        )
        self.parser.add_argument(
            "--artifact",
            dest="artifact",
            default=None,
            help="Force this artifact",
        )
        self.parser.add_argument(
            "--extension",
            dest="extension",
            default=None,
            help="Force this artifact",
        )
        self.parser.add_argument(
            "--sonatype-base-url",
            dest="sonatype_base_url",
            default=os.environ.get("SONATYPE_BASE_URL"),
        )
        self.parser.add_argument("--thorough", dest="thorough", action="store_true", default=False)
        self.parser.add_argument(
            "--nexus-repo",
            dest="nexus_repo",
            choices=list(REPOS.keys()),
            default=None,
        )
        self.configs: dict[str, dict] = {}
        self.poms: dict[str, Element] = {}
        self.gets: dict[str, requests.Response] = {}
        self.repos: dict[str, Repo] = {}
        self.expected_extensions: set[str] = set()

    def setup(self) -> None:
        """Do something after parsing args but before main."""
        super().setup()
        if self.args.verbose:
            easy_logging.easy_initialize_logging("DEBUG")
        else:
            easy_logging.easy_initialize_logging()

    def main(self) -> None:
        """Interact with sonatype nexus."""
        super().main()

        if self.args.extension:
            self.expected_extensions = {self.args.extension}
        else:
            self.expected_extensions = {"pom", "jar"}

        sonatype_path = f"{self.args.sonatype_base_url}/nexus/content"
        for nexus_repo_name, nexus_repo_path in REPOS.items():
            self.repos[nexus_repo_name] = Repo(sonatype_path, nexus_repo_path, nexus_repo_name)

        for path in find_properties():
            self.load_properties_into_configs(path)
        for path in find_poms():
            self.load_pom_xml_into_configs(path)
        gradle_settings = read_gradle_settings()

        artifact_versions: set[ArtifactVersion] = set()
        sonatype_artifact_versions: set[ArtifactVersion] = set()

        if self.configs:
            a, b = self.build_artifact_versions_from_configs(gradle_settings)
            artifact_versions.update(a)
            sonatype_artifact_versions.update(b)
        if self.poms:
            a, b = self.build_artifact_versions_from_poms()
            artifact_versions.update(a)
            sonatype_artifact_versions.update(b)

        if self.args.thorough:
            artifact_versions.update(sonatype_artifact_versions)

        artifact_version_extensions = self.build_artifact_version_extensions(artifact_versions)

        for artifact_version_extension in artifact_version_extensions:
            self.get(artifact_version_extension.sha_url)

    def get(self, url: str) -> requests.Response | None:
        """Get something via HTTP GET or from an in-memory cache."""
        if url not in self.gets:
            try:
                self.gets[url] = requests.get(url, timeout=360)
                if self.gets[url].status_code >= ERROR_RESPONSE_CODE_THRESHOLD:
                    logger.warning("GET %s %s", self.gets[url].status_code, url)
                else:
                    logger.info("GET %s %s", self.gets[url].status_code, url)
            except Exception:
                logger.exception("Error fetching %s", url)
        return self.gets.get(url)

    def load_properties_into_configs(self, file_path: pathlib.Path) -> None:
        """Extract properties from a .properties file and load them into dictionaries for later."""
        try:
            with file_path.open("rb") as config_file:
                config = jproperties.Properties()
                config.load(config_file)
                self.configs[resolve_path(file_path)] = {}
                for item in config.items():
                    self.configs[resolve_path(file_path)][item[0]] = item[1].data
        except Exception:
            logger.exception("Oopies")

    def load_pom_xml_into_configs(self, file_path: pathlib.Path) -> None:
        """Extract properties from a pom file and load them into dictionaries for later."""
        try:
            with file_path.open("rb") as config_file:
                tree = ElementTree.parse(config_file)
                root = tree.getroot()
                self.poms[resolve_path(file_path)] = root
        except Exception:
            logger.exception("Oopies")

    def build_artifact_version_extensions(
        self, artifact_versions: set[ArtifactVersion]
    ) -> set[ArtifactVersionExtension]:
        """Ask sonatype for extensions for known artifact versions."""
        artifact_version_extensions: set[ArtifactVersionExtension] = set()
        for artifact_version in sorted(artifact_versions, key=lambda x: x.url):
            with logging_context(**artifact_version.context()):
                extension: str | None = self.args.extension
                if extension is not None:
                    artifact_version_extension = ArtifactVersionExtension(
                        artifact_version, extension, artifact_version.version
                    )
                    artifact_version_extensions.add(artifact_version_extension)
                if artifact_version.repo_artifact.nexus_repo.name in ("releases", "public"):
                    for extension in self.expected_extensions:
                        artifact_sub_version = ArtifactVersionExtension(
                            artifact_version, extension, artifact_version.version
                        )
                        artifact_version_extensions.add(artifact_sub_version)
                if artifact_version.repo_artifact.nexus_repo.name == "snapshots":
                    artifact_version_extensions.update(self.get_artifact_version_extensions(artifact_version))
        return artifact_version_extensions

    def build_artifact_versions_from_poms(self) -> tuple[set[ArtifactVersion], set[ArtifactVersion]]:
        """Build artifact versions from maven poms."""
        artifact_versions: set[ArtifactVersion] = set()
        sonatype_artifact_versions: set[ArtifactVersion] = set()
        artifact_versions.update(self.get_artifact_versions_from_poms())
        got_metadata: set[str] = set()
        for artifact_version in artifact_versions:
            artifact_group = artifact_version.repo_artifact.group
            artifact_id = artifact_version.repo_artifact.artifact_id

            validate_not_none(artifact_id)

            artifact_stub = f"{artifact_group.group_id}:{artifact_id}"
            if artifact_stub not in got_metadata:
                sonatype_artifact_versions.update(self.get_artifact_versions_from_sonatype(artifact_group, artifact_id))
                got_metadata.add(artifact_stub)
        return artifact_versions, sonatype_artifact_versions

    def build_artifact_versions_from_configs(
        self, gradle_settings: GradleSettings | None
    ) -> tuple[set[ArtifactVersion], set[ArtifactVersion]]:
        """Build artifact versions from gradle settings."""
        artifact_versions: set[ArtifactVersion] = set()
        sonatype_artifact_versions: set[ArtifactVersion] = set()
        properties: dict = {}
        properties.update(self.configs.get(resolve_filename("./gradle.properties")) or {})
        release_properties = {}
        release_properties.update(properties)
        release_properties.update(self.configs.get(resolve_filename("./release.properties")) or {})
        base_artifact_id = self.get_artifact_id(properties, release_properties)
        base_group = self.get_group(properties, release_properties)
        if base_group and base_artifact_id:
            validate_not_none(base_artifact_id)
            artifact_versions.update(
                self.get_artifact_versions_from_properties(base_group, base_artifact_id, properties, release_properties)
            )
            sonatype_artifact_versions.update(self.get_artifact_versions_from_sonatype(base_group, base_artifact_id))

        if gradle_settings:
            includes = gradle_settings.includes or []
        else:
            includes = []
        if includes:
            for include in includes:
                include_properties = {}
                include_properties.update(properties)
                include_properties.update(self.configs.get(resolve_filename(f"./{include}/gradle.properties")) or {})
                include_release_properties = {}
                include_release_properties.update(include_properties)
                include_release_properties.update(release_properties)
                include_release_properties.update(
                    self.configs.get(resolve_filename(f"./{include}/release.properties")) or {}
                )

                include_group = self.get_group(include_properties, include_release_properties)
                include_artifact_id = self.get_artifact_id(include_properties, include_release_properties)
                if include_group and include_artifact_id:
                    sonatype_artifact_versions.update(
                        self.get_artifact_versions_from_sonatype(include_group, include_artifact_id)
                    )

                    artifact_versions.update(
                        self.get_artifact_versions_from_properties(
                            include_group,
                            include_artifact_id,
                            include_properties,
                            include_release_properties,
                        )
                    )
        return sonatype_artifact_versions, artifact_versions

    def get_artifact_version_extensions(self, artifact_version: ArtifactVersion) -> set[ArtifactVersionExtension]:
        """Find all the artifact version's extensions."""
        artifact_version_extensions: set[ArtifactVersionExtension] = set()
        metadata_sha_response = self.get(artifact_version.metadata_sha_url)
        if metadata_sha_response is not None and metadata_sha_response.status_code == OK_RESPONSE_CODE:
            artifact_version_response = self.get(artifact_version.metadata_url)
            if artifact_version_response is not None and artifact_version_response.status_code == OK_RESPONSE_CODE:
                artifact_version_content = artifact_version_response.content
                try:
                    tree = ElementTree.fromstring(artifact_version_content)
                except Exception:
                    logger.warning("Could not load %s", artifact_version.metadata_url)
                    return artifact_version_extensions
                versioning = tree.find("versioning")
                if versioning is None or not len(versioning):
                    return artifact_version_extensions

                snapshot_versions = versioning.find("snapshotVersions")
                if snapshot_versions is not None and len(snapshot_versions):
                    for snapshot_version in snapshot_versions.iter("snapshotVersion"):
                        extension = snapshot_version.find("extension").text
                        if self.args.thorough or self.args.extension is None or self.args.extension == extension:
                            self.expected_extensions.add(extension)
                            sub_version = snapshot_version.find("value").text
                            artifact_sub_version = ArtifactVersionExtension(artifact_version, extension, sub_version)
                            artifact_version_extensions.add(artifact_sub_version)
        return artifact_version_extensions

    def get_artifact_versions_from_properties(
        self, group: Group, artifact_id: str, properties: dict, release_properties: dict
    ) -> list[ArtifactVersion]:
        """Find artifact versions in properties dictionaries."""
        with logging_context(group_id=group.group_id, artifact_id=artifact_id):
            if not group or not artifact_id:
                logger.debug(f"Skipping {group=} {artifact_id=}")
                return []

            artifact_versions = []
            for version in self.get_versions(properties, release_properties):
                if version is None:
                    continue
                guessed_repo = guess_repo(version)
                forced_repo = self.args.nexus_repo
                if forced_repo is None or forced_repo == guessed_repo:
                    repo = self.repos.get(guessed_repo)
                else:
                    repo = self.repos.get(forced_repo)
                if repo is not None:
                    repo_artifact = RepoArtifact(repo, group, artifact_id)
                    artifact_version = ArtifactVersion(repo_artifact, version)
                    artifact_versions.append(artifact_version)
                else:
                    logger.warning(f"Could not determine repo from {guessed_repo=} {forced_repo=}")
            return artifact_versions

    def get_artifact_versions_from_poms(self) -> set[ArtifactVersion]:
        """Find artifact versions in available poms."""
        artifact_versions: set[ArtifactVersion] = set()
        for config in self.poms.values():
            artifact_id = text_of(config, "pom:artifactId")
            version = text_of(config, "pom:version")
            group_id = text_of(config, "pom:groupId")

            parent_tag = config.find("pom:parent", POM_NAMESPACE)
            if parent_tag is not None:
                if version is None:
                    version = text_of(parent_tag, "pom:version")
                if group_id is None:
                    group_id = text_of(parent_tag, "pom:groupId")
            if group_id is not None and version is not None and artifact_id is not None:
                guessed_repo = guess_repo(version)
                forced_repo = self.args.nexus_repo
                if forced_repo is None or forced_repo == guessed_repo:
                    repo = self.repos.get(guessed_repo)
                else:
                    repo = self.repos.get(forced_repo)
                if repo is not None:
                    repo_artifact = RepoArtifact(repo, Group(group_id), artifact_id)
                    artifact_version = ArtifactVersion(repo_artifact, version)
                    artifact_versions.add(artifact_version)
                else:
                    logger.warning(f"Could not determine repo from {guessed_repo=} {forced_repo=}")
        return artifact_versions

    def get_artifact_id(self, properties: dict, release_properties: dict) -> str | None:
        """Find an artifact_id id from the command line or properties dictionaries."""
        args_artifact_id: str | None = self.args.artifact
        properties_artifact_id: str | None = properties.get("artifactId")
        release_properties_artifact_id: str | None = release_properties.get("artifactId")
        for artifact_id in (
            args_artifact_id,
            properties_artifact_id,
            release_properties_artifact_id,
        ):
            if artifact_id is not None:
                return artifact_id
        logger.debug("Did not find artifact_id")
        return None

    def get_group(self, properties: dict, release_properties: dict) -> Group | None:
        """Find a group id from the command line or properties dictionaries."""
        group_id = self.args.group_id
        if group_id is not None:
            return Group(group_id)
        for p in properties, release_properties:
            group_id = p.get("groupId")
            if group_id is not None:
                return Group(group_id)
        logger.debug("Did not find group")
        return None

    def get_versions(self, properties: dict, release_properties: dict) -> list[str]:
        """Look in properties files for version strings."""
        args_version = self.args.version
        if args_version is not None:
            return [args_version]
        properties_version = properties.get("version")
        release_version = release_properties.get("version")
        versions = {x for x in [properties_version, release_version] if x is not None}
        if not versions and not self.args.thorough:
            raise ValueError(
                "Could not find versions in properties files (try --thorough or specifying a version directly"
            )
        return list(versions)

    def get_artifact_versions_from_sonatype(self, group: Group, artifact_id: str) -> set[ArtifactVersion]:
        """Fetch data from sonatype and turn it into ArtifactVersions."""
        validate_not_none(artifact_id)
        with logging_context(artifact_id=artifact_id, **group.context()):
            if self.args.nexus_repo:
                nexus_repos = [self.args.nexus_repo]
            else:
                nexus_repos = list(REPOS.keys())
            artifact_versions: set[ArtifactVersion] = set()
            for nexus_repo in nexus_repos:
                repo = self.repos[nexus_repo]
                with logging_context(**repo.context()):
                    repo_artifact = RepoArtifact(repo, group, artifact_id)
                    sha_response = self.get(repo_artifact.metadata_sha_url)
                    if sha_response is None or sha_response.status_code != OK_RESPONSE_CODE:
                        logger.debug("No metadata sha available.")
                        continue

                    artifact_response = self.get(repo_artifact.metadata_url)
                    if artifact_response is not None and artifact_response.status_code < ERROR_RESPONSE_CODE_THRESHOLD:
                        response_artifact_versions = get_artifact_versions_from_repo_artifact_metadata(
                            repo_artifact, artifact_response.content
                        )
                        artifact_versions.update(response_artifact_versions)
        return artifact_versions


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
