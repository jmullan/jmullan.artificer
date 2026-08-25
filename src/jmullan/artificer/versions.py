"""Helper methods for working with Python and Java versions."""

import logging
from collections.abc import Iterable, Iterator

from packaging.specifiers import (
    InvalidSpecifier,
    Specifier,
    SpecifierSet,
    UnparsedVersion,
    UnparsedVersionVar,
)
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)


MAJOR_MINOR_POINT_SECTION_COUNT = 3


def maybe_specifier_set(
    original_specifier: str | list[str | None] | None, specifier_string: str
) -> SpecifierSet | None:
    """Attempt to let SpecifierSet parse the string.

    Builds a specifier set... or not.
    """
    try:
        return SpecifierSet(specifier_string)
    except InvalidSpecifier:
        logger.debug("Could not parse specifier %s as %s", original_specifier, specifier_string)
    return None


def get_version(version: UnparsedVersion) -> Version | None:
    """Turn a version string into a Version object or None if there is an error."""
    if isinstance(version, Version):
        return version

    try:
        return Version(version)
    except InvalidVersion:
        pass
    return None


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

    def contains(self, item: UnparsedVersion, prereleases: bool | None = None, installed: bool | None = None) -> bool:  # noqa: FBT001
        """Return Whether the item is contained in this SpecifierSet.

        :param item:
            The item to check for, which can be a version string or a
            :class:`Version` instance.
        :param prereleases:
            Whether to match prereleases with this SpecifierSet. If set to
            ``None`` (the default), it will follow the recommendation from :pep:`440`
            and match prereleases, as there are no other versions.
        :param installed:
            Whether the item is installed. If set to ``True``, it will
            accept prerelease versions even if the specifier does not allow them.

        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.2.3")
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains(Version("1.2.3"))
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.0.1")
        False
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.3.0a1")
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1", prereleases=False).contains("1.3.0a1")
        False
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.3.0a1", prereleases=True)
        True
        """
        version = get_version(item)

        if version is not None and installed and version.is_prerelease:
            prereleases = True

        # When item is a string and === is involved, keep it as-is
        # so the comparison isn't done against the normalized form.
        if version is None or (self._has_arbitrary and not isinstance(item, Version)):
            check_item = item
        else:
            check_item = version
        return bool(list(self.filter([check_item], prereleases=prereleases)))

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
            Whether to allow prereleases in the returned iterator. If set to
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
        # SpecifierSet thinks for whether we should support prereleases.
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
                    item for item in iterable if (version := get_version(item)) is None or not version.is_prerelease
                )

        # Finally if prereleases is None, apply PEP 440 logic:
        # exclude prereleases unless there are no final releases that matched.
        filtered_items: list[UnparsedVersionVar] = []
        found_prereleases: list[UnparsedVersionVar] = []
        found_final_release = False

        for item in iterable:
            parsed_version = get_version(item)
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
