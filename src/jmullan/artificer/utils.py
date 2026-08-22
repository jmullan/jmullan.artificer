"""Utility functions for various artificer needs."""

import dataclasses
import logging
import os
import pathlib
import re
import tomllib
import typing
from collections import defaultdict
from collections.abc import Generator

import yaml
from packaging.specifiers import SpecifierSet

from jmullan.artificer import file_utils

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FoundVersion:
    """Data about a python version as found in a file."""

    file: pathlib.Path
    selector: str
    original_string: str
    specifier_set: SpecifierSet


def toml_var(filename: str | pathlib.Path, variable: str) -> typing.Any:
    """Load a TOML file and find a variable in that file."""
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return toml_var(pathlib.Path(filename), variable)
    if not filename.exists():
        message = f"{filename} does not exist"
        raise FileNotFoundError(message)
    with filename.open("rb") as f:
        data = tomllib.load(f)
        return deep_get(data, variable)


def yaml_var(filename: str | pathlib.Path, variable: str) -> typing.Any:
    """Load a YAML file and find a variable in that file."""
    values = yaml_vars(filename, variable)
    if values:
        return values[0]
    return None


def yaml_vars(filename: str | pathlib.Path, variable: str) -> list[typing.Any]:
    """Load a YAML file and find a variable in that file."""
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return yaml_vars(pathlib.Path(filename), variable)
    if not filename.exists():
        message = f"{filename} does not exist"
        raise FileNotFoundError(message)
    with filename.open("rb") as f:
        values = []
        for document in yaml.safe_load_all(f):
            value = deep_get(document, variable)
            if value is not None:
                values.append(value)
        return values


def deep_get(data: typing.Any, variable: str) -> typing.Any:
    """Get a value from a nested dictionary.

    >>> deep_get({"a": {"b": "c"}}, "a.b")
    'c'
    >>> deep_get({"a": {"b": [1, 2, 3]}}, "a.b.0")
    1
    >>> deep_get({"a": {"b": "c"}}, "a.d")  # not found
    >>> deep_get({"a": {"b": [1, 2, 3]}}, "a.b.q")  # not found
    """
    parts = variable.split(".")
    consumed = ""
    remaining = data
    for part in parts:
        if consumed:
            consumed = f"{consumed}.{part}"
        else:
            consumed = part
        if isinstance(remaining, dict):
            if part not in remaining:
                return None
            remaining = remaining[part]
        elif isinstance(remaining, list):
            if re.match("^[0-9]+$", part):
                try:
                    remaining = remaining[int(part)]
                except Exception:
                    logger.exception("Error parsing %s as a list index at %s", part, consumed)
                    return None
            else:
                logger.debug("%s not in %s at %s", variable, data, consumed)
                return None
    return remaining


def find_up(filename: str | pathlib.Path, in_dir: pathlib.Path | None = None) -> pathlib.Path | None:
    """Look for a file in a parent directory."""
    for path in find_ups(filename, in_dir):
        return path
    return None


def find_ups(filename: str | pathlib.Path, in_dir: pathlib.Path | None = None) -> Generator[pathlib.Path, typing.Any]:
    """Look for a file in parent directories."""
    if in_dir is None:
        in_dir = pathlib.Path().cwd()
    test_path = in_dir / filename
    if test_path.exists():
        yield test_path
    for ancestor in in_dir.parents:
        test_path = ancestor / filename
        if test_path.exists():
            yield test_path
    return None


class ResolvedPaths:
    """Precalculated paths for resolving short paths."""

    resolved: dict[str, pathlib.Path]
    reversed: dict[pathlib.Path, str]

    def __init__(self):
        self.resolved = {".": pathlib.Path().cwd()}
        self.reversed = {pathlib.Path().cwd(): "."}

    def add(self, in_dir: pathlib.Path) -> None:
        """Resolve and store a path relative to its shortest textual path."""
        expanded = in_dir
        if "~" in str(in_dir):
            expanded = in_dir.expanduser()
        resolved = expanded.resolve()
        text_path = str(in_dir)
        self.resolved[text_path] = resolved
        if resolved not in self.reversed or len(text_path) < len(self.reversed[resolved]):
            self.reversed[resolved] = text_path

    def resolve(self, in_dir: pathlib.Path) -> pathlib.Path:
        """Find the path with the shortest textual path for a path."""
        text_path = str(in_dir)
        if text_path not in self.resolved:
            self.add(in_dir)
        return self.resolved[text_path]

    def shortest(self, in_dir: pathlib.Path) -> str:
        """Find the shortest textual path for a path."""
        resolved = self.resolve(in_dir)
        return self.reversed[resolved]


def shortest_path_to(resolved_paths: ResolvedPaths, path: pathlib.Path) -> str:
    """Find the shortest text path relative to a set of paths."""
    shortest_path = str(path)
    for resolved, as_dir in resolved_paths.reversed.items():
        if path.is_relative_to(resolved):
            relative = path.relative_to(resolved)
            if str(relative).startswith("/"):
                candidate = str(path)
            elif as_dir in (".", "./"):
                candidate = str(relative)
            else:
                candidate = f"{as_dir}/{relative}"
        else:
            candidate = str(path)
        if len(candidate) < len(shortest_path):
            shortest_path = str(candidate)
    return shortest_path


def find_up_many(
    in_dirs: typing.Iterable[pathlib.Path], filenames: typing.Iterable[pathlib.Path], *, absolute: bool = False
) -> typing.Iterable[str]:
    """Look in dirs for files."""
    resolved_paths: ResolvedPaths = ResolvedPaths()
    used_paths: set[pathlib.Path] = set()
    seen_files: set[pathlib.Path] = set()
    for in_dir in in_dirs:
        resolved_paths.add(in_dir)

    for in_dir in in_dirs:
        resolved = resolved_paths.resolve(in_dir)

        if resolved not in used_paths:
            used_paths.add(resolved)
            for filename in filenames:
                for maybe_found in find_ups(filename, resolved):
                    if maybe_found in seen_files:
                        continue
                    seen_files.add(maybe_found)
                    if absolute:
                        yield str(maybe_found.resolve())
                    else:
                        yield shortest_path_to(resolved_paths, maybe_found)


def rglob_var(document: typing.Any, var_name: str) -> list[typing.Any]:
    """Find a variable in a data structure."""
    if document is None:
        return []
    variables = []
    if isinstance(document, dict):
        if var_name in document:
            return [document[var_name]]
        for value in document.values():
            variables.extend(rglob_var(value, var_name))
    elif isinstance(document, list | tuple | set):
        for value in document:
            variables.extend(rglob_var(value, var_name))
    elif hasattr(document, var_name):
        return [getattr(document, var_name)]
    return variables


def find_dockerfiles(in_dir: pathlib.Path) -> set[pathlib.Path]:
    """Look for Dockerfiles recursively.

    This can be expensive!
    """
    ignored_files = file_utils.find_ignored_files(in_dir)
    if ignored_files is None:
        # we are not in a git-controlled dir, so limit depth
        git_ignore_spec = file_utils.load_global_gitignore()
        dockerfiles = file_utils.rglob(in_dir, "Dockerfile*", 4, git_ignore_spec=git_ignore_spec, limit=10)
        dockerfiles = [p for p in dockerfiles if p.is_file()]
    else:
        dockerfiles = [p for p in file_utils.rglob(in_dir, "Dockerfile*") if p.is_file()]
    found = {p.resolve() for p in dockerfiles}
    if ignored_files is not None:
        ignored = {p.resolve() for p in ignored_files}
        return found - ignored
    return found


def dump_versions(found_versions: list[FoundVersion]) -> None:
    """Print where we found the versions."""
    if not found_versions:
        logger.debug("No specifiers found")
        return
    restrictions: dict[str, list[FoundVersion]] = defaultdict(list)
    for found_version in found_versions:
        restrictions[f"{found_version.specifier_set}"].append(found_version)
    for specifier_set, found in restrictions.items():
        logger.info(specifier_set)
        for found_version in found:
            logger.info(
                "    %s %s %r",
                pathlib.Path(os.path.relpath(found_version.file, pathlib.Path.cwd())),
                found_version.selector,
                found_version.original_string,
            )
