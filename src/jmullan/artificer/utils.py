"""Utility functions for various artificer needs."""

import dataclasses
import logging
import os
import pathlib
import re
import tomllib
import typing
from collections import defaultdict

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
