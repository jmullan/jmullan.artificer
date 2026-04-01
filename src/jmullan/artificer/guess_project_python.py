"""Look in various files to guess the desired python version"""

import dataclasses
import logging
import os
import pathlib
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from typing import Any

import yaml
from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from jmullan.cmd import cmd
from jmullan.logging import easy_logging

from jmullan.artificer.chomp_python_version import parse_specifier

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FoundVersion:
    file: pathlib.Path
    selector: str
    specifier_set: SpecifierSet
    original_string: str


def deep_get(data: Any, variable: str):
    """>>> deep_get({"a": {"b": "c"}}, "a.b")
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
                logger.warning("%s not in %s at %s", variable, data, consumed)
                return None
    return remaining


def toml_var(filename: str | pathlib.Path, variable: str) -> Any:
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return toml_var(pathlib.Path(filename), variable)
    if not filename.exists():
        raise FileNotFoundError(f"{filename} does not exist")
    with open(filename, "rb") as f:
        data = tomllib.load(f)
        return deep_get(data, variable)


def yaml_var(filename: str | pathlib.Path, variable: str) -> Any:
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return yaml_var(pathlib.Path(filename), variable)
    if not filename.exists():
        raise FileNotFoundError(f"{filename} does not exist")
    with open(filename, "rb") as f:
        data = yaml.safe_load(f)
        return deep_get(data, variable)


def find_up(filename: str | pathlib.Path) -> pathlib.Path | None:
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


def find_toml_version(path: pathlib.Path, selector: str) -> FoundVersion | None:
    value = toml_var(path, selector)
    if value is None:
        return None
    specifier = parse_specifier(value)
    if specifier:
        return FoundVersion(path, selector, specifier, value)
    return None


def find_yaml_version(path: pathlib.Path, selector: str) -> FoundVersion | None:
    value = yaml_var(path, selector)
    if value is None:
        return None
    specifier = parse_specifier(value)
    if specifier:
        return FoundVersion(path, selector, specifier, value)
    return None


def extract_versions(found_versions: list[FoundVersion]) -> set[str]:
    if found_versions is None:
        return set()
    versions: set[str] = set()
    for found_version in found_versions:
        for specifier in found_version.specifier_set._specs:
            if isinstance(specifier, Specifier):
                canonical_spec = specifier._canonical_spec
                if canonical_spec:
                    version = canonical_spec[1]
                    if versions is not None:
                        versions.add(version)
    return versions


def run(*args: str, cwd: pathlib.Path | None = None) -> list[str]:
    if (len(args)) == 1 and " " in args[0]:
        return run(*(args[0].split(" ")), cwd=cwd)
    logger.debug("Running %s", " ".join(args))
    with subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd) as proc:
        if proc.stdout is not None:
            return proc.stdout.read().decode("UTF8").strip().split("\n")
    return []


def find_dockerfiles(in_dir: pathlib.Path) -> set[pathlib.Path]:
    ignored_files = find_ignored_files(in_dir)
    dockerfiles = [p for p in in_dir.rglob("Dockerfile*") if p.is_file()]
    found = {p.resolve() for p in dockerfiles}
    ignored = {p.resolve() for p in ignored_files}
    return found - ignored


def find_ignored_files(in_dir: pathlib.Path) -> set[pathlib.Path]:
    command = ("git", "ls-files", "--others", "-i", "--exclude-standard")
    files = run(*command, cwd=in_dir)
    return set([in_dir / file_name for file_name in files if file_name is not None])


class Main(cmd.Main):
    """Check a harbor registry for docker images."""

    def __init__(self):
        super().__init__()
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument(
            "--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version"
        )
        ordering.add_argument("--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")

    def setup(self) -> None:
        """Do something after parsing args but before main."""
        super().setup()
        if self.args.verbose:
            easy_logging.easy_initialize_logging("DEBUG")
        else:
            easy_logging.easy_initialize_logging("INFO")

    def main(self) -> None:
        """Look in harbor registry for docker images."""
        super().main()

        found_versions: list[FoundVersion] = []
        pyproject_path = find_up("pyproject.toml")
        if pyproject_path is not None and pyproject_path.exists():
            # version ranging
            pyproject_selectors = [
                "project.requires-python",
                "tool.poetry.dependencies.python",
                "tool.mypy.python_version",
                "tool.black.target-version",
                "tool.ruff.target-version",
            ]
            for selector in pyproject_selectors:
                found_version = find_toml_version(pyproject_path, selector)
                if found_version is not None:
                    found_versions.append(found_version)
        pre_commit_yaml_path = find_up(".pre-commit-config.yaml")
        if pre_commit_yaml_path is not None and pre_commit_yaml_path.exists():
            found_version = find_yaml_version(pre_commit_yaml_path, "default_language_version.python")
            if found_version:
                found_versions.append(found_version)

        dot_venv = find_up(".venv")
        if dot_venv is not None and dot_venv.is_file():
            with dot_venv.open("r") as handle:
                for line in handle:
                    specifier = parse_specifier(line)
                    if specifier:
                        found_version = FoundVersion(dot_venv, ".", specifier, line.strip())
                        found_versions.append(found_version)
        dot_git = find_up(".git")
        if dot_git is not None and dot_git.is_dir():
            dockerfiles = find_dockerfiles(dot_git.parent)
        else:
            dockerfiles = find_dockerfiles(pathlib.Path.cwd())
        if dockerfiles:
            for dockerfile in dockerfiles:
                with dockerfile.open("r") as handle:
                    for line in handle:
                        matches = re.match(r"FROM.*(python[.0-9]+)", line.strip())
                        if matches:
                            specifier = parse_specifier(matches.group(1))
                            if specifier:
                                found_version = FoundVersion(dockerfile, "FROM", specifier, line.strip())
                                found_versions.append(found_version)
                                continue
                        matches = re.match(r"FROM.*python[^:]*:([.0-9]+)", line.strip())
                        if matches:
                            specifier = parse_specifier(matches.group(1))
                            if specifier:
                                found_version = FoundVersion(dockerfile, "FROM", specifier, line.strip())
                                found_versions.append(found_version)
                                continue
        possible_versions = extract_versions(found_versions)
        iterable = possible_versions
        specifier_sets = [found_version.specifier_set for found_version in found_versions]
        for specifier_set in specifier_sets:
            logger.debug("Will apply %s", specifier_set)
            for version in possible_versions:
                logger.debug("%s in %s == %s", version, specifier_set, specifier_set.contains(version))
            iterable = specifier_set.filter(iterable)
        filtered = list(iterable)
        if not filtered:
            logger.info("No versions found from %s", possible_versions)
            dump_versions(found_versions)
            sys.exit(1)
        if len(filtered) == 1:
            logger.debug("Only one version available from %s", possible_versions)
            print(filtered[0])
        else:
            sorted_versions = sorted(filtered, key=Version)
            logger.debug("Picking %s of %s", self.args.pick, len(sorted_versions))
            if self.args.pick == "max":
                print(sorted_versions[-1])
            else:
                print(sorted_versions[0])
        if self.args.verbose:
            dump_versions(found_versions)


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


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
