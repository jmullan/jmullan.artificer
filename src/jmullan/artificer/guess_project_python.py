"""Look in harbor registry for current docker image versions."""
import dataclasses
import logging
import pathlib
import re
import tomllib
from typing import Any

import yaml
from jmullan.cmd import cmd
from jmullan.logging import easy_logging
from packaging.specifiers import SpecifierSet, Specifier
from packaging.version import Version

from jmullan.artificer.chomp_python_version import parse_specifier

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FoundVersion:
    file: pathlib.Path
    selector: str
    specifier_set: SpecifierSet


def deep_get(data: Any, variable: str):
    """
    >>> deep_get({"a": {"b": "c"}}, "a.b")
    'c'
    >>> deep_get({"a": {"b": [1, 2, 3]}}, "a.b.0")
    1
    >>> deep_get({"a": {"b": "c"}}, "a.d")  # not found
    >>> deep_get({"a": {"b": [1, 2, 3]}}, "a.b.q")  # not found
    """
    parts = variable.split('.')
    consumed = ""
    remaining = data
    for part in parts:
        if len(consumed):
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
    with open(filename, 'rb') as f:
        data = tomllib.load(f)
        return deep_get(data, variable)


def yaml_var(filename: str | pathlib.Path, variable: str) -> Any:
    if filename is None:
        raise ValueError("filename must not be None")
    if isinstance(filename, str):
        return yaml_var(pathlib.Path(filename), variable)
    if not filename.exists():
        raise FileNotFoundError(f"{filename} does not exist")
    with open(filename, 'rb') as f:
        data = yaml.safe_load(f)
        return deep_get(data, variable)


def find_up(filename: str | pathlib.Path) -> pathlib.Path | None:
    current_dir = pathlib.Path(".").absolute()
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
        return FoundVersion(path, selector, specifier)
    return None

def find_yaml_version(path: pathlib.Path, selector: str) -> FoundVersion | None:
    value = yaml_var(path, selector)
    if value is None:
        return None
    specifier = parse_specifier(value)
    if specifier:
        return FoundVersion(path, selector, specifier)
    return None


def extract_versions(found_versions: list[FoundVersion]) -> set[str]:
    if found_versions is None:
        return set()
    versions = set()
    for found_version in found_versions:
        for specifier in found_version.specifier_set._specs:
            if isinstance(specifier, Specifier):
                canonical_spec = specifier._canonical_spec
                if canonical_spec:
                    version = canonical_spec[1]
                    if versions is not None:
                        versions.add(version)
    return versions


class Main(cmd.Main):
    """Check a harbor registry for docker images."""

    def __init__(self):
        super().__init__()
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument("--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version")
        ordering.add_argument(
            "--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")


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
                "tool.ruff.target-version"
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

        possible_versions = extract_versions(found_versions)
        iterable = possible_versions
        specifier_sets = [found_version.specifier_set for found_version in found_versions]
        for specifier_set in specifier_sets:
            iterable = specifier_set.filter(iterable)
        filtered = list(iterable)
        if not filtered:
            logger.info("No versions found from %s", possible_versions)
            exit(1)
        if len(filtered) == 1:
            print(filtered[0])
        else:
            sorted_versions = sorted(filtered, key=Version)
            if self.args.pick == "max":
                print(sorted_versions[-1])
            else:
                print(sorted_versions[0])


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
