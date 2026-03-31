"""Look in harbor registry for current docker image versions."""

import argparse
import dataclasses
import logging
import os.path
import re
import shutil
import textwrap
from argparse import ArgumentError, ArgumentParser, Namespace
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import requests

from jmullan.cmd import cmd
from jmullan.logging import easy_logging

from jmullan.artificer.artificer import validate_is_dict, validate_not_none

logger = logging.getLogger(__name__)


DEBIAN_VERSION_PREFIXES = {
    "duke": "15.",
    "forky": "14.",
    "trixie": "13.",
    "bookworm": "12.",
    "bullseye": "11.",
    "buster": "10.",
    "stretch": "9.",
    "jessie": "8.",
    "wheezy": "7.",
    "squeeze": "6.",
    "lenny": "5.",
    "etch": "4.",
    "sarge": "3.1.",
    "woody": "3.0.",
    "potato": "2.2..",
    "slink": "2.1.",
    "hamm": "2.0.",
    "bo": "1.3.",
    "rex": "1.2.",
    "buzz": "1.1.",
}


def get_terminal_width() -> int:
    """Return the width of the terminal in characters."""
    try:
        size = shutil.get_terminal_size()
    except OSError:
        return 80  # Default to 80 columns if size cannot be determined
    return size.columns


def get_debian_version_name(version_string: str | None) -> str | None:
    """Guess the debian version name from a version string."""
    if version_string is None:
        return None
    for name, prefix in DEBIAN_VERSION_PREFIXES.items():
        if name.lower() == version_string or version_string.startswith(prefix):
            return name.capitalize()
    return None


@dataclasses.dataclass
class Project:
    """Information about a project in harbor.

    "creation_time": "2022-03-10T06:11:20.158Z",
    "current_user_role_ids": null,
    "cve_allowlist": {
      "creation_time": "0001-01-01T00:00:00.000Z",
      "items": null,
      "update_time": "0001-01-01T00:00:00.000Z"
    },
    "metadata": {
      "auto_scan": "true",
      "enable_content_trust": "false",
      "prevent_vul": "false",
      "public": "true",
      "retention_id": "167",
      "reuse_sys_cve_allowlist": "true",
      "severity": "high"
    },
    "name": "avejandla-sb-thanos",
    "owner_id": 1,
    "project_id": 286,
    "repo_count": 1,
    "update_time": "2022-03-10T06:11:20.158Z"
    """

    current_user_role_ids: Any | None
    cve_allowlist: Any
    metadata: dict[str, str]
    name: str
    owner_id: int
    project_id: int
    repo_count: int
    creation_time: datetime
    update_time: datetime


@dataclasses.dataclass
class Repository:
    """Information about a particular docker image's repository.

    "artifact_count": 1,
      "project_id": 1234,
      "project_name": "promviz-api-backend",
      "project_public": true,
      "pull_count": 1,
      "repository_name": "promviz-api-backend/sb-connect"
    """

    project_id: int
    project_name: str
    project_public: bool
    repository_name: str
    artifact_count: int = 0
    pull_count: int = 0

    @property
    def short_repo_name(self) -> str:
        """Get just the end of the repo name."""
        return self.repository_name.split("/")[1]


@dataclasses.dataclass
class Artifact:
    """A tagged and labelled docker image."""

    base_url: str
    project: str
    repository: str
    data: dict

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.base_url)
        validate_not_none(self.project)
        validate_not_none(self.repository)
        validate_not_none(self.data)
        validate_is_dict(self.data)
        validate_not_none(self.data.get("id"))

    @property
    def artifact_id(self) -> str:
        """Find the id of the artifact."""
        return str(self.data["id"])

    @property
    def date(self) -> str:
        """Find the best-guess date for the artifact."""
        build_date = self.label_schemas.get("build-date")
        try:
            if build_date is not None:
                build_datetime = datetime.strptime(build_date, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
                return (
                    build_datetime.isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")  # just Z please
                    .replace(".000Z", "Z")  # trim off no decimal seconds
                    .replace("T00:00:00Z", "Z")  # trim off midnight
                    .replace(":00Z", "Z")
                )  # or trim off no seconds

        except Exception:  # noqa: S110
            pass
        return self.data.get("push_time", self.data.get("pull_time")) or ""

    @property
    def tags(self) -> list[str]:
        """Find the tags attached to the artifact."""
        return [tag["name"] for tag in (self.data.get("tags") or [])]

    @property
    def label_schemas(self) -> dict[str, str]:
        """Find the label schemas attached to the artifact."""
        extra_attrs = self.data.get("extra_attrs") or {}
        config = extra_attrs.get("config") or {}
        labels: dict[str, str] = config.get("Labels") or {}
        return {k.removeprefix("org.label-schema."): v for k, v in labels.items() if k.startswith("org.label-schema.")}

    @property
    def version(self) -> str | None:
        """Get the official version (if any) for this artifact."""
        return self.label_schemas.get("version")

    @property
    def urls(self) -> set[str]:
        """Get all the urls that could be used to retrieve this artifact."""
        # harbor-registry.example.com/project_name/repository_name:3.6.5-stretch-xy0.0.5
        return {f"{self.base_url}/{self.project}/{self.repository}:{tag}" for tag in self.tags}


@dataclasses.dataclass()
class Matcher:
    """Criteria for filtering artifacts.

    (Who matches the matchers?)
    """

    key: str
    is_regex: bool
    value: str

    def __post_init__(self):
        """Validate property values."""
        validate_not_none(self.key)
        validate_not_none(self.is_regex)
        validate_not_none(self.value)

    def matches(self, value: str) -> bool:
        """Determine if the value matches this matcher."""
        if self.value.startswith("(") and self.value.endswith(")"):
            values = self.value.removeprefix("(").removesuffix(")").split(" ")
            if not self.is_regex:
                return value in values
            regexes = [re.compile(x) for x in values]
            return any(r.search(value) for r in regexes)
        if self.value.startswith("[") and self.value.endswith("]") and "-" in self.value:
            values = self.value.removeprefix("[").removesuffix("]").split("-", 1)
            first = int(values[0])
            second = int(values[1])
            int_value = int(value)
            return int_value >= min(first, second) and int_value < max(first, second)
        if not self.is_regex:
            return value == self.value
        return bool(re.compile(self.value).search(value))


def get_matchers(labels: list[str | None] | None) -> list[Matcher]:
    """Determine what matchers have been requested from the command line."""
    if labels is None or not labels:
        return []
    matchers = []
    for label in labels:
        if label is None or not len(label) or "=" not in label:
            continue
        key, value = label.split("=", 1)
        if len(value) and value.startswith("~"):
            value = value.removeprefix("~")
            fuzzy = True
        else:
            fuzzy = False
        matchers.append(Matcher(key, fuzzy, value))
    return matchers


def all_match(matchers: list[Matcher], label_schemas: dict[str, str]) -> bool:
    """Check that all matchers match the label schemes, or that there are no matchers provided."""
    if not matchers:
        return True
    if not label_schemas:
        return False
    for matcher in matchers:
        if matcher.key not in label_schemas:
            return False
        value = label_schemas[matcher.key]
        if not matcher.matches(value):
            return False
    return True


class UrlAction(argparse.Action):
    """Validates and processes URL actions on the command line."""

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,  # noqa: ARG002
    ) -> None:
        """Validate and process URL actions on the command line."""
        if values is None:
            parser.error("Please enter a valid url.")
        if isinstance(values, Sequence):
            parser.error("Please enter a valid url.")

        values = values.lstrip()
        if not len(values):
            parser.error("Please enter a valid url.")
        if not values.startswith("http://") and not values.startswith("https://"):
            parser.error("Your url must start with http:// or https://")
        setattr(namespace, self.dest, values)


query_help = """Supported query patterns are:
key=value: exact match
key=~value: fuzzy match
k=[min~max]: range match
k={v1 v2 v3}: match all? of the values (union)
k=(v1 v2 v3): match any? of the values (intersection)

The value of range and list can be string(enclosed by " or '), integer or time (in format "2020-04-09 02:36:00").
"""

label_help = """org.label-schema.[KEY][OPERATOR][VALUE]
Supported query patterns are:
key=value: exact match
key=~regex: regex match
k=[min~max]: range match
k=(v1 v2 v3): match any of the values

The value of range and list can be string(enclosed by " or '), integer or time (in format "2020-04-09 02:36:00").
"""


def print_artifact_url(url: str, artifact: Artifact, *, show_artifact_details: bool = False) -> None:
    """Print the url and debian information from the artifact.

    Note: an artifact can have many urls.
    """
    debian_version = artifact.label_schemas.get("debian-version")
    debian_version_name = get_debian_version_name(debian_version)

    parts = [f"{url}", f"{artifact.date}", "Debian"]
    if debian_version is not None:
        parts.append(f"{debian_version}")
    if debian_version_name is not None:
        parts.append(f"({debian_version_name})")
    # put the pieces together and remove Debian if no version info is available
    full_url = " ".join(parts).removesuffix("Debian")
    print(f"  {full_url}")
    if show_artifact_details:
        tags = artifact.tags
        if tags:
            print(
                textwrap.fill(
                    ", ".join(tags), get_terminal_width(), initial_indent="    Tags: ", subsequent_indent="      "
                )
            )
        labels = artifact.label_schemas
        if labels:
            kvs = [f"{k}={v}" for k, v in artifact.label_schemas.items()]
            print(
                textwrap.fill(
                    "\n".join(kvs), get_terminal_width(), initial_indent="    Labels: ", subsequent_indent="      "
                )
            )


def build_artifacts_by_version(artifacts_by_id: dict[str, Artifact]) -> dict[str, Artifact]:
    """Extract version information from artifacts, and try to find the best artifact for each version."""
    artifacts_by_version: dict[str, Artifact] = {}
    for artifact in artifacts_by_id.values():
        if artifact.version is not None:
            for artifact_url in artifact.urls:
                if artifact_url.endswith(f":{artifact.version}"):
                    # if we find the canonical version as a tag, use
                    # this artifact for the version
                    artifacts_by_version[artifact.version] = artifact
    return artifacts_by_version


def print_artifact_urls(
    matchers: list[Matcher], artifacts_by_id: dict[str, Artifact], *, show_artifact_details: bool = False
) -> None:
    """Output urls for artifacts that match the filters."""
    artifacts_by_version = build_artifacts_by_version(artifacts_by_id)
    for artifact in sorted(artifacts_by_id.values(), key=lambda a: a.date):
        if matchers and not all_match(matchers, artifact.label_schemas):
            continue

        if artifact.version is not None and artifact.version in artifacts_by_version:
            # we have a canonical artifact for this version
            if artifact == artifacts_by_version[artifact.version]:
                # if this is that canonical artifact, use it, otherwise ignore it
                for artifact_url in artifact.urls:
                    if artifact_url.endswith(f":{artifact.version}"):
                        # only print the canonical version's canonical url
                        print_artifact_url(artifact_url, artifact, show_artifact_details=show_artifact_details)
        else:
            # there is no canonical artifact, so just print all the urls
            for artifact_url in artifact.urls:
                print_artifact_url(artifact_url, artifact, show_artifact_details=show_artifact_details)


class Main(cmd.Main):
    """Check a harbor registry for docker images."""

    def __init__(self):
        super().__init__()
        self.url_action = self.parser.add_argument(
            "--url",
            dest="url",
            default=os.environ.get("HARBOR_REGISTRY_URL"),
            action=UrlAction,
            help="What base url to use to look for docker images in harbor",
        )
        self.project_action = self.parser.add_argument(
            "--project",
            dest="project",
            required=False,
            help="What project to use when searching for images",
        )
        self.repository_action = self.parser.add_argument(
            "--repository",
            dest="repository",
            required=False,
            help="What repository to use when searching for images",
        )
        self.queries_action = self.parser.add_argument("--query", dest="queries", action="append", help=query_help)
        self.labels_action = self.parser.add_argument("--label", dest="labels", action="append", help=label_help)
        self.limit_action = self.parser.add_argument(
            "--limit", dest="limit", required=False, default=None, help="only show this many results"
        )
        self.show_artifact_details = self.parser.add_argument(
            "--show-artifact-details",
            dest="show_artifact_details",
            action="store_true",
            required=False,
            default=False,
            help="Also print more artifact detail like tags and labels",
        )

    def setup(self) -> None:
        """Do something after parsing args but before main."""
        super().setup()
        if self.args.verbose:
            easy_logging.easy_initialize_logging("DEBUG")
        else:
            easy_logging.easy_initialize_logging()

    def main(self) -> None:
        """Look in harbor registry for docker images."""
        super().main()
        if not self.args.url:
            raise ArgumentError(self.url_action, "Missing harbor url")

        if self.args.project and self.args.repository:
            self.find_artifacts()
        else:
            self.search()

    def find_artifacts(self) -> None:
        """Look for artifacts in various places."""
        if not self.args.project:
            raise ArgumentError(self.project_action, "Missing project")
        if not self.args.repository:
            raise ArgumentError(self.repository_action, "Missing repository")

        logger.info(f"{self.args.project} {self.args.repository}")
        artifacts_by_id = self.get_all_artifacts_by_id()

        matchers = get_matchers(self.args.labels)
        print_artifact_urls(matchers, artifacts_by_id, show_artifact_details=self.args.show_artifact_details)

    def get_all_artifacts_by_id(self) -> dict[str, Artifact]:
        """Request all pages from registry and organize them by id."""
        page = 0
        page_size = 10
        artifacts_by_id: dict[str, Artifact] = {}
        max_page = 1000
        while page < max_page:
            try:
                artifacts_page = self.request_page(page, page_size)
            except Exception:
                logger.exception("Error requesting page")
                raise
            if not artifacts_page:
                break
            artifacts_by_id.update({a.artifact_id: a for a in artifacts_page})
            page += 1
        return artifacts_by_id

    def request_page(self, page: int, page_size: int) -> list[Artifact]:
        """Get one page of results."""
        url = f"{self.args.url}/api/v2.0/projects/{self.args.project}/repositories/{self.args.repository}/artifacts"
        params: dict[str, str] = {
            "page": str(page),
            "page_size": str(page_size),
            "with_tag": "true",
            "with_label": "true",
        }
        query_param = []
        if self.args.queries:
            for query in self.args.queries:
                if query is None:
                    continue
                stripped_query = query.lstrip()
                if len(stripped_query) == 0:
                    continue
                if "=" not in stripped_query:
                    raise ArgumentError(self.queries_action, f"Invalid query: {query}")
                query_param.append(stripped_query)
        if query_param:
            params["q"] = ",".join(query_param)
        response = requests.get(url, params=params, timeout=360)
        data = response.json()
        if isinstance(data, dict) and "errors" in data:
            message = f"Errors fetching from harbor: {data['errors']}"
            raise ValueError(message)
        return [Artifact(self.args.url, self.args.project, self.args.repository, a) for a in data]

    def search(self) -> None:
        """Look in the harbor registry for artifacts."""
        projects, repositories = self.query()
        projects_to_repositories: dict[str, list[Repository]] = {p.name: [] for p in projects or []}
        for repository in repositories:
            if repository.project_name not in projects_to_repositories:
                projects_to_repositories[repository.project_name] = []
            projects_to_repositories[repository.project_name].append(repository)
        if projects_to_repositories:
            print("Projects and Repositories")
            for project_name, project_repos in sorted(projects_to_repositories.items(), key=lambda e: (e[0], e[1])):
                print(f"  {project_name}")
                if not project_repos:
                    print("    No repos match.")
                else:
                    for repo in sorted(project_repos, key=lambda r: r.repository_name):
                        print(f"    {repo.short_repo_name}")

    def query(self) -> tuple[list[Project], list[Repository]]:
        """Search for a string."""
        # https://harbor-registry.savagebeast.com/api/v2.0/search?q=pushgateway
        url = f"{self.args.url}/api/v2.0/search"
        params: dict[str, str] = {}
        query_param = []
        if self.args.queries:
            for query in self.args.queries:
                if query is None:
                    continue
                stripped_query = query.lstrip()
                if len(stripped_query) == 0:
                    continue
                query_param.append(stripped_query)
        if query_param:
            params["q"] = ",".join(query_param)
        if not params:
            raise ValueError("No valid queries found")
        response = requests.get(url, params=params, timeout=360)
        data = response.json()
        if isinstance(data, dict) and "errors" in data:
            message = f"Errors fetching from harbor: {data['errors']}"
            raise ValueError(message)
        project_data = data.get("project") or []
        repository_data = data.get("repository") or []
        return [Project(**p) for p in project_data], [Repository(**r) for r in repository_data]


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
