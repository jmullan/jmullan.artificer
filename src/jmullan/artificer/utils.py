"""Utility functions for various artificer needs."""

import dataclasses
import io
import logging
import os
import pathlib
import re
import subprocess
import tomllib
import typing
from collections import defaultdict

import pathspec
import yaml
from packaging.specifiers import SpecifierSet

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FoundVersion:
    """Data about a python version as found in a file."""

    file: pathlib.Path
    selector: str
    specifier_set: SpecifierSet
    original_string: str


@dataclasses.dataclass
class GlobbedFile:
    """Represents a file and how it was found."""

    glob: str
    file_path: pathlib.Path
    handle: io.TextIOWrapper


def rglob_from_dir_containing(
    signpost_path: str,
    glob: str,
    max_depth: int | None = None,
    git_ignore_spec: pathspec.PathSpec | None = None,
    limit: int | None = None,
) -> typing.Generator[GlobbedFile, typing.Any]:
    """Find a file in a directory containing a file."""
    signpost = find_up(signpost_path)

    if signpost is not None and signpost.exists() and signpost.parent.is_dir():
        rglob(signpost.parent, glob, max_depth, git_ignore_spec, limit)
        for found_file in signpost.parent.rglob(glob):
            with found_file.open("r") as file_handle:
                logger.debug("Found %s %s", glob, found_file)
                yield GlobbedFile(glob, found_file, file_handle)


def rglob(  # noqa: PLR0912 C901
    path: pathlib.Path,
    glob: str,
    max_depth: int | None = None,
    git_ignore_spec: pathspec.PathSpec | None = None,
    limit: int | None = None,
) -> list[pathlib.Path]:
    """Look for files that match the glob but are not excluded."""
    files: list[pathlib.Path] = []
    if path is None or not path.exists() or glob is None or not len(glob):
        return files
    if max_depth is None:
        max_depth = len(glob)
    if max_depth is None:
        logger.debug("max_depth set to None, using built-in rglob")
        return list(path.rglob(glob))
    has_sep = any(sep in glob for sep in ("/", os.sep))

    root_depth = len(path.parts)
    logger.debug("max_depth set to %s, using custom rglob", max_depth)
    for directory_path_string, directory_names, filenames in os.walk(path):
        dir_path = pathlib.Path(directory_path_string)
        depth = len(dir_path.parts) - root_depth
        if depth >= max_depth:
            # stop descending
            directory_names[:] = []
        if git_ignore_spec is not None:
            for d in list(directory_names):
                if git_ignore_spec.match_file(d) or git_ignore_spec.match_file(dir_path / d):
                    directory_names.remove(d)
        if git_ignore_spec is not None:
            filenames = [f for f in filenames if not git_ignore_spec.match_file(f)]  # noqa: PLW2901
        for filename in filenames:
            found_path = dir_path / filename
            if git_ignore_spec is not None and git_ignore_spec.match_file(found_path):
                continue
            if has_sep:
                matched = found_path.match(glob)
            else:
                matched = pathlib.Path(filename).match(glob)
            if matched:
                files.append(found_path)
                if limit is not None and len(files) >= limit:
                    return files
    return files


def find_ignored_files(in_dir: pathlib.Path) -> set[pathlib.Path] | None:
    """Ask git to tell us what files can be ignored."""
    dot_git = find_up(".git")
    if dot_git is None or not dot_git.is_dir():
        return None

    command = ("git", "ls-files", "--others", "-i", "--exclude-standard")
    files = run(*command, cwd=in_dir)
    return {in_dir / file_name for file_name in files if file_name is not None}


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


def find_up(filename: str | pathlib.Path) -> pathlib.Path | None:
    """Look for a file in a parent directory."""
    current_dir = pathlib.Path().absolute()
    test_path = current_dir / filename
    if test_path.exists():
        return test_path
    for ancestor in current_dir.parents:
        test_path = ancestor / filename
        if test_path.exists():
            return test_path
        current_dir = current_dir.parent
    return None


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


def load_global_gitignore() -> pathspec.PathSpec | None:
    """Look for a global .gitignore file to find ignorable paths."""
    command = ("git", "config", "--get", "core.excludesfile")
    file_paths = run(*command)
    if file_paths is None:
        return None
    for file_path in file_paths:
        git_ignore = pathlib.Path(file_path.strip()).expanduser()
        if git_ignore.is_file():
            with git_ignore.open("r", encoding="UTF-8") as fh:
                return pathspec.PathSpec.from_lines("gitwildmatch", fh)
    return None


def find_dockerfiles(in_dir: pathlib.Path) -> set[pathlib.Path]:
    """Look for Dockerfiles recursively.

    This can be expensive!
    """
    ignored_files = find_ignored_files(in_dir)
    if ignored_files is None:
        # we are not in a git-controlled dir, so limit depth
        git_ignore_spec = load_global_gitignore()
        dockerfiles = rglob(in_dir, "Dockerfile*", 4, git_ignore_spec=git_ignore_spec, limit=10)
        dockerfiles = [p for p in dockerfiles if p.is_file()]
    else:
        dockerfiles = [p for p in rglob(in_dir, "Dockerfile*") if p.is_file()]
    found = {p.resolve() for p in dockerfiles}
    if ignored_files is not None:
        ignored = {p.resolve() for p in ignored_files}
        return found - ignored
    return found


def run(*args: str, cwd: pathlib.Path | None = None) -> list[str]:
    """Run a command and return the output as a list."""
    if (len(args)) == 1 and " " in args[0]:
        return run(*(args[0].split(" ")), cwd=cwd)
    logger.debug("Running %s", " ".join(args))
    with subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd) as proc:  # noqa: S603
        if proc.stdout is not None:
            return proc.stdout.read().decode("UTF8").strip().split("\n")
    return []


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
