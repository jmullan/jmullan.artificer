"""Examine a file and present information about it."""

import logging
import pathlib
import sys

from jmullan.cmd import cmd
from jmullan.logging import formatters
from jmullan.logging.easy_logging import easy_initialize_logging

from jmullan.artificer import file_utils

logger = logging.getLogger(__name__)


class Main(cmd.Main):
    """Print a bunch of stuff that was done on a particular day."""

    def __init__(self):
        super().__init__()
        cwd_as_dot = "."
        self.parser.add_argument(
            "paths",
            metavar="PATH",
            nargs="*",
            type=pathlib.Path,
            default=[pathlib.Path(cwd_as_dot)],
            help="Where to look for files",
        )
        self.parser.add_argument(
            "--globs",
            dest="globs",
            metavar="INCLUDE",
            nargs="*",
            default=[],
            type=str,
            help='Files matching these globs (like "*.py", "**/*.py"',
        )
        self.parser.add_argument(
            "--path-regex",
            dest="regexes",
            metavar="REGEX",
            nargs="*",
            default=[".*"],
            type=str,
            help=r'File paths matching these regexes (like ".*/.*\.py")',
        )
        self.parser.add_argument(
            "--file-types",
            dest="file_types",
            metavar="FILE_TYPES",
            nargs="*",
            default=["utf8"],
            type=str,
            help='File types to include (like "text", "python")',
        )

    def setup(self) -> None:
        """Set up logging."""
        super().setup()
        if self.args.verbose:
            easy_initialize_logging("DEBUG", stream=sys.stdout, formatter=formatters.PlainTextFormatter())
        else:
            easy_initialize_logging("INFO", stream=sys.stdout, formatter=formatters.PlainTextFormatter())

    def main(self) -> None:
        """Examine a file and present information about it."""
        super().main()
        paths: list[pathlib.Path] = self.args.paths

        resolved = file_utils.resolve_paths(paths)

        for file_path in file_utils.find_files(
            list(resolved.values()), self.args.globs, self.args.regexes, self.args.file_types
        ):
            print(file_path)


if __name__ == "__main__":
    Main().main()
