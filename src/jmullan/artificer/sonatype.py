"""Yell at sonatype sometimes."""

import abc
import ast
import dataclasses
import inspect
import json
import logging
import numbers
import os
import pathlib
import re
import types
import typing
from collections.abc import Generator
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

logger = logging.getLogger(__name__)


T = typing.TypeVar("T")

POM_NAMESPACE = {"pom": "http://maven.apache.org/POM/4.0.0"}


class UniqueByUrl(abc.ABC):
    """Items whose uniqueness can be determined based on their URL."""

    @property
    @abc.abstractmethod
    def url(self) -> str:
        """Give the unique url for this item."""

    def __eq__(self, other: object) -> bool:
        """Check if these two things are the same based on their urls."""
        if other is None:
            return False
        if hasattr(other, "url"):
            return bool(self.url == other.url)
        return False

    def __hash__(self):
        """Make the hash depend on the url's value."""
        return hash(self.url)


def _get_caller_source(frame: types.FrameType | None) -> str | None:
    if frame is None:
        return None
    parent_frame = frame.f_back
    if parent_frame is not None:
        frame = parent_frame
    frame_info = inspect.getframeinfo(frame)
    if frame_info is None:
        return None
    code_context = frame_info.code_context
    if code_context is None or not code_context:
        return None
    src = code_context[0].removeprefix("validate_not_none(").removesuffix(")")
    if src.startswith("self."):
        src = src.removeprefix("self.")
        if "self" in frame.f_locals:
            old_self = frame.f_locals["self"]
            if old_self and hasattr(old_self, "__class__"):
                class_name = old_self.__class__.__name__
                src = f"{class_name}.{src}"
    return src


def validate_not_none(value: Any | None) -> None:  # noqa: ANN401
    """Almost the same thing as `assert thing is not None` but free of linter complaints."""
    if value is None:
        src = _get_caller_source(inspect.currentframe())
        if src is None:
            raise ValueError("Got unexpected None")
        message = f"{src} must not be None"
        raise ValueError(message)


@dataclasses.dataclass(frozen=True)
class Repo(UniqueByUrl):
    """Basically, public, release, or snapshot."""

    sonatype_path: str
    subpath: str
    name: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.sonatype_path)
        validate_not_none(self.subpath)
        validate_not_none(self.name)

    @property
    def url(self) -> str:
        """Get the folder url for this artifact, ignoring version."""
        return f"{self.sonatype_path}/{self.subpath}"

    def context(self) -> dict[str, typing.Any]:
        """Build a logging context from this repo."""
        return {"nexus_repo": self.name}


@dataclasses.dataclass(frozen=True)
class Group:
    """A group, with a group id in it so that group id can be manipulated."""

    group_id: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(lambda: self.group_id is not None)

    @property
    def group_path(self) -> str:
        """Get the path for the group by replacing dots with slashes."""
        return self.group_id.replace(".", "/")

    def context(self) -> dict[str, typing.Any]:
        """Build a logging context from group id."""
        return {"group_id": self.group_id}


@dataclasses.dataclass(frozen=True)
class RepoArtifact(UniqueByUrl):
    """A repo, group, and artifact id together."""

    nexus_repo: Repo
    group: Group
    artifact_id: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.nexus_repo)
        validate_not_none(self.group)
        validate_not_none(self.artifact_id)

    @property
    def url(self) -> str:
        """Get the folder url for this artifact, ignoring version."""
        return f"{self.nexus_repo.url}/{self.group.group_path}/{self.artifact_id}"

    @property
    def metadata_url(self) -> str:
        """Get the url for the metadata that describes this artifact, ignoring version."""
        return f"{self.url}/maven-metadata.xml"

    @property
    def metadata_sha_url(self) -> str:
        """Get the sha url for the metadata that describes this artifact, ignoring version."""
        return f"{self.metadata_url}.sha1"

    def context(self) -> dict[str, typing.Any]:
        """Build a logging context from this repo and artifact."""
        context = self.nexus_repo.context()
        context.update(self.group.context())
        context["artifact_id"] = self.artifact_id
        return context


@dataclasses.dataclass(frozen=True)
class ArtifactVersion(UniqueByUrl):
    """An artifact and version, which may have multiple extensions and subversions."""

    repo_artifact: RepoArtifact
    version: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.repo_artifact)
        validate_not_none(self.version)

    @property
    def url(self) -> str:
        """Get the folder url for this artifact."""
        return f"{self.repo_artifact.url}/{self.version}"

    @property
    def metadata_url(self) -> str:
        """Get the url for the metadata xml for this artifact."""
        return f"{self.url}/maven-metadata.xml"

    @property
    def metadata_sha_url(self) -> str:
        """Get the sha url that describes this metadata xml for this artifact."""
        return f"{self.metadata_url}.sha1"

    @property
    def artifact_id(self) -> str:
        """Get the artifact id from this artifact version."""
        return self.repo_artifact.artifact_id

    def context(self) -> dict[str, typing.Any]:
        """Build a logging context from this artifact version."""
        context = self.repo_artifact.context()
        context["version"] = self.version
        return context


@dataclasses.dataclass(frozen=True)
class ArtifactVersionExtension(UniqueByUrl):
    """Combines an artifact version with an extension and a subversion."""

    artifact_version: ArtifactVersion
    extension: str  # pom, jar, xml
    sub_version: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.artifact_version)
        validate_not_none(self.extension)
        validate_not_none(self.sub_version)

    @property
    def url(self) -> str:
        """Get the url to download this artifact at this version in particular."""
        return f"{self.artifact_version.url}/{self.artifact_id}-{self.sub_version}.{self.extension}"

    @property
    def sha_url(self) -> str:
        """Get the sha url that describes this artifact at this version."""
        return f"{self.url}.sha1"

    @property
    def artifact_id(self) -> str:
        """Get the artifact id from the artifact version."""
        return self.artifact_version.artifact_id

    def context(self) -> dict[str, typing.Any]:
        """Build a logging context from this extended artifact version."""
        context = self.artifact_version.context()
        context["extension"] = self.sub_version
        context["sub_version"] = self.sub_version
        return context


@dataclasses.dataclass
class GradleSettings:
    """Values extracted from gradle.settings."""

    root_project_name: str | None
    includes: list[str]

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.root_project_name)
        validate_not_none(self.includes)


def find_poms() -> Generator[Path, Any]:
    """Find any pom.xml files anywhere in the file tree."""
    for root, _, files in os.walk("./", topdown=True):
        for filename in files:
            if filename in ["pom.xml"]:
                yield pathlib.Path(root, filename)


def find_properties() -> Generator[Path, Any]:
    """Find any properties files anywhere in the file tree."""
    for root, _, files in os.walk("./", topdown=True):
        for filename in files:
            if filename in ["gradle.properties", "release.properties"]:
                yield pathlib.Path(root, filename)


def guess_repo(version: str) -> str:
    """Guess the repo path based on the version.

    Essentially looks for SNAPSHOT.
    """
    if version is None:
        raise ValueError("Cannot guess repo for none version")
    if "SNAPSHOT" in version.upper():
        return "snapshots"
    return "releases"


def root_project_name_from_property_line(line: str | None) -> str | None:
    """Find a project name in a property line if at all possible."""
    if line is None or not line.startswith("rootProject.name"):
        return None
    split = line.split("=", 1)
    if len(split) > 1:
        return load_json_or_python(split[1])
    return None


def read_includes_from_include_line(line: str) -> list[str]:
    """Find any included subprojects if possible."""
    # include 'signon-service-dto', 'signon-service'
    includes = []
    parts = line.split(" ", 1)
    if len(parts) > 1:
        value: typing.Any = parts[1:][0]
        maybe = load_json_or_python(value)
        value_list = force_a_list(maybe)
        value = [x.strip(":") for x in value_list]
        includes.extend(value)
    return includes


def force_a_list(value: typing.Any) -> list[typing.Any]:  # noqa: ANN401 PLR0911
    """Change practically any value into a list."""
    match value:
        case list(x):
            return x
        case tuple(x):
            return list(x)
        case set(x):
            return list(x)
        case str(x):
            return [x]
        case numbers.Number() as x:
            return [x]
        case bytes(x):
            return [x.decode("utf-8")]
        case bool(x):
            return [x]
        case None:
            return []
        case dict(_):
            raise TypeError("Cannot turn a dict into a list")
        case _:
            raise TypeError("Cannot turn a value of an unknown type into a list")


def load_json_or_python(value: str | None) -> typing.Any | None:  # noqa: ANN401
    """Make a string in a string into a not-string."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(value)
    except ValueError:
        pass
    return value


def parse_gradle_settings(data: str) -> GradleSettings | None:
    """Read Gradle settings from string."""
    root_project_name: str | None = None
    includes = []
    if data is None:
        return None
    lines = data.split("\n")
    for line in lines:
        stripped = re.sub("#.*", "", line).strip()
        if len(stripped) == 0:
            continue

        maybe_root_project_name = root_project_name_from_property_line(stripped)
        if maybe_root_project_name is not None:
            root_project_name = maybe_root_project_name
            continue
        if stripped.startswith("include "):
            includes.extend(read_includes_from_include_line(stripped))

    return GradleSettings(root_project_name, includes)


def read_gradle_settings() -> GradleSettings | None:
    """Read Gradle settings from file."""
    file_path = pathlib.Path("./settings.gradle")
    if file_path.exists():
        with file_path.open(encoding="utf-8") as f:
            return parse_gradle_settings(f.read())
    return None


def text_of(element: Element, path: str) -> str | None:
    """Extract text from an Element if present."""
    maybe = element.find(path, POM_NAMESPACE)
    if maybe is not None and maybe.text is not None:
        return maybe.text.strip()
    return None


def get_artifact_versions_from_repo_artifact_metadata(
    repo_artifact: RepoArtifact, artifact_content: bytes | None
) -> set[ArtifactVersion]:
    """Extract artifact versions from an artifact metadata xml."""
    validate_not_none(repo_artifact)
    artifact_versions: set[ArtifactVersion] = set()

    if artifact_content is None:
        return artifact_versions

    try:
        tree = ElementTree.fromstring(artifact_content)
    except Exception:
        logger.exception("Could not parse xml")
        return artifact_versions
    versioning = tree.find("versioning")
    version = versioning.find("latest").text.strip()
    artifact_version = ArtifactVersion(repo_artifact, version)
    artifact_versions.add(artifact_version)
    for version_tag in versioning.iter("version"):
        version_tag_value = version_tag.text.strip()
        if version_tag_value and version_tag != version:
            artifact_version = ArtifactVersion(repo_artifact, version_tag_value)
            artifact_versions.add(artifact_version)
    versions = versioning.find("versions")
    if versions is not None:
        for version_tag in versions.iter("version"):
            if version_tag is not None:
                version_tag_value = version_tag.text.strip()
                if version_tag_value and version_tag != version:
                    artifact_version = ArtifactVersion(repo_artifact, version_tag_value)
                    artifact_versions.add(artifact_version)

    return artifact_versions
