"""Turn a vague python version string into something concrete."""

import logging
import sys

from jmullan.cmd import cmd

from jmullan.artificer import python_version

logger = logging.getLogger(__name__)


class Main(cmd.Main):
    """Figure out a version based on restrictions and available python versions."""

    def __init__(self):
        super().__init__()
        self.parser.add_argument("restriction", help="Figure out a version from this string")
        ordering = self.parser.add_mutually_exclusive_group()
        ordering.add_argument(
            "--max", dest="pick", action="store_const", default="max", const="max", help="Pick Maximum version"
        )
        ordering.add_argument("--min", dest="pick", action="store_const", const="min", help="Pick Minimum version")

    def main(self) -> None:
        """Turn a python version string into a reasonable python version."""
        super().main()
        if self.args.restriction is None:
            self.parser.print_usage()
            sys.exit(1)

        restriction = self.args.restriction.strip()
        if not len(restriction):
            self.parser.print_usage()
            sys.exit(1)
        matching_version = python_version.get_matching_python_version(restriction, pick=self.args.pick)
        if matching_version is not None:
            print(matching_version)
            sys.exit(0)
        sys.exit(1)


def main() -> None:
    """Turn a python version string into a reasonable python version."""
    Main().main()


if __name__ == "__main__":
    main()
