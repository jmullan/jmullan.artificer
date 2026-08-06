"""Contains tooling about Python versions."""

import enum
import logging
import os
import pathlib
import re
import sys
import typing

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import Version

from jmullan.artificer import base_version, utils

logger = logging.getLogger(__name__)


class PythonVersion(enum.Enum):
    """Known Python versions and some useful metadata."""

    PYTHON_2_7 = ("2.7", 4)
    PYTHON_3_5 = ("3.5", 5)
    PYTHON_3_6 = ("3.6", 6)
    PYTHON_3_7 = ("3.7", 7)
    PYTHON_3_8 = ("3.8", 8)
    PYTHON_3_9 = ("3.9", 9)
    PYTHON_3_10 = ("3.10", 10)
    PYTHON_3_11 = ("3.11", 11)
    PYTHON_3_12 = ("3.12", 12)
    PYTHON_3_13 = ("3.13", 13)
    PYTHON_3_14 = ("3.14", 14)
    PYTHON_3_15 = ("3.15", 15)

    def __init__(
        self,
        version_name: str,
        class_major: int,
    ) -> None:
        self.version_name = version_name
        self.class_major = class_major

    @property
    def major_minor_patch(self) -> str:
        """Get a three part version string."""
        parts = self.version_name.split(".")
        while len(parts) < base_version.MAJOR_MINOR_POINT_SECTION_COUNT:
            parts.append("0")
        return ".".join(parts)

    @property
    def specifier(self) -> str:
        """Get a python version specifier."""
        return f"~={self.major_minor_patch}"

    @classmethod
    def from_version(cls, value: str) -> "PythonVersion | None":
        """Attempt to parse a string into a PythonVersion."""
        normalized = value.strip()

        for version in cls:
            if normalized == version.version_name:
                return version
        return None

    @property
    def next_version(self) -> "PythonVersion | None":
        """Find the next version after this version."""
        for value in self.__class__:
            if value.class_major > self.class_major:
                return value
        return None

    @property
    def version(self) -> Version:
        """Build a Version from this PythonVersion."""
        return Version(self.version_name)


class PythonBuilds:
    """Holds various python versions that can be matched against."""

    possible_versions: typing.ClassVar[set[Version]] = set()
    likely_versions: typing.ClassVar[set[Version]] = set()

    @classmethod
    def populate_versions(cls) -> None:  # noqa: C901
        """Find python versions in the system or the spec."""
        version_tuple = sys.version_info[:3]
        version_str = f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2]}"
        version = base_version.get_version(version_str)
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
                    maybe_versions = [base_version.get_version(f.name) for f in path.iterdir() if f.is_file()]
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
                            version = base_version.get_version(version_dir.name)
                            if version is not None:
                                cls.possible_versions.add(version)
                                cls.likely_versions.add(version)


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
    >>> parse_python_specifier("Programming Language :: Python :: 3.6")
    <SpecifierSet('~=3.6.0')>
    >>> parse_python_specifier(["python3.13", "py27"])
    <SpecifierSetOr('~=2.7.0|~=3.13.0')>
    >>> parse_python_specifier(["Python 3.11.14"])
    <SpecifierSet('==3.11.14')>
    """
    if specifier is None:
        return None
    if isinstance(specifier, list):
        specifiers = [s for s in [parse_python_specifier(v) for v in specifier] if s is not None]
        if specifiers is not None:
            if len(specifiers) == 1:
                return specifiers[0]
            return base_version.SpecifierSetOr(specifiers)
    specifier = specifier.strip()
    matches = re.search(r"Programming Language :: Python :: ([.0-9]+)", specifier)
    if matches:
        specifier = matches.group(1)
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


def find_python_toml_version(path: pathlib.Path, selector: str) -> utils.FoundVersion | None:
    """Extract a version from a TOML file."""
    value = utils.toml_var(path, selector)
    if value is None:
        return None
    specifier = parse_python_specifier(value)
    if specifier:
        return utils.FoundVersion(path, selector, value, specifier)
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
