"""Turn a vague python version string into something concrete."""

import dataclasses
import enum
import logging
import os
import pathlib
import re
import sys
import typing
from collections.abc import Iterable, Iterator

from packaging.specifiers import (
    InvalidSpecifier,
    Specifier,
    SpecifierSet,
    UnparsedVersionVar,
    _coerce_version,
)
from packaging.version import InvalidVersion, Version

from jmullan.cmd import cmd

from jmullan.artificer import utils

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FoundVersion:
    """Data about a python version as found in a file."""

    file: pathlib.Path
    selector: str
    specifier_set: SpecifierSet
    original_string: str


class JavaVersion(enum.Enum):
    JAVA_1_0 = (("1.0",), 45)
    JAVA_1_1 = (("1.1",), 45)
    JAVA_1_2 = (("1.2", "2", "2.0"), 46)
    JAVA_1_3 = (("1.3", "3", "3.0"), 47)
    JAVA_1_4 = (("1.4", "4", "4.0"), 48)
    JAVA_5 = (("5", "5", "5.0", "1.5"), 49)
    JAVA_6 = (("6", "6", "6.0", "1.6"), 50)
    JAVA_7 = (("7", "7", "7.0", "1.7"), 51)
    JAVA_8 = (("8", "8", "8.0", "1.8"), 52)
    JAVA_9 = (("9", "9.0"), 53)
    JAVA_10 = (("10", "10.0"), 54)
    JAVA_11 = (("11", "11.0", "1.11"), 55)
    JAVA_12 = (("12", "12.0"), 56)
    JAVA_13 = (("13", "13.0"), 57)
    JAVA_14 = (("14", "14.0"), 58)
    JAVA_15 = (("15", "15.0"), 59)
    JAVA_16 = (("16", "16.0"), 60)
    JAVA_17 = (("17", "17.0"), 61)
    JAVA_18 = (("18", "18.0"), 62)
    JAVA_19 = (("19", "19.0"), 63)
    JAVA_20 = (("20", "20.0"), 64)
    JAVA_21 = (("21", "21.0"), 65)
    JAVA_22 = (("22", "22.0"), 66)
    JAVA_23 = (("23", "23.0"), 67)
    JAVA_24 = (("24", "24.0"), 68)
    JAVA_25 = (("25", "25.0"), 69)
    JAVA_26 = (("26", "26.0"), 70)

    def __init__(
        self,
        aliases: tuple[str, ...],
        class_major: int,
    ) -> None:
        self.aliases = aliases
        self.class_major = class_major

    @property
    def canonical(self) -> str:
        return self.aliases[0]

    @property
    def major_minor(self) -> str:
        for alias in self.aliases:
            if "." in alias:
                return alias
        return self.canonical

    @property
    def specifier(self):
        canonical = self.aliases[0]
        parts = canonical.split(".")
        while len(parts) < 3:
            parts.append("0")
        return "~=" + ".".join(parts)

    @classmethod
    def from_version(cls, value: str) -> "JavaVersion | None":
        normalized = value.strip()

        for version in cls:
            if normalized in version.aliases:
                return version
        return None

    @classmethod
    def from_class_major(cls, major: int) -> "JavaVersion | None":
        for version in cls:
            if version.class_major == major:
                return version
        return None


class SDKManVersion(Version):
    def __init__(self, version: str) -> None:
        super().__init__(version.replace("-", "+", 1))
        self.version = version


def get_matching_java_versions(restriction: str | Specifier | SpecifierSet) -> list[str]:
    """Get a list of versions matching a restriction."""
    if not PythonBuilds.possible_versions:
        PythonBuilds.populate_versions()
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
        for version in sdkman_versions:
            if SDKManVersion(version) in specifier:
                versions.append(version)
    for java_version in JavaVersion:
        if java_version.canonical in specifier:
            versions.append(f"{java_version.canonical}")  # noqa: PERF401
    return versions


def specifier_set_contains_java_version(specifier: SpecifierSet | Specifier, java_version: str) -> bool:
    pep_440_version = java_version.split("-")[0]
    logger.debug("Checking version %s as %s against %s", java_version, pep_440_version, specifier)
    return pep_440_version in specifier


def get_version(version: str | None) -> Version | None:
    """Turn a version string into a Version object or None if there is an error."""
    if version is None:
        return None
    try:
        return Version(version)
    except InvalidVersion:
        pass
    return None


class PythonBuilds:
    """Holds various python versions that can be matched against."""

    possible_versions: typing.ClassVar[set[Version]] = set()
    likely_versions: typing.ClassVar[set[Version]] = set()

    @classmethod
    def populate_versions(cls) -> None:  # noqa: C901
        """Find python versions in the system or the spec."""
        version_tuple = sys.version_info[:3]
        version_str = f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2]}"
        version = get_version(version_str)
        if version is not None:
            cls.possible_versions.add(version)
            cls.likely_versions.add(version)
        roots = [os.environ.get("PYENV_ROOT"), "~/.pyenv", "/usr/share/pyenv"]
        pyenv_roots = {x for x in roots if x is not None}
        for root in pyenv_roots:
            path = pathlib.Path(root).expanduser()
            if path.exists():
                path = pathlib.Path.expanduser(path / "plugins/python-build/share/python-build/")
                if path.exists():
                    maybe_versions = [get_version(f.name) for f in path.iterdir() if f.is_file()]
                    versions = [v for v in maybe_versions if v is not None]
                    if versions:
                        cls.possible_versions.update(versions)
        roots = [os.environ.get("PYENV_ROOT"), "~/.pyenv", "/usr/share/pyenv"]
        pyenv_roots = {x for x in roots if x is not None}
        for root in pyenv_roots:
            path = pathlib.Path(root).expanduser()
            if path.exists():
                path = pathlib.Path.expanduser(path / "versions")
                if path.exists():
                    version_dirs = [f for f in path.iterdir() if f.is_dir()]
                    for version_dir in version_dirs:
                        executable = version_dir / "bin/python"
                        if executable.is_file():
                            version = get_version(version_dir.name)
                            if version is not None:
                                cls.possible_versions.add(version)
                                cls.likely_versions.add(version)


def version_in_specifier(version: str, specifier: SpecifierSet) -> bool:
    """Check if the version is contained in the specifier."""
    if version is None or specifier is None:
        return False
    blessed_version = get_version(version)
    return blessed_version is not None and blessed_version in specifier


class SpecifierSetOr(SpecifierSet):
    """A Specifier Set that is an OR of the component parts instead of an AND."""

    def __init__(
        self,
        specifiers: str | Iterable[Specifier | SpecifierSet] = "",
        prereleases: bool | None = None,  # noqa: FBT001
    ):
        if isinstance(specifiers, str):
            specifiers = specifiers.replace("|", ",")
        super().__init__(specifiers, prereleases)

    def __repr__(self) -> str:
        """Represent the specifier set showing all internal state.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> SpecifierSetOr(">=1.0.0,!=2.0.0")
        <SpecifierSetOr('!=2.0.0|>=1.0.0')>
        >>> SpecifierSetOr(">=1.0.0,!=2.0.0", prereleases=False)
        <SpecifierSetOr('!=2.0.0|>=1.0.0', prereleases=False)>
        >>> SpecifierSetOr(">=1.0.0,!=2.0.0", prereleases=True)
        <SpecifierSetOr('!=2.0.0|>=1.0.0', prereleases=True)>
        """
        pre = f", prereleases={self.prereleases!r}" if self._prereleases is not None else ""

        return f"<SpecifierSetOr({str(self)!r}{pre})>"

    def __str__(self) -> str:
        """Represent the specifier set.

        Can be round-tripped.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> str(SpecifierSetOr(">=1.0.0,!=1.0.1"))
        '!=1.0.1|>=1.0.0'
        >>> str(SpecifierSetOr(">=1.0.0,!=1.0.1", prereleases=False))
        '!=1.0.1|>=1.0.0'
        """
        return "|".join(sorted(str(s) for s in self._specs))

    def filter(
        self,
        iterable: Iterable[UnparsedVersionVar],
        prereleases: bool | None = None,  # noqa: FBT001
    ) -> Iterator[UnparsedVersionVar]:
        """Filter items in the given iterable, that match the specifiers in this set.

        :param iterable:
            An iterable that can contain version strings and :class:`Version` instances.
            The items in the iterable will be filtered according to the specifier.
        :param prereleases:
            Whether or not to allow prereleases in the returned iterator. If set to
            ``None`` (the default), it will follow the recommendation from :pep:`440`
            and match prereleases if there are no other versions.

        >>> list(SpecifierSetOr(">=1.2.3").filter(["1.2", "1.3", "1.5a1"]))
        ['1.3']
        >>> list(SpecifierSetOr(">=1.2.3").filter(["1.2", "1.3", Version("1.4")]))
        ['1.3', <Version('1.4')>]
        >>> list(SpecifierSetOr(">=1.2.3").filter(["1.2", "1.5a1"]))
        ['1.5a1']
        >>> list(SpecifierSetOr(">=1.2.3").filter(["1.3", "1.5a1"], prereleases=True))
        ['1.3', '1.5a1']
        >>> list(SpecifierSetOr(">=1.2.3", prereleases=True).filter(["1.3", "1.5a1"]))
        ['1.3', '1.5a1']
        >>> list(SpecifierSetOr(">=1.2.3", prereleases=True).filter(["1.1"]))
        []
        >>> list(
        ...     SpecifierSetOr("==1.2.3|==5.6.7", prereleases=True).filter(
        ...         ["1.2.3", "5.6.7"]
        ...     )
        ... )
        ['1.2.3', '5.6.7']

        An "empty" SpecifierSet will filter items based on the presence of prerelease
        versions in the set.

        >>> list(SpecifierSetOr("").filter(["1.3", "1.5a1"]))
        ['1.3']
        >>> list(SpecifierSetOr("").filter(["1.5a1"]))
        ['1.5a1']
        >>> list(SpecifierSetOr("", prereleases=True).filter(["1.3", "1.5a1"]))
        ['1.3', '1.5a1']
        >>> list(SpecifierSetOr("").filter(["1.3", "1.5a1"], prereleases=True))
        ['1.3', '1.5a1']
        """
        # Determine if we're forcing a prerelease or not, if we're not forcing
        # one for this particular filter call, then we'll use whatever the
        # SpecifierSet thinks for whether or not we should support prereleases.
        if prereleases is None and self.prereleases is not None:
            prereleases = self.prereleases

        # If we have any specifiers, then we want to wrap our iterable in the
        # filter method for each one, this will act as a logical AND amongst
        # each specifier.
        if self._specs:
            # When prereleases is None, we need to let all versions through
            # the individual filters, then decide about prereleases at the end
            # based on whether any non-prereleases matched ALL specs.

            iterable = (
                v
                for v in iterable
                if any(
                    spec.contains(v, prereleases=True if prereleases is None else prereleases) for spec in self._specs
                )
            )

            if prereleases is not None:
                # If we have a forced prereleases value,
                # we can immediately return the iterator.
                return iter(iterable)
        else:
            # Handle empty SpecifierSet cases where prereleases is not None.
            if prereleases is True:
                return iter(iterable)

            if prereleases is False:
                return (
                    item for item in iterable if (version := _coerce_version(item)) is None or not version.is_prerelease
                )

        # Finally if prereleases is None, apply PEP 440 logic:
        # exclude prereleases unless there are no final releases that matched.
        filtered_items: list[UnparsedVersionVar] = []
        found_prereleases: list[UnparsedVersionVar] = []
        found_final_release = False

        for item in iterable:
            parsed_version = _coerce_version(item)
            # Arbitrary strings are always included as it is not
            # possible to determine if they are prereleases,
            # and they have already passed all specifiers.
            if parsed_version is None:
                filtered_items.append(item)
                found_prereleases.append(item)
            elif parsed_version.is_prerelease:
                found_prereleases.append(item)
            else:
                filtered_items.append(item)
                found_final_release = True

        return iter(filtered_items if found_final_release else found_prereleases)


def parse_python_specifier(specifier: str | list[str | None] | None) -> SpecifierSet | None:  # noqa: PLR0911 C901
    """Parse a specifier or specifiers into a SpecifierSet.

    >>> parse_python_specifier(">=2.3")
    <SpecifierSet('>=2.3')>
    >>> parse_python_specifier("2.3")
    <SpecifierSet('~=2.3.0')>
    >>> parse_python_specifier("2.7")
    <SpecifierSet('~=2.7.0')>
    >>> parse_python_specifier("3")
    <SpecifierSet('~=3.0')>
    >>> parse_python_specifier("py3")
    <SpecifierSet('~=3.0')>
    >>> parse_python_specifier("py36")
    <SpecifierSet('~=3.6.0')>
    >>> parse_python_specifier("py310")
    <SpecifierSet('~=3.10.0')>
    >>> parse_python_specifier("python3.13")
    <SpecifierSet('~=3.13.0')>
    >>> parse_python_specifier("3.9.13")
    <SpecifierSet('==3.9.13')>
    >>> parse_python_specifier("python-3.9.13")
    <SpecifierSet('==3.9.13')>
    >>> parse_python_specifier(["python3.13", "py27"])
    <SpecifierSetOr('~=2.7.0|~=3.13.0')>
    >>> parse_python_specifier(["Python 3.11.14"])
    <SpecifierSet('==3.11.14')>
    """
    if specifier is None:
        return None
    if isinstance(specifier, list):
        specifiers = [parse_python_specifier(v) for v in specifier]
        if len(specifiers) == 1:
            return SpecifierSet(specifiers[0])
        return SpecifierSetOr(specifiers)
    specifier = specifier.strip()
    py_version_lower = specifier.lower().strip()
    matches = re.match(r"^python[- ]?([.0-9]+)$", py_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"^py([.0-9]+)$", py_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"^([0-9])\.([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={specifier}.0")
    matches = re.match(r"^([23])$", specifier)
    if matches:
        return SpecifierSet(f"~={matches.group(1)}.0")
    matches = re.search(r"^([23])\.([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={specifier}.0")
    matches = re.search(r"^([23])\.([0-9]+).([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"=={specifier}")
    matches = re.search(r"^([23])([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={matches.group(1)}.{matches.group(2)}.0")
    try:
        return SpecifierSet(specifier)
    except InvalidSpecifier:
        logger.debug("Could not parse %s as a specifier", specifier)
        return None


def maybe_specifier_set(
    original_specifier: str | list[str | None] | None, specifier_string: str
) -> SpecifierSet | None:
    try:
        return SpecifierSet(specifier_string)
    except InvalidSpecifier:
        logger.debug("Could not parse specifier %s as %s", original_specifier, specifier_string)
    return None


def parse_java_specifier(specifier: str | list[str | None] | None) -> SpecifierSet | None:  # noqa: PLR0911 C901
    """Parse a specifier or specifiers into a SpecifierSet.
    >>> parse_java_specifier("11.0-stretch-yy0.0.1")
    <SpecifierSet('~=11.0.0')>
    >>> parse_java_specifier("21.0.8-tem")
    <SpecifierSet('==21.0.8+tem')>
    >>> parse_java_specifier("17.0.4.1+1")
    <SpecifierSet('==17.0.4.1+1')>
    >>> parse_java_specifier("1.7.0_60")
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("JDK 7 Update 60")
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("21")
    <SpecifierSet('~=21.0.0')>
    >>> parse_java_specifier("11.0.1")
    <SpecifierSet('==11.0.1')>
    >>> parse_java_specifier("JDK 7u60")
    <SpecifierSet('==7.0.60')>
    >>> parse_java_specifier("1.11")
    <SpecifierSet('~=11.0.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_1_8")
    <SpecifierSet('~=8.0.0')>
    >>> parse_java_specifier("JavaVersion.VERSION_11")
    <SpecifierSet('~=11.0.0')>
    """
    if specifier is None:
        return None
    original_specifier = specifier
    if isinstance(specifier, list):
        specifiers = [parse_java_specifier(v) for v in specifier]
        if len(specifiers) == 1:
            return SpecifierSet(specifiers[0])
        return SpecifierSetOr(specifiers)
    specifier = specifier.strip()
    java_version_lower = specifier.lower().strip()
    matches = re.search(r"javaversion.version_(?P<version>[_0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1).replace("_", ".")
    matches = re.match(r"java[- ]?([.0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"java *([.0-9]+)$", java_version_lower)
    if matches:
        specifier = matches.group(1)
    # jdk 8u123
    matches = re.match(r"(jdk *)?(?P<major>[0-9]+) *u(pdate)? *(?P<update>[0-9]+)(?P<build>-.*)?", java_version_lower)
    if matches:
        major = matches.group("major")
        update = matches.group("update")
        build = matches.group("build")
        if build is not None:
            build = build.removeprefix("-b")
            build_was = build
            if re.match(r"^[0-9]+$", build):
                build = build.lstrip("0")
                if len(build) > 0:
                    return SpecifierSet(f"=={major}.0.{update}+{build}")
            return SpecifierSet(f"=={major}.0.{update}+{build_was}")
        return SpecifierSet(f"=={major}.0.{update}")
    # jdk 11.1.2_123
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
            new_parts = java_version.major_minor.split(".")
            parts = [major, minor, point]
            if update is not None:
                parts.append(update)
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            return SpecifierSet("==" + ".".join(new_parts))
        return SpecifierSet(f"=={major}.{minor}.{point}+u{update}")

    # jdk 11.1.2-123
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
            if point is None:
                return SpecifierSet(java_version.specifier)
            new_parts = java_version.major_minor.split(".")
            parts = [major, minor, point]
            if build is not None:
                parts.append(build)
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            specifier_set = maybe_specifier_set(original_specifier, "==" + ".".join(new_parts))
            if specifier_set is not None:
                return specifier_set
        specifier_set = maybe_specifier_set(original_specifier, f"=={major}.{minor}.{point}+{build}")
        if specifier_set is not None:
            return specifier_set

    parts = specifier.split(".")
    if len(parts) == 3:
        java_version = JavaVersion.from_version(".".join(parts[:1]))
        if java_version is not None:
            canonical = java_version.canonical
            new_parts = canonical.split(".")
            while len(new_parts) < len(parts):
                new_parts.append(parts[len(new_parts)])
            # we use == here because the asked-for java version was 3 segments
            specifier_set = maybe_specifier_set(original_specifier, "==" + ".".join(new_parts))
            if specifier_set is not None:
                return specifier_set

    java_version = JavaVersion.from_version(specifier)
    if java_version is not None:
        return SpecifierSet(f"{java_version.specifier}")
    try:
        return SpecifierSet(f"=={specifier}")
    except ValueError:
        pass
    return None


def find_python_toml_version(path: pathlib.Path, selector: str) -> FoundVersion | None:
    """Extract a version from a TOML file."""
    value = utils.toml_var(path, selector)
    if value is None:
        return None
    specifier = parse_python_specifier(value)
    if specifier:
        return FoundVersion(path, selector, specifier, value)
    return None


def get_matching_python_versions(restriction: str | Specifier | SpecifierSet, backwards: bool = False) -> list[str]:  # noqa: FBT001 FBT002
    """Get a list of versions matching a restriction."""
    if not PythonBuilds.possible_versions:
        PythonBuilds.populate_versions()
    if isinstance(restriction, str):
        try:
            specifier = SpecifierSet(restriction)
        except InvalidSpecifier:
            specifier = SpecifierSet(f"=={restriction}")
    else:
        specifier = restriction
    versions = []
    for version in sorted(PythonBuilds.likely_versions, reverse=backwards):
        if version in specifier:
            versions.append(f"{version}")  # noqa: PERF401
    for version in sorted(PythonBuilds.possible_versions, reverse=backwards):
        if version in specifier:
            versions.append(f"{version}")  # noqa: PERF401

    return versions


def get_matching_python_version(restriction: str, pick: str | None = None) -> str | None:
    """Get the minimum version from a restriction."""
    backwards = pick == "max"
    matching_versions = get_matching_python_versions(restriction, backwards)
    if matching_versions:
        return matching_versions[0]
    return None


class Main(cmd.Main):
    """Figure out a version based on restrictions and available python versions."""

    def __init__(self):
        super().__init__()
        self.parser.add_argument("restriction", help="Figure out a version from this string")
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument(
            "--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version"
        )
        ordering.add_argument("--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")

    def main(self) -> None:
        """Turn a python version string into a reasonable python version."""
        super().main()
        if self.args.restriction is None:
            self.parser.print_usage()
            sys.exit(1)

        restriction = self.args.restriction.strip()
        if not len(restriction):
            self.parser.print_usage()
            sys.exit(1)
        matching_version = get_matching_python_version(restriction, pick=self.args.pick)
        if matching_version is not None:
            print(matching_version)
            sys.exit(0)
        sys.exit(1)


def main() -> None:
    """Turn a python version string into a reasonable python version."""
    Main().main()


if __name__ == "__main__":
    main()
