import logging
from collections import defaultdict
from typing import Iterable, Iterator

from jmullan.cmd import cmd
from packaging.specifiers import SpecifierSet, InvalidSpecifier, Specifier, UnparsedVersion, _coerce_version, \
    UnparsedVersionVar
from packaging.version import Version, InvalidVersion
import pathlib
import os
import re
import sys

logger = logging.getLogger(__name__)


def get_version(version: str | None) -> Version | None:
    if version is None:
        return None
    try:
        return Version(version)
    except InvalidVersion:
        pass
    return None


class PythonBuilds:
    possible_versions: set[Version] = set()
    likely_versions: set[Version] = set()

    @classmethod
    def populate_versions(cls):
        version_tuple = sys.version_info[:3]
        version_str = f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2]}"
        version = get_version(version_str)
        if version is not None:
            cls.possible_versions.add(version)
            cls.likely_versions.add(version)
        roots = [os.environ.get("PYENV_ROOT"), "~/.pyenv", "/usr/share/pyenv"]
        pyenv_roots = set(x for x in roots if x is not None)
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
        pyenv_roots = set(x for x in roots if x is not None)
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
    if version is None or specifier is None:
        return False
    blessed_version = get_version(version)
    return blessed_version is not None and blessed_version in specifier


class SpecifierSetOr(SpecifierSet):
    def __init__(self,
         specifiers: str | Iterable[Specifier | SpecifierSet] = "",
         prereleases: bool | None = None,
     ):
        if isinstance(specifiers, str):
            specifiers = specifiers.replace("|", ",")
        super().__init__(specifiers, prereleases)

    def __repr__(self) -> str:
        """A representation of the specifier set that shows all internal state.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> SpecifierSetOr('>=1.0.0,!=2.0.0')
        <SpecifierSetOr('!=2.0.0|>=1.0.0')>
        >>> SpecifierSetOr('>=1.0.0,!=2.0.0', prereleases=False)
        <SpecifierSetOr('!=2.0.0|>=1.0.0', prereleases=False)>
        >>> SpecifierSetOr('>=1.0.0,!=2.0.0', prereleases=True)
        <SpecifierSetOr('!=2.0.0|>=1.0.0', prereleases=True)>
        """
        pre = (
            f", prereleases={self.prereleases!r}"
            if self._prereleases is not None
            else ""
        )

        return f"<SpecifierSetOr({str(self)!r}{pre})>"

    def __str__(self) -> str:
        """A string representation of the specifier set that can be round-tripped.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> str(SpecifierSetOr(">=1.0.0,!=1.0.1"))
        '!=1.0.1|>=1.0.0'
        >>> str(SpecifierSetOr(">=1.0.0,!=1.0.1", prereleases=False))
        '!=1.0.1|>=1.0.0'
        """
        return "|".join(sorted(str(s) for s in self._specs))

    def filter(
        self, iterable: Iterable[UnparsedVersionVar], prereleases: bool | None = None
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
        >>> list(SpecifierSetOr("==1.2.3|==5.6.7", prereleases=True).filter(["1.2.3", "5.6.7"]))
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
                for v
                in iterable
                if any(
                    spec.contains(v, prereleases=True if prereleases is None else prereleases)
                    for spec in self._specs
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
                    item
                    for item in iterable
                    if (version := _coerce_version(item)) is None
                    or not version.is_prerelease
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

def parse_specifier(specifier: str | list[str | None] | None) -> SpecifierSet | None:
    """
    >>> parse_specifier(">=2.3")
    <SpecifierSet('>=2.3')>
    >>> parse_specifier("2.3")
    <SpecifierSet('~=2.3')>
    >>> parse_specifier("2.7")
    <SpecifierSet('~=2.7')>
    >>> parse_specifier("3")
    <SpecifierSet('~=3.0')>
    >>> parse_specifier("py3")
    <SpecifierSet('~=3.0')>
    >>> parse_specifier("py36")
    <SpecifierSet('~=3.6')>
    >>> parse_specifier("py310")
    <SpecifierSet('~=3.10')>
    >>> parse_specifier("python3.13")
    <SpecifierSet('~=3.13')>
    >>> parse_specifier(["python3.13", "py27"])
    <SpecifierSetOr('~=2.7|~=3.13')>
    """
    if specifier is None:
        return None
    if isinstance(specifier, list):
        specifiers = [parse_specifier(v) for v in specifier]
        return SpecifierSetOr(specifiers)

    py_version_lower = specifier.lower()
    matches = re.match(r"^python([.0-9]+)$", py_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"^py([0-9]+)$", py_version_lower)
    if matches:
        specifier = matches.group(1)
    matches = re.match(r"^([0-9])\.([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={specifier}")
    matches = re.match(r"^([23])$", specifier)
    if matches:
        return SpecifierSet(f"~={matches.group(1)}.0")
    matches = re.search(r"^([23])\.([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={specifier}")
    matches = re.search(r"^([23])([0-9]+)$", specifier)
    if matches:
        return SpecifierSet(f"~={matches.group(1)}.{matches.group(2)}")
    try:
        return SpecifierSet(specifier)
    except InvalidSpecifier:
        logger.warning("Could not parse %s as a specifier", specifier)
        return None


def get_matching_version(restriction: str, pick: str | None = None) -> str | None:
    """Get the minimum version from a restriction."""
    backwards = pick == "max"
    if not PythonBuilds.possible_versions:
        PythonBuilds.populate_versions()
    try:
        specifier = SpecifierSet(restriction)
    except InvalidSpecifier:
        specifier = SpecifierSet(f"=={restriction}")
    for version in sorted(PythonBuilds.likely_versions, reverse=backwards):
        if version in specifier:
            return f'{version}'
    for version in sorted(PythonBuilds.possible_versions, reverse=backwards):
        if version in specifier:
            return f'{version}'

    return None


class Main(cmd.Main):
    """Figure out a python version based on restrictions and available python
    versions.
    """

    def __init__(self):
        super().__init__()
        self.parser.add_argument(
            "restriction",
            help="Figure out a version from this string"
        )
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument("--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version")
        ordering.add_argument(
            "--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")

    def main(self):
        super().main()
        if self.args.restriction is None:
            self.parser.print_usage()
            exit(1)

        restriction = self.args.restriction.strip()
        if not len(restriction):
            self.parser.print_usage()
            exit(1)
        matching_version = get_matching_version(restriction, pick=self.args.pick)
        if matching_version is not None:
            print(matching_version)
            exit(0)
        exit(1)


def main():
    Main().main()


if __name__ == "__main__":
    main()
