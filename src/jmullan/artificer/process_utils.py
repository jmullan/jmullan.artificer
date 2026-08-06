"""Utility functions for running processes."""

import logging
import pathlib
import subprocess

logger = logging.getLogger(__name__)


def run(*args: str, cwd: pathlib.Path | None = None) -> list[str]:
    """Run a command and return the output as a list."""
    if (len(args)) == 1 and " " in args[0]:
        return run(*(args[0].split(" ")), cwd=cwd)
    logger.debug("Running %s", " ".join(args))
    with subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd) as proc:  # noqa: S603
        if proc.stdout is not None:
            return proc.stdout.read().decode("UTF8").strip().split("\n")
    return []
