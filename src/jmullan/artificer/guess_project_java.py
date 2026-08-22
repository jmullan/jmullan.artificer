"""Look in various files to guess the desired python version."""

import logging
import pathlib
import re
import sys
import typing
from collections import defaultdict
from typing import Any

import yaml
from lxml import etree
from packaging.specifiers import Specifier, SpecifierSet

from jmullan.cmd import cmd
from jmullan.logging import easy_logging, formatters

from jmullan.artificer import file_utils, java_version, utils

logger = logging.getLogger(__name__)


def xml_vars(filename: str | pathlib.Path, namespace: dict[str, str], selector: str) -> list[typing.Any]:
    """Load a TOML file and find a variable in that file."""
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return xml_vars(pathlib.Path(filename), namespace, selector)
    if not filename.exists():
        message = f"{filename} does not exist"
        raise FileNotFoundError(message)
    return list(etree.parse(filename).xpath(selector, namespaces=namespace))


def find_xml_versions(
    path: pathlib.Path, namespace: dict[str, str], selector: str, *, strict: bool = False
) -> list[utils.FoundVersion] | None:
    """Extract a version from a TOML file."""
    values = xml_vars(path, namespace, selector)
    if values is None:
        return []
    if not len(values):
        return []

    found_versions = []
    for value in values:
        specifier = java_version.parse_java_specifier(value, strict=strict)
        if specifier:
            found_versions.append(utils.FoundVersion(path, selector, value, specifier))
    return found_versions


def find_yaml_version(path: pathlib.Path, selector: str) -> utils.FoundVersion | None:
    """Extract a version from a YAML file."""
    value = utils.yaml_var(path, selector)
    if value is None:
        return None
    specifier = java_version.parse_java_specifier(value)
    if specifier:
        return utils.FoundVersion(path, selector, value, specifier)
    return None


def extract_java_versions(found_versions: list[utils.FoundVersion]) -> set[str]:
    """Guess what versions would match a set of specifications."""
    if found_versions is None:
        return set()
    versions: set[str] = set()

    for found_version in found_versions:
        for specifier in found_version.specifier_set:
            if isinstance(specifier, Specifier) and specifier.operator == "===":
                versions.add(specifier.version)

        matching_versions = java_version.get_matching_java_versions(found_version.specifier_set)
        if matching_versions is not None:
            versions.update(matching_versions)
    return versions


def find_pom_xml_files() -> set[pathlib.Path]:
    """Look for Dockerfiles recursively.

    This can be expensive!
    """
    dot_git = file_utils.find_up(".git")
    if dot_git is not None and dot_git.is_dir():
        in_dir = dot_git.parent
    else:
        in_dir = pathlib.Path.cwd()
    ignored_files = file_utils.find_ignored_files(in_dir)
    if ignored_files is None:
        # we are not in a git-controlled dir, so limit depth
        git_ignore_spec = file_utils.load_global_gitignore()
        pom_files = file_utils.rglob(in_dir, "pom.xml", 4, git_ignore_spec=git_ignore_spec, limit=10)
        pom_files = [p for p in pom_files if p.is_file()]
    else:
        pom_files = [p for p in file_utils.rglob(in_dir, "pom.xml") if p.is_file()]
    found = {p.resolve() for p in pom_files}
    if ignored_files is not None:
        ignored = {p.resolve() for p in ignored_files}
        return found - ignored
    return found


def find_maven_versions(*, strict: bool = False) -> list[utils.FoundVersion]:
    """Look for versions in pyproject versions."""
    logger.debug("Finding maven versions...")
    found_versions: list[utils.FoundVersion] = []
    pom_paths = find_pom_xml_files()
    for pom_path in pom_paths:
        logger.debug(f"pom path {pom_path}")
        if pom_path is not None and pom_path.exists():
            namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
            # version ranging
            pyproject_selectors = [
                "//m:java.version/text()",
                "//m:maven.compiler.release/text()",
                "//m:maven.compiler.source/text()",
                "//m:maven.compiler.target/text()",
                "//m:plugin[m:artifactId='maven-compiler-plugin']/m:configuration/m:release/text()",
                "//m:plugin[m:artifactId='maven-compiler-plugin']/m:configuration/m:source/text()",
                "//m:plugin[m:artifactId='maven-compiler-plugin']/m:configuration/m:target/text()",
            ]
            for selector in pyproject_selectors:
                xml_versions = find_xml_versions(pom_path, namespace, selector, strict=strict)
                if xml_versions:
                    found_versions.extend(xml_versions)
    return found_versions


gradle_support = {
    java_version.JavaVersion.JAVA_8: SpecifierSet(">=2.0,<=8.15"),
    java_version.JavaVersion.JAVA_9: SpecifierSet(">=4.3,<=8.15"),
    java_version.JavaVersion.JAVA_10: SpecifierSet(">=4.7,<=8.15"),
    java_version.JavaVersion.JAVA_11: SpecifierSet(">=5.0,<=8.15"),
    java_version.JavaVersion.JAVA_12: SpecifierSet(">=5.4,<=8.15"),
    java_version.JavaVersion.JAVA_13: SpecifierSet(">=6.0,<=8.15"),
    java_version.JavaVersion.JAVA_14: SpecifierSet(">=6.3,<=8.15"),
    java_version.JavaVersion.JAVA_15: SpecifierSet(">=6.7,<=8.15"),
    java_version.JavaVersion.JAVA_16: SpecifierSet(">=7.0,<=8.15"),
    java_version.JavaVersion.JAVA_17: SpecifierSet(">=7.3"),
    java_version.JavaVersion.JAVA_18: SpecifierSet(">=7.5"),
    java_version.JavaVersion.JAVA_19: SpecifierSet(">=7.6"),
    java_version.JavaVersion.JAVA_20: SpecifierSet(">=8.3"),
    java_version.JavaVersion.JAVA_21: SpecifierSet(">=8.5"),
    java_version.JavaVersion.JAVA_22: SpecifierSet(">=8.8"),
    java_version.JavaVersion.JAVA_23: SpecifierSet(">=8.10"),
    java_version.JavaVersion.JAVA_24: SpecifierSet(">=8.14"),
    java_version.JavaVersion.JAVA_25: SpecifierSet(">=9.1.0"),
    java_version.JavaVersion.JAVA_26: SpecifierSet(">=9.4.0"),
}


def find_gradle_versions(*, strict: bool = False) -> list[utils.FoundVersion]:
    """Look for versions in Gradle files."""
    logger.debug("Finding gradle versions...")
    found_version: utils.FoundVersion | None
    found_versions: list[utils.FoundVersion] = []
    for build_gradle in file_utils.rglob_from_dir_containing(".git", "build.gradle"):
        logger.debug("Found build.gradle %s", build_gradle.file_path)
        for line in build_gradle.handle:
            specifier = extract_java_specifier_from_gradle_line(line, strict=strict)
            if specifier:
                found_version = utils.FoundVersion(build_gradle.file_path, "FROM", line.strip(), specifier)
                if found_version:
                    found_versions.append(found_version)
                continue
    for gradle_properties in file_utils.rglob_from_dir_containing(".git", "gradle/wrapper/gradle-wrapper.properties"):
        for line in gradle_properties.handle:
            found_version = get_gradle_line_found_version(line, gradle_properties)
            if found_version is not None:
                found_versions.append(found_version)
    return found_versions


def get_gradle_line_found_version(line: str, gradle_properties: file_utils.GlobbedFile) -> utils.FoundVersion | None:
    """Given a line of text in a Gradle file, return a found version if possible."""
    line = re.sub("#.*", "", line).strip()
    if not len(line) or "=" not in line:
        return None
    key, value = line.split("=", 1)
    if key != "distributionUrl":
        return None
    # https\://services.gradle.org/distributions/gradle-5.4.1-all.zip
    gradle_zip_file = pathlib.Path(value).name
    matches = re.search(r"gradle-(?P<version>[^-]+)-", gradle_zip_file)
    if matches:
        version = matches.group("version")
        min_java_version = None
        max_java_version = None
        for java_version_enum, gradle_specifier in gradle_support.items():
            if version in gradle_specifier:
                if min_java_version is None:
                    min_java_version = java_version_enum
                max_java_version = java_version_enum
        if min_java_version is not None:
            if max_java_version is None:
                max_java_version = java_version.JavaVersion.JAVA_26
            version_range = f">={min_java_version.major_minor},<={max_java_version.next_major_minor}"
            logger.debug("gradle wrapper version %s -> %s", version, version_range)
            gradle_based_specifier = SpecifierSet(version_range)
            return utils.FoundVersion(gradle_properties.file_path, ".", line.strip(), gradle_based_specifier)
    return None


gradle_line_matchers = [
    r"projectJavaVersion *= *[\"']?(?P<version>[.0-9]+)[\"']?",
    r"sourceCompatibility *= *(?P<version>JavaVersion.VERSION_([0-9_]+))",
    r"targetCompatibility *= *(?P<version>JavaVersion.VERSION_([0-9_]+))",
    r"JavaLanguageVersion.of\((?P<version>[0-9_]+)\)",
]


def extract_java_specifier_from_gradle_line(line: str, *, strict: bool = False) -> SpecifierSet | None:
    """Get a java version from a dockerfile line.

    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk8u462-b08"', strict=True
    ... )
    <SpecifierSet('==8.0.462+8')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     "JAVA_HOME = '/usr/lib/jvm/java-11-openjdk-amd64'", strict=True
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk-17.0.4.1+1/"', strict=True
    ... )
    <SpecifierSet('==17.0.4.1+1')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk8u462-b08"', strict=False
    ... )
    <SpecifierSet('~=8.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     "JAVA_HOME = '/usr/lib/jvm/java-11-openjdk-amd64'", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk-17.0.4.1+1/"', strict=False
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_jenkins_line("", strict=False)
    """
    line = line.strip()
    for matcher in gradle_line_matchers:
        matches = re.search(matcher, line)
        if matches:
            version = matches.group("version")
            return java_version.parse_java_specifier(version, strict=strict)
    return None


def find_runtime_txt_version() -> list[utils.FoundVersion]:
    """Look for python versions in .venv files."""
    found_versions: list[utils.FoundVersion] = []
    runtime_txt_version = file_utils.find_up("runtime.txt")
    if runtime_txt_version is not None and runtime_txt_version.is_file():
        logger.debug("Found runtime.txt")
        with runtime_txt_version.open("r") as handle:
            for line in handle:
                specifier = java_version.parse_java_specifier(line)
                if specifier:
                    found_version = utils.FoundVersion(runtime_txt_version, ".", line.strip(), specifier)
                    found_versions.append(found_version)
    return found_versions


def find_github_action_java_versions(*, strict: bool = False) -> list[utils.FoundVersion]:
    """Look for GitHub actions and then look for java versions in those files."""
    found_versions: list[utils.FoundVersion] = []
    for workflow_yaml in file_utils.rglob_from_dir_containing(".github", ".github/workflows/*.yml"):
        logger.debug("Found workflow_yaml %s", workflow_yaml.file_path)
        documents = yaml.safe_load_all(workflow_yaml.handle)
        for index, document in enumerate(documents):
            java_versions = []
            java_versions.extend(utils.rglob_var(document, "java_version"))
            java_versions.extend(utils.rglob_var(document, "java-version"))
            java_versions.extend(utils.rglob_var(document, "JAVA_VERSION"))
            for java_version_string in java_versions:
                specifier = java_version.parse_java_specifier(f"{java_version_string}", strict=strict)
                if specifier:
                    found_version = utils.FoundVersion(
                        workflow_yaml.file_path, f"Document {index}", java_version_string, specifier
                    )
                    found_versions.append(found_version)
    return found_versions


def find_jenkins_file_versions(*, strict: bool = False) -> list[utils.FoundVersion]:
    """Look for Jenkinsfiles and then look for java versions in those files."""
    found_versions: list[utils.FoundVersion] = []
    for jenkins_file in file_utils.rglob_from_dir_containing(".git", "Jenkinsfile*"):
        logger.debug("Found Jenkinsfile %s", jenkins_file.file_path)
        for line in jenkins_file.handle:
            specifier = extract_java_specifier_from_jenkins_line(line, strict=strict)
            if specifier is not None:
                found_version = utils.FoundVersion(jenkins_file.file_path, "", line.strip(), specifier)
                found_versions.append(found_version)
                continue
    return found_versions


jenkins_line_matchers = [
    r"java-?(?P<version>[.0-9]+)-openjdk",
    r"jdk-?(?P<version>[.0-9]+u[.0-9]+(-b[0-9]+)?)",
    r"jdk-?(?P<version>[.0-9]+(\+[-a-zA-Z0-9.]+)?)",
]


def extract_java_specifier_from_jenkins_line(line: str, *, strict: bool = False) -> SpecifierSet | None:
    """Get a java version from a dockerfile line.

    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk8u462-b08"', strict=True
    ... )
    <SpecifierSet('==8.0.462+8')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     "JAVA_HOME = '/usr/lib/jvm/java-11-openjdk-amd64'", strict=True
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk-17.0.4.1+1/"', strict=True
    ... )
    <SpecifierSet('==17.0.4.1+1')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk8u462-b08"', strict=False
    ... )
    <SpecifierSet('~=8.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     "JAVA_HOME = '/usr/lib/jvm/java-11-openjdk-amd64'", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_jenkins_line(
    ...     'JAVA_HOME = "/opt/openjdk/jdk-17.0.4.1+1/"', strict=False
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_jenkins_line("", strict=False)
    """
    line = line.strip()
    for matcher in jenkins_line_matchers:
        matches = re.search(matcher, line)
        if matches:
            version = matches.group("version")
            return java_version.parse_java_specifier(version, strict=strict)
    return None


def find_dockerfile_versions(*, strict: bool = False) -> list[utils.FoundVersion]:
    """Look for python versions in Dockerfiles."""
    found_versions: list[utils.FoundVersion] = []
    dot_git = file_utils.find_up(".git")
    if dot_git is not None and dot_git.is_dir():
        dockerfiles = utils.find_dockerfiles(dot_git.parent)
    else:
        dockerfiles = utils.find_dockerfiles(pathlib.Path.cwd())
    if dockerfiles:
        for dockerfile in dockerfiles:
            logger.debug("Found dockerfile %s", dockerfile)
            with dockerfile.open("r") as handle:
                for line in handle:
                    specifier = extract_java_specifier_from_docker_line(line, strict=strict)
                    if specifier:
                        found_version = utils.FoundVersion(dockerfile, "FROM", line.strip(), specifier)
                        found_versions.append(found_version)
                        continue
    return found_versions


def extract_java_specifier_from_docker_line(line: str, *, strict: bool = False) -> SpecifierSet | None:
    """Get a java version from a dockerfile line if possible.

    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/thing/jetty-foo:9.4.43-java17-buster-yy0.0.2",
    ...     strict=True,
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11.0-stretch-yy0.0.1", strict=True
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11.0.1-stretch-xx0.0.1", strict=True
    ... )
    <SpecifierSet('==11.0.1+stretch-xx0.0.1')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11-stretch-yy0.0.1", strict=True
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/thing/jetty-foo:9.4.43-java17-buster-yy0.0.2",
    ...     strict=False,
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11.0-stretch-yy0.0.1", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11.0.1-stretch-xx0.0.1", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_line(
    ...     "FROM registry.example.com/java-foo:11-stretch-yy0.0.1", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_line("", strict=False)
    >>> extract_java_specifier_from_docker_line("", strict=True)
    """
    line = line.strip()
    matches = re.match(r"^FROM +(?P<docker_base_image>.*)", line)
    if matches:
        version = matches.group("docker_base_image")
        specifier = extract_java_specifier_from_docker_base_image(version, strict=strict)
        if specifier:
            return specifier
    return None


build_sbt_docker_matchers = [
    r"amazoncorretto(?P<short_version>[0-9]+):(?P<version>.*)",
    r"amazoncorretto:(?P<version>.*)",
    r"amazoncorretto(?P<version>[0-9]+)$",
    r"amazoncorretto$",
    r"jdk-.*:(?P<version>[0-9]+.*)",
    r"java-.*:(?P<version>[0-9]+.*)",
    r"jdk-?(?P<version>[0-9]+.*)",
    r"java-?(?P<version>[0-9]+.*)",
]


def extract_java_specifier_from_docker_base_image(
    docker_base_image: str, *, strict: bool = False
) -> SpecifierSet | None:
    """Look for a java version in a docker image name.

    >>> extract_java_specifier_from_docker_base_image(
    ...     "registry.example.com/thing/jetty-foo:9.4.43-java17-buster-yy0.0.2",
    ...     strict=True,
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "registry.example.com/java-foo:11.0.1-stretch-xx0.0.1", strict=True
    ... )
    <SpecifierSet('==11.0.1+stretch-xx0.0.1')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto:17.0.5-al2", strict=True
    ... )
    <SpecifierSet('==17.0.5+al2')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto21:latest", strict=True
    ... )
    <SpecifierSet('~=21.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto:latest", strict=True
    ... )
    <SpecifierSet('~=8.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "registry.example.com/thing/jetty-foo:9.4.43-java17-buster-yy0.0.2",
    ...     strict=False,
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "registry.example.com/java-foo:11.0.1-stretch-xx0.0.1", strict=False
    ... )
    <SpecifierSet('~=11.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto:17.0.5-al2", strict=False
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto21:latest", strict=False
    ... )
    <SpecifierSet('~=21.0')>
    >>> extract_java_specifier_from_docker_base_image(
    ...     "amazoncorretto:latest", strict=False
    ... )
    <SpecifierSet('~=8.0')>
    >>> extract_java_specifier_from_docker_base_image("amazoncorretto", strict=False)
    >>> extract_java_specifier_from_docker_base_image("amazoncorretto", strict=True)
    """
    for docker_matcher in build_sbt_docker_matchers:
        docker_matches = re.search(docker_matcher, docker_base_image)
        if docker_matches:
            group_dict = docker_matches.groupdict()
            logger.debug("group_dict %s", group_dict)
            version = group_dict.get("version")
            if version == "latest":
                if "short_version" not in group_dict:
                    specifier = java_version.parse_java_specifier("8")
                    if specifier is not None:
                        return specifier
                else:
                    version = group_dict.get("short_version")
            if version is not None:
                specifier = java_version.parse_java_specifier(version.replace("-", "+", 1), strict=strict)
                if specifier is not None:
                    return specifier
    if docker_base_image == "amazoncorretto":
        logger.debug("Not picking a default version for amazoncorretto with no tags")
    return None


build_sbt_line_matchers = [
    r'dockerBaseImage\s*:=\s*s?"(?P<docker_base_image>[^"]+)"',
    r"dockerBaseImage\s*:=\s*s?'(?P<docker_base_image>[^']+)'",
]


def extract_java_specifier_from_build_sbt_line(line: str, *, strict: bool = False) -> SpecifierSet | None:
    """Get a java version from a build sbt line if possible.

    >>> extract_java_specifier_from_build_sbt_line(
    ...     '    dockerBaseImage := "amazoncorretto:17.0.5-al2"', strict=True
    ... )
    <SpecifierSet('==17.0.5+al2')>
    >>> extract_java_specifier_from_build_sbt_line(
    ...     '    dockerBaseImage        := s"$host/example-base-amzn2-java-amazoncorretto21:latest",',
    ...     strict=True,
    ... )
    <SpecifierSet('~=21.0')>
    >>> extract_java_specifier_from_build_sbt_line(
    ...     '    dockerBaseImage := "amazoncorretto:17.0.5-al2"', strict=False
    ... )
    <SpecifierSet('~=17.0')>
    >>> extract_java_specifier_from_build_sbt_line(
    ...     '    dockerBaseImage        := s"$host/example-base-amzn2-java-amazoncorretto21:latest",',
    ...     strict=False,
    ... )
    <SpecifierSet('~=21.0')>
    >>> extract_java_specifier_from_build_sbt_line("", strict=False)
    >>> extract_java_specifier_from_build_sbt_line("", strict=True)
    """
    line = line.strip()
    for matcher in build_sbt_line_matchers:
        matches = re.search(matcher, line)
        if matches:
            docker_base_image = matches.group("docker_base_image")
            specifier = extract_java_specifier_from_docker_base_image(docker_base_image, strict=strict)
            if specifier is not None:
                return specifier
    return None


def find_build_sbt_versions() -> list[utils.FoundVersion]:
    """Look for python versions in Dockerfiles."""
    logger.debug("Finding build.sbt versions...")
    found_versions: list[utils.FoundVersion] = []
    for build_sbt in file_utils.rglob_from_dir_containing(".git", "build.sbt"):
        logger.debug("Found build.sbt %s", build_sbt.file_path)
        for line in build_sbt.handle:
            specifier = extract_java_specifier_from_build_sbt_line(line)
            if specifier is not None:
                found_version = utils.FoundVersion(build_sbt.file_path, line, line.strip(), specifier)
                found_versions.append(found_version)
                continue
    return found_versions


def find_sdkman_rc_specifier(prefix: str, *, strict: bool = False) -> utils.FoundVersion | None:
    """Look for an .sdkmanrc file and extract a version from it."""
    sdkman_rc_path = file_utils.find_up(".sdkmanrc")
    if sdkman_rc_path is not None:
        with sdkman_rc_path.open("r") as handle:
            for line in handle:
                stripped_line = line.strip()
                candidate = re.sub(r"#.*", "", stripped_line).strip()
                if len(candidate) == 0 or "=" not in candidate:
                    continue
                key, value = candidate.split("=", 1)
                if key == prefix:
                    specifier = java_version.parse_java_specifier(value.replace("-", "+", 1), strict=strict)
                    if specifier:
                        return utils.FoundVersion(sdkman_rc_path, value, line, specifier)
                    logger.warning("Could not parse SDKMAN java version: %s", value)
    return None


def validate_sdkman_version(
    found_specifiers: list[utils.FoundVersion], return_code: int, sdkman_rc_specifier: utils.FoundVersion
) -> int:
    """Check that the .sdkmanrc version matches the given filters."""
    seen_combos: set[str] = set()
    sdk_man_version = java_version.SDKManVersion(sdkman_rc_specifier.selector)
    for found_version in found_specifiers:
        if sdk_man_version not in found_version.specifier_set:
            combo = f"{sdk_man_version.version} {found_version.specifier_set}"
            if combo not in seen_combos:
                logger.error(
                    "%s sdk man version %s does not match %s",
                    pathlib.Path.cwd(),
                    sdk_man_version.version,
                    found_version.specifier_set,
                )
                return_code = 1
                seen_combos.add(combo)
    return return_code


def filter_possible_versions(
    found_versions_by_specifier: dict[str, list[utils.FoundVersion]], possible_versions: set[str]
) -> list[Any]:
    """Produce a list of versions."""
    iterable = possible_versions

    for specifier_found_versions in found_versions_by_specifier.values():
        found_version = specifier_found_versions[0]
        specifier_set = found_version.specifier_set
        for version in possible_versions:
            logger.debug(
                "%s in %s == %s",
                version,
                specifier_set,
                java_version.specifier_set_contains_java_version(specifier_set, version),
            )
        iterable = specifier_set.filter(iterable, key=java_version.SDKManVersion)
    return list(iterable)


class Main(cmd.Main):
    """Check a harbor registry for docker images."""

    def __init__(self):
        super().__init__()
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument(
            "--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version"
        )
        ordering.add_argument("--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")
        self.parser.add_argument(
            "--sdkman", dest="sdkman", action="store_true", default=False, help="Output .sdkmanrc-style version"
        )
        self.parser.add_argument(
            "--check-sdkman",
            dest="check_sdkman",
            action="store_true",
            default=False,
            help="Verify that the version in .sdkmanrc is right",
        )
        self.parser.add_argument(
            "--dump", dest="dump", action="store_true", default=False, help="Dump the version information we found"
        )
        self.parser.add_argument(
            "--strict",
            dest="strict",
            action="store_true",
            default=False,
            help="Require minor, point, and build versions",
        )

    def setup(self) -> None:
        """Do something after parsing args but before main."""
        super().setup()
        if self.args.verbose:
            easy_logging.easy_initialize_logging("DEBUG", stream=sys.stderr, formatter=formatters.ConsoleFormatter())
        elif self.args.quiet:
            easy_logging.easy_initialize_logging("WARNING", stream=sys.stderr, formatter=formatters.ConsoleFormatter())
        else:
            easy_logging.easy_initialize_logging("INFO", stream=sys.stderr, formatter=formatters.ConsoleFormatter())

    def main(self) -> None:
        """Look in harbor registry for docker images."""
        super().main()

        return_code = 0
        strict = self.args.strict
        found_specifiers: list[utils.FoundVersion] = find_maven_versions(strict=strict)
        found_specifiers.extend(find_dockerfile_versions(strict=strict))
        found_specifiers.extend(find_github_action_java_versions(strict=strict))
        found_specifiers.extend(find_jenkins_file_versions(strict=strict))
        found_specifiers.extend(find_gradle_versions(strict=strict))
        sdkman_rc_specifier = find_sdkman_rc_specifier("java", strict=strict)
        if sdkman_rc_specifier:
            if self.args.check_sdkman:
                return_code = validate_sdkman_version(found_specifiers, return_code, sdkman_rc_specifier)
            found_specifiers.append(sdkman_rc_specifier)

        found_versions_by_specifier: dict[str, list[utils.FoundVersion]] = defaultdict(list)
        for found_version in found_specifiers:
            found_versions_by_specifier[f"{found_version.specifier_set}"].append(found_version)

        possible_versions = extract_java_versions(found_specifiers)
        filtered = filter_possible_versions(found_versions_by_specifier, possible_versions)
        if not filtered:
            logger.warning("No versions found from %s", possible_versions)
            utils.dump_versions(found_specifiers)
            return_code = 1
            sys.exit(return_code)
        else:
            if self.args.verbose or self.args.dump:
                utils.dump_versions(found_specifiers)
            if len(filtered) == 1:
                logger.debug("Only one version available from %s", possible_versions)
                chosen = filtered[0]
            else:
                sorted_versions = sorted(filtered, key=java_version.SDKManVersion)
                logger.debug("Picking %s of %s", self.args.pick, len(sorted_versions))
                if self.args.pick == "max":
                    chosen = sorted_versions[-1]
                else:
                    chosen = sorted_versions[0]
            if self.args.sdkman and chosen is not None:
                print(f"java={chosen}")
            else:
                print(chosen)
        sys.exit(return_code)


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
