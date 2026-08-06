"""Utility functions for finding files."""

import dataclasses
import io
import logging
import math
import os
import pathlib
import re
import typing
from collections.abc import Generator

import pathspec

from jmullan.artificer import process_utils

logger = logging.getLogger(__name__)

PYTHON_EXTENSIONS = [
    ".pyx",  # Cython
    ".pxd",  # Cython declarations
    ".pyi",  # type stubs
    ".ipynb",  # Jupyter notebooks (JSON, not source)
]


@dataclasses.dataclass
class GlobbedFile:
    """Represents a file and how it was found."""

    glob: str
    file_path: pathlib.Path
    handle: io.TextIOWrapper


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


def load_global_gitignore() -> pathspec.PathSpec | None:
    """Look for a global .gitignore file to find ignorable paths."""
    command = ("git", "config", "--get", "core.excludesfile")
    file_paths = process_utils.run(*command)
    if file_paths is None:
        return None
    for file_path in file_paths:
        git_ignore = pathlib.Path(file_path.strip()).expanduser()
        if git_ignore.is_file():
            with git_ignore.open("r", encoding="UTF-8") as fh:
                return pathspec.PathSpec.from_lines("gitwildmatch", fh)
    return None


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
    files = process_utils.run(*command, cwd=in_dir)
    return {in_dir / file_name for file_name in files if file_name is not None}


def judge_path(path: pathlib.Path) -> tuple[float, float]:
    """Make a sortable tuple of number of path components and string path length."""
    if path is None or not path.exists():
        return math.inf, math.inf
    return len(path.parts), len(str(path))


def resolve_paths(paths: list[pathlib.Path]) -> dict[pathlib.Path, pathlib.Path]:
    """Make a dictionary of paths to their shortest expressed locations."""
    resolved_to_display: dict[pathlib.Path, pathlib.Path] = {}

    for path in paths:
        resolved = path.expanduser().resolve()
        display = resolved_to_display.get(resolved)

        if display is None or judge_path(path) < judge_path(display):
            resolved_to_display[resolved] = path
    return resolved_to_display


def glob_files(paths: list[pathlib.Path], globs: list[str]) -> Generator[pathlib.Path]:
    """Look for files that match an optional set of globs."""
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            if globs:
                for glob in globs:
                    yield from (p for p in path.rglob(glob) if p.is_file())
            else:
                yield from (p for p in path.rglob("*") if p.is_file())


def regex_files(paths: list[pathlib.Path], globs: list[str], regexes: list[str]) -> Generator[pathlib.Path]:
    """Look for files that match any of an optional set of regexes."""
    if not regexes:
        yield from glob_files(paths, globs)
    else:
        for path in glob_files(paths, globs):
            if any(re.search(regex, str(path)) for regex in regexes):
                yield path


def matches_a_file_type(path: pathlib.Path, file_type: str) -> bool:
    """Look for files that match one file type."""
    if file_type is None:
        return False
    if not path.is_file():
        return False
    if file_type == "utf8":
        try:
            with path.open("r", encoding="utf-8") as f:
                for _ in f:
                    pass
        except UnicodeDecodeError:
            return False
        else:
            return True
    if file_type == "python":
        return any(path.name.endswith(ext) for ext in PYTHON_EXTENSIONS)
    return False


def matches_any_file_type(path: pathlib.Path, file_types: list[str]) -> bool:
    """Look for files that match given file types."""
    if path is None:
        return False
    if not file_types:
        return True
    return any(matches_a_file_type(path, file_type) for file_type in file_types)


def find_files(
    paths: list[pathlib.Path],
    globs: list[str],
    regexes: list[str],
    file_types: list[str],
) -> Generator[pathlib.Path]:
    """Look for files that match optional restrictions."""
    if file_types:
        for file_path in regex_files(paths, globs, regexes):
            if any(matches_a_file_type(file_path, file_type) for file_type in file_types):
                yield file_path
    yield from regex_files(paths, globs, regexes)
