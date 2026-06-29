"""Look in various files to guess the desired python version."""

import logging
import pathlib
import re
import sys
from configparser import ConfigParser

import yaml
from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from jmullan.cmd import cmd
from jmullan.logging import easy_logging, formatters

from jmullan.artificer import utils
from jmullan.artificer.chomp_python_version import (
    PythonVersion,
    find_python_toml_version,
    get_matching_python_versions,
    parse_python_specifier,
)

logger = logging.getLogger(__name__)


def find_yaml_version(path: pathlib.Path, selector: str) -> utils.FoundVersion | None:
    """Extract a version from a YAML file."""
    value = utils.yaml_var(path, selector)
    if value is None:
        return None
    specifier = parse_python_specifier(value)
    if specifier:
        return utils.FoundVersion(path, selector, specifier, value)
    return None


def extract_versions(found_versions: list[utils.FoundVersion]) -> set[str]:
    """Guess what versions would match a set of specifications."""
    if found_versions is None:
        return set()
    versions: set[str] = set()
    for found_version in found_versions:
        for specifier in found_version.specifier_set:
            if isinstance(specifier, Specifier):
                if specifier.operator == "===":
                    versions.add(specifier.version)
                matching_versions = get_matching_python_versions(str(specifier))
                if matching_versions is not None:
                    versions.update(matching_versions)
    return versions


def find_pyproject_versions() -> list[utils.FoundVersion]:
    """Look for versions in pyproject versions."""
    found_versions: list[utils.FoundVersion] = []
    pyproject_path = utils.find_up("pyproject.toml")
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
            found_version = find_python_toml_version(pyproject_path, selector)
            if found_version is not None:
                found_versions.append(found_version)
    pre_commit_yaml_path = utils.find_up(".pre-commit-config.yaml")
    if pre_commit_yaml_path is not None and pre_commit_yaml_path.exists():
        logger.debug("Found pre-commit config")
        found_version = find_yaml_version(pre_commit_yaml_path, "default_language_version.python")
        if found_version:
            found_versions.append(found_version)
    return found_versions


def find_dot_venv_versions() -> list[utils.FoundVersion]:
    """Look for python versions in .venv files."""
    found_versions: list[utils.FoundVersion] = []
    dot_venv = utils.find_up(".venv")
    if dot_venv is not None and dot_venv.is_file():
        logger.debug("Found .venv")
        with dot_venv.open("r") as handle:
            for line in handle:
                specifier = parse_python_specifier(line)
                if specifier:
                    found_version = utils.FoundVersion(dot_venv, ".", specifier, line.strip())
                    found_versions.append(found_version)
    return found_versions


def find_dot_python_version() -> list[utils.FoundVersion]:
    """Look for python versions in .venv files."""
    found_versions: list[utils.FoundVersion] = []
    dot_python_version = utils.find_up(".python-version")
    if dot_python_version is not None and dot_python_version.is_file():
        logger.debug("Found .python-version")
        with dot_python_version.open("r") as handle:
            for line in handle:
                specifier = parse_python_specifier(line)
                if specifier:
                    found_version = utils.FoundVersion(dot_python_version, ".", specifier, line.strip())
                    found_versions.append(found_version)
    return found_versions


def find_runtime_txt_version() -> list[utils.FoundVersion]:
    """Look for python versions in .venv files."""
    found_versions: list[utils.FoundVersion] = []
    runtime_txt_file = utils.find_up("runtime.txt")
    if runtime_txt_file is not None and runtime_txt_file.is_file():
        logger.debug("Found runtime.txt")
        with runtime_txt_file.open("r") as handle:
            for line in handle:
                specifier = parse_python_specifier(line)
                if specifier:
                    found_version = utils.FoundVersion(runtime_txt_file, ".", specifier, line.strip())
                    found_versions.append(found_version)
    return found_versions


def find_github_action_python_versions() -> list[utils.FoundVersion]:
    """Look for python versions in .venv files."""
    found_versions: list[utils.FoundVersion] = []
    dot_github = utils.find_up(".github")
    python_versions = []
    if dot_github is not None and dot_github.exists():
        for workflow_yaml in dot_github.glob("workflows/*.yml"):
            with workflow_yaml.open("rb") as f:
                documents = yaml.safe_load_all(f)
                for index, document in enumerate(documents):
                    python_versions.extend(utils.rglob_var(document, "python_version"))
                    python_versions.extend(utils.rglob_var(document, "python-version"))
                    python_versions.extend(utils.rglob_var(document, "PYTHON_VERSION"))
                    for python_version in python_versions:
                        specifier = parse_python_specifier(python_version)
                        if specifier:
                            found_version = utils.FoundVersion(
                                workflow_yaml, f"Document {index}", specifier, python_version
                            )
                            found_versions.append(found_version)
    return found_versions


def find_dockerfile_versions() -> list[utils.FoundVersion]:
    """Look for python versions in Dockerfiles."""
    found_versions: list[utils.FoundVersion] = []
    dot_git = utils.find_up(".git")
    if dot_git is not None and dot_git.is_dir():
        dockerfiles = utils.find_dockerfiles(dot_git.parent)
    else:
        dockerfiles = utils.find_dockerfiles(pathlib.Path.cwd())
    if dockerfiles:
        for dockerfile in dockerfiles:
            logger.debug("Found dockerfile %s", dockerfile)
            with dockerfile.open("r") as handle:
                for line in handle:
                    matches = re.match(r"FROM.*(python[.0-9]+)", line.strip())
                    if matches:
                        specifier = parse_python_specifier(matches.group(1))
                        if specifier:
                            found_version = utils.FoundVersion(dockerfile, "FROM", specifier, line.strip())
                            found_versions.append(found_version)
                            continue
                    matches = re.match(r"FROM.*python[^:]*:([.0-9]+)", line.strip())
                    if matches:
                        specifier = parse_python_specifier(matches.group(1))
                        if specifier:
                            found_version = utils.FoundVersion(dockerfile, "FROM", specifier, line.strip())
                            found_versions.append(found_version)
                            continue
    return found_versions


def find_tox_versions() -> list[utils.FoundVersion]:
    """Look for tox versions in tox.ini files."""
    pythons: list[PythonVersion] = []
    tox_ini_file = utils.find_up("tox.ini")
    if tox_ini_file is not None and tox_ini_file.is_file():
        logger.debug("Found tox.ini")
        cfg = ConfigParser()
        cfg.read(tox_ini_file)
        tox_section = cfg["tox"]
        envlist = tox_section["envlist"]
        for env in envlist.split("\n"):
            matches = re.match(r"py(?P<major>[0-9])(?P<minor>[0-9]+)", env)
            if matches:
                major = matches.group("major")
                minor = matches.group("minor")
                version = f"{major}.{minor}"
                python_version = PythonVersion.from_version(version)
                if python_version:
                    pythons.append(python_version)
    else:
        return []
    if not pythons:
        return []
    pythons.sort(key=lambda p: p.class_major)

    python_ranges: list[list[PythonVersion]] = []
    python_range: list[PythonVersion] = []
    last_python_version = None
    for python in pythons:
        if last_python_version is None or python.class_major != last_python_version.class_major + 1:
            python_range = [python]
            python_ranges.append(python_range)
        else:
            python_range.append(python)
        last_python_version = python
    found_versions = []
    for python_range in python_ranges:
        if len(python_range) == 1:
            python = python_range[0]
            specifier = parse_python_specifier(python.specifier)
            if specifier:
                found_version = utils.FoundVersion(tox_ini_file, ".tox.envlist", specifier, python.specifier)
                found_versions.append(found_version)
        else:
            min_python = python_range[0]
            max_python = python_range[-1]
            next_python = max_python.next_version
            if next_python is not None:
                specifier_string = f">={min_python.major_minor_patch},<={next_python.major_minor_patch}"
            else:
                specifier_string = f">={min_python.major_minor_patch},<={max_python.major_minor_patch}"
            specifier = SpecifierSet(specifier_string)
            found_version = utils.FoundVersion(tox_ini_file, ".tox.envlist", specifier, envlist)
            found_versions.append(found_version)
    return found_versions


def find_pyvenv_cfg() -> list[utils.FoundVersion]:
    """Look for tox versions in tox.ini files."""
    pythons: list[PythonVersion] = []
    pyvenv_cfg = utils.find_up("pyvenv.cfg")
    if pyvenv_cfg is not None and pyvenv_cfg.is_file():
        logger.debug("Found pyvenv.cfg")
        cfg = ConfigParser()
        cfg.read(pyvenv_cfg)
        tox_section = cfg["tox"]
        envlist = tox_section["envlist"]
        for env in envlist.split("\n"):
            matches = re.match(r"py(?P<major>[0-9])(?P<minor>[0-9]+)", env)
            if matches:
                major = matches.group("major")
                minor = matches.group("minor")
                version = f"{major}.{minor}"
                python_version = PythonVersion.from_version(version)
                if python_version:
                    pythons.append(python_version)
    else:
        return []
    if not pythons:
        return []
    pythons.sort(key=lambda p: p.class_major)

    python_ranges: list[list[PythonVersion]] = []
    python_range: list[PythonVersion] = []
    last_python_version = None
    for python in pythons:
        if last_python_version is None or python.class_major != last_python_version.class_major + 1:
            python_range = [python]
            python_ranges.append(python_range)
        else:
            python_range.append(python)
        last_python_version = python
    found_versions = []
    for python_range in python_ranges:
        if len(python_range) == 1:
            python = python_range[0]
            specifier = parse_python_specifier(python.specifier)
            if specifier:
                found_version = utils.FoundVersion(pyvenv_cfg, ".tox.envlist", specifier, python.specifier)
                found_versions.append(found_version)
        else:
            min_python = python_range[0]
            max_python = python_range[-1]
            next_python = max_python.next_version
            if next_python is not None:
                specifier_string = f">={min_python.major_minor_patch},<={next_python.major_minor_patch}"
            else:
                specifier_string = f">={min_python.major_minor_patch},<={max_python.major_minor_patch}"
            specifier = SpecifierSet(specifier_string)
            found_version = utils.FoundVersion(pyvenv_cfg, ".tox.envlist", specifier, envlist)
            found_versions.append(found_version)
    return found_versions


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
            easy_logging.easy_initialize_logging("DEBUG", formatter=formatters.ConsoleFormatter())
        else:
            easy_logging.easy_initialize_logging("INFO")

    def main(self) -> None:
        """Look in harbor registry for docker images."""
        super().main()

        found_versions: list[utils.FoundVersion] = find_pyproject_versions()
        found_versions.extend(find_dot_venv_versions())
        found_versions.extend(find_dockerfile_versions())
        found_versions.extend(find_dot_python_version())
        found_versions.extend(find_runtime_txt_version())
        found_versions.extend(find_github_action_python_versions())
        found_versions.extend(find_tox_versions())
        found_versions.extend(find_pyvenv_cfg())

        possible_versions = extract_versions(found_versions)
        iterable = possible_versions

        for found_version in found_versions:
            specifier_set = found_version.specifier_set
            logger.debug("Will apply %s", specifier_set)
            for version in possible_versions:
                logger.debug("%s in %s == %s", version, specifier_set, specifier_set.contains(version))
            iterable = specifier_set.filter(iterable)
        filtered = list(iterable)
        if not filtered:
            logger.info("No versions found from %s", possible_versions)
            utils.dump_versions(found_versions)
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
            utils.dump_versions(found_versions)


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
