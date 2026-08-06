"""Helpful java version functions and classes."""

import enum
import logging
import pathlib
import re

from packaging.specifiers import (
    InvalidSpecifier,
    Specifier,
    SpecifierSet,
)
from packaging.version import Version

from jmullan.artificer import base_version

logger = logging.getLogger(__name__)


class JavaVersion(enum.Enum):
    """Known Java versions and some useful metadata."""

    JAVA_1_0 = (("1.0",), 45)
    JAVA_1_1 = (("1.1",), 45)
    JAVA_1_2 = (("1.2", "2", "2.0"), 46)
    JAVA_1_3 = (("1.3", "3", "3.0"), 47)
    JAVA_1_4 = (("1.4", "4", "4.0"), 48)
    JAVA_5 = (("5", "5", "5.0", "1.5"), 49)
    JAVA_6 = (("6", "6", "6.0", "1.6"), 50)
    JAVA_7 = (("7", "7", "7.0", "1.7"), 51)
    JAVA_8 = (("8", "8", "8.0", "1.8"), 52, "~=8.0")
    JAVA_9 = (("9", "9.0"), 53)
    JAVA_10 = (("10", "10.0"), 54)
    JAVA_11 = (("11", "11.0", "1.11"), 55, "~=11.0")
    JAVA_12 = (("12", "12.0"), 56)
    JAVA_13 = (("13", "13.0"), 57)
    JAVA_14 = (("14", "14.0"), 58)
    JAVA_15 = (("15", "15.0"), 59)
    JAVA_16 = (("16", "16.0"), 60)
    JAVA_17 = (("17", "17.0"), 61, "~=17.0")
    JAVA_18 = (("18", "18.0"), 62)
    JAVA_19 = (("19", "19.0"), 63)
    JAVA_20 = (("20", "20.0"), 64)
    JAVA_21 = (("21", "21.0"), 65, "~=21.0")
    JAVA_22 = (("22", "22.0"), 66)
    JAVA_23 = (("23", "23.0"), 67)
    JAVA_24 = (("24", "24.0"), 68)
    JAVA_25 = (("25", "25.0"), 69)
    JAVA_26 = (("26", "26.0"), 70)
    JAVA_27 = (("27", "27.0"), 71)

    def __init__(
        self,
        aliases: tuple[str, ...],
        class_major: int,
        specifier: str | None = None,
    ) -> None:
        self.aliases = aliases
        self.class_major = class_major
        self._specifier = specifier

    @property
    def canonical(self) -> str:
        """Give the canonical alias of this version."""
        return self.aliases[0]

    @property
    def major_minor(self) -> str:
        """Give the two-part alias of this version."""
        for alias in self.aliases:
            if "." in alias:
                return alias
        return self.canonical

    @property
    def next_major_minor(self) -> str:
        """Give the version that follows this version."""
        for value in self.__class__:
            if value.class_major > self.class_major:
                return value.major_minor
        return "28.0"

    @property
    def specifier(self) -> str:
        """Return the specifier that would select this Java version."""
        if self._specifier is not None:
            return self._specifier
        canonical = self.aliases[0]
        parts = canonical.split(".")
        while len(parts) < base_version.MAJOR_MINOR_POINT_SECTION_COUNT:
            parts.append("0")
        return "~=" + ".".join(parts)

    @classmethod
    def from_version(cls, value: str) -> "JavaVersion | None":
        """Pick a version with an alias that matches the string."""
        normalized = value.strip()

        for version in cls:
            if normalized in version.aliases:
                return version
        return None

    @classmethod
    def from_class_major(cls, major: int) -> "JavaVersion | None":
        """Make a whole version from just one number."""
        for version in cls:
            if version.class_major == major:
                return version
        return None


class SDKManVersion(Version):
    """A version as told to SDK."""

    def __init__(self, version: str) -> None:
        super().__init__(version.replace("-", "+", 1))
        self.version = version


def get_matching_java_versions(restriction: str | Specifier | SpecifierSet) -> list[str]:
    """Get a list of versions matching a restriction."""
    if isinstance(restriction, str):
        try:
            specifier = SpecifierSet(restriction)
        except InvalidSpecifier:
            specifier = SpecifierSet(f"=={restriction}")
    else:
        specifier = restriction
    versions = []
    java_dir = pathlib.Path.home() / ".sdkman" / "candidates" / "java"
    if java_dir.exists():
        sdkman_versions = sorted(p.name for p in java_dir.iterdir() if p.is_dir() and p.name != "current")
        versions = [version for version in sdkman_versions if SDKManVersion(version) in specifier]
    for java_version in JavaVersion:
        if java_version.canonical in specifier:
            logger.debug("Found Java version: %s for %s", java_version, specifier)
            versions.append(f"{java_version.canonical}")
    return versions


def specifier_set_contains_java_version(specifier: SpecifierSet | Specifier, java_version: str) -> bool:
    """Check if a specifier set contains a Java version."""
    if specifier is None or java_version is None:
        return False
    pep_440_version = java_version.split("-", maxsplit=1)[0]
    logger.debug("Checking version %s as %s against %s", java_version, pep_440_version, specifier)
    return pep_440_version in specifier


def parse_java_specifier(specifier: str | list[str | None] | None, *, strict: bool = False) -> SpecifierSet | None:  # noqa: PLR0911 C901
    """Parse a specifier or specifiers into a SpecifierSet.

    >>> parse_java_specifier("11.0-stretch-yy0.0.1", strict=True)
    <SpecifierSet('~=11.0')>
    >>> parse_java_specifier("21.0.8-tem", strict=True)
    <SpecifierSet('==21.0.8+tem')>
    >>> parse_java_specifier("17.0.4.1+1", strict=True)
    <SpecifierSet('==17.0.4.1+1')>
    >>> parse_java_specifier("1.7.0_60", strict=True)
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("JDK 7 Update 60", strict=True)
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("21", strict=True)
    <SpecifierSet('~=21.0')>
    >>> parse_java_specifier("11.0.1", strict=True)
    <SpecifierSet('==11.0.1')>
    >>> parse_java_specifier("JDK 7u60", strict=True)
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("1.11", strict=True)
    <SpecifierSet('~=11.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_1_8", strict=True)
    <SpecifierSet('~=8.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_11", strict=True)
    <SpecifierSet('~=11.0')>

    >>> parse_java_specifier("11.0-stretch-yy0.0.1", strict=False)
    <SpecifierSet('~=11.0')>
    >>> parse_java_specifier("21.0.8-tem", strict=False)
    <SpecifierSet('~=21.0')>
    >>> parse_java_specifier("17.0.4.1+1", strict=False)
    <SpecifierSet('~=17.0')>
    >>> parse_java_specifier("1.7.0_60", strict=False)
    <SpecifierSet('~=7.0.0')>
    >>> parse_java_specifier("JDK 7 Update 60", strict=False)
    <SpecifierSet('~=7.0')>
    >>> parse_java_specifier("21", strict=False)
    <SpecifierSet('~=21.0')>
    >>> parse_java_specifier("11.0.1", strict=False)
    <SpecifierSet('~=11.0')>
    >>> parse_java_specifier("JDK 7u60", strict=False)
    <SpecifierSet('~=7.0')>
    >>> parse_java_specifier("1.11", strict=False)
    <SpecifierSet('~=11.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_1_8", strict=False)
    <SpecifierSet('~=8.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_11", strict=False)
    <SpecifierSet('~=11.0')>

    """
    if specifier is None:
        return None
    original_specifier = specifier
    if isinstance(specifier, list):
        specifiers = [parse_java_specifier(v) for v in specifier]
        non_none = [s for s in specifiers if s is not None]
        if len(non_none) == 1:
            return SpecifierSet(non_none[0])
        return base_version.SpecifierSetOr(non_none)
    specifier = specifier.strip()
    java_version_lower = specifier.lower().strip()

    specifier_set = parse_jdk_u(java_version_lower, strict=strict)
    if specifier_set is not None:
        return specifier_set

    specifier_set = parse_jdk_underscore_version(java_version_lower, strict=strict)
    if specifier_set is not None:
        return specifier_set
    specifier_set = parse_jdk_dash_version(original_specifier, java_version_lower, strict=strict)
    if specifier_set is not None:
        return specifier_set

    specifier_set = parse_dotted_version(original_specifier, specifier, strict=strict)
    if specifier_set is not None:
        return specifier_set

    matches = re.search(r"javaversion.version_(?P<version>[_0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1).replace("_", ".")
    matches = re.match(r"java[- ]?([.0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"java *([.0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1)
    java_version = JavaVersion.from_version(specifier)
    if java_version is not None:
        return SpecifierSet(f"{java_version.specifier}")
    try:
        return SpecifierSet(f"=={specifier}")
    except ValueError:
        pass
    return None


def parse_jdk_u(java_version_lower: str, *, strict: bool = False) -> SpecifierSet | None:
    """Parse a string like jdk 8u123 into a java version."""
    matches = re.match(r"(jdk *)?(?P<major>[0-9]+) *u(pdate)? *(?P<update>[0-9]+)(?P<build>-.*)?", java_version_lower)
    if matches:
        major = matches.group("major")
        update = matches.group("update")
        build = matches.group("build")
        if not strict:
            return SpecifierSet(f"~={major}.0")
        if build is not None:
            build = build.removeprefix("-b")
            build_was = build
            if re.match(r"^[0-9]+$", build):
                build = build.lstrip("0")
                if len(build) > 0:
                    return SpecifierSet(f"=={major}.0.{update}+{build}")
            return SpecifierSet(f"=={major}.0.{update}+{build_was}")
        return SpecifierSet(f"=={major}.0.{update}")
    return None


def parse_jdk_underscore_version(java_version_lower: str, *, strict: bool = False) -> SpecifierSet | None:
    """Parse a string like jdk 11.1.2_123 into a java version."""
    matches = re.match(
        r"(jdk *)?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(\.(?P<point>[0-9]+))?_(?P<update>[0-9]+)", java_version_lower
    )
    if matches:
        major = matches.group("major")
        minor = matches.group("minor")
        point = matches.group("point")
        update = matches.group("update")
        if point is None or point == "0":
            point = update
            update = None
        java_version = JavaVersion.from_version(f"{major}.{minor}")
        if java_version is not None:
            if not strict:
                return SpecifierSet(java_version.specifier)
            new_parts = java_version.major_minor.split(".")
            parts = [major, minor, point]
            if update is not None:
                parts.append(update)
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            return SpecifierSet("==" + ".".join(new_parts))
        if not strict:
            return SpecifierSet(f"~={major}.{minor}")
        return SpecifierSet(f"=={major}.{minor}.{point}+u{update}")
    return None


def parse_jdk_dash_version(
    original_specifier: str | list[str | None] | None, java_version_lower: str, *, strict: bool = False
) -> SpecifierSet | None:
    """Parse a string like jdk 11.1.2-123 into a java version."""
    matches = re.match(
        r"(jdk *)?(?P<major>[0-9]+)(\.(?P<minor>[0-9]+))?(\.(?P<point>[0-9]+))?[-+](?P<build>.+)", java_version_lower
    )
    if matches:
        major = matches.group("major")
        minor = matches.group("minor")
        if minor is None:
            java_version = JavaVersion.from_version(f"{major}")
            if java_version is not None:
                return SpecifierSet(java_version.specifier)
        point = matches.group("point")
        build = matches.group("build")
        if (point is None or point == "0") and re.match(r"^[0-9]+$", build):
            point = build
            build = None
        java_version = JavaVersion.from_version(f"{major}.{minor}")
        if java_version is not None:
            if point is None or not strict:
                return SpecifierSet(java_version.specifier)
            new_parts = java_version.major_minor.split(".")
            parts = [major, minor, point]
            if build is not None:
                parts.append(build)
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            specifier_set = base_version.maybe_specifier_set(original_specifier, "==" + ".".join(new_parts))
            if specifier_set is not None:
                return specifier_set
        specifier_set = base_version.maybe_specifier_set(original_specifier, f"=={major}.{minor}.{point}+{build}")
        if specifier_set is not None:
            return specifier_set
    return None


def parse_java_version_parts(
    original_specifier: str | list[str | None] | None,
    major: str | None,
    minor: str | None,
    point: str | None,
    build: str | None,
) -> SpecifierSet | None:
    """Parse four strings into a java version."""
    if major is None:
        return None
    if major is not None and minor is not None and point is not None and build is not None:
        if point == "0" and re.match(r"^[0-9]+$", build):
            return base_version.maybe_specifier_set(original_specifier, f"=={major}.{minor}.{build}")
        return base_version.maybe_specifier_set(original_specifier, f"=={major}.{minor}.{point}+{build}")
    if minor is None:
        java_version = JavaVersion.from_version(f"{major}")
        if java_version is not None:
            return SpecifierSet(java_version.specifier)
        return None
    java_version = JavaVersion.from_version(f"{major}.{minor}")
    if java_version is None:
        return None
    if point is None and build is None:
        return SpecifierSet(java_version.specifier)
    new_parts = java_version.major_minor.split(".")
    if build is None:
        parts = [major, minor, point]
    elif (point is None or point == "0") and (build is not None and re.match(r"^[0-9]+$", build)):
        parts = [major, minor, build]
    elif point is None:
        parts = [major, minor, point]
    else:
        parts = [major, minor, point, build]

    if (point is None or point == "0") and (build is not None and re.match(r"^[0-9]+$", build)):
        return parse_java_version_parts(original_specifier, major, minor, build, None)

    return None


def parse_dotted_version(
    original_specifier: str | list[str | None] | None, specifier: str, *, strict: bool = False
) -> SpecifierSet | None:
    """See if the specifier can be parsed as a dotted version."""
    parts = specifier.split(".")
    if len(parts) >= base_version.MAJOR_MINOR_POINT_SECTION_COUNT:
        java_version = JavaVersion.from_version(".".join(parts[:1]))
        if java_version is not None:
            if not strict:
                return SpecifierSet(java_version.specifier)
            canonical = java_version.canonical
            new_parts = canonical.split(".")
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            specifier_set = base_version.maybe_specifier_set(original_specifier, "==" + ".".join(new_parts))
            if specifier_set is not None:
                return specifier_set
    return None
