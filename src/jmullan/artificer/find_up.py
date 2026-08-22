"""The main command-line entrypoint."""

import logging
import pathlib
import sys

from jmullan.cmd import cmd
from jmullan.logging import easy_logging

from jmullan.artificer.utils import find_up_many

logger = logging.getLogger(__name__)

REPOS = {"snapshots": "repositories/snapshots", "releases": "repositories/releases", "public": "groups/public"}
OK_RESPONSE_CODE = 200
ERROR_RESPONSE_CODE_THRESHOLD = 400


class Main(cmd.Main):
    """Interact with sonatype nexus."""

    def __init__(self) -> None:
        super().__init__()
        self.parser.add_argument(
            "--in-dir",
            dest="in_dirs",
            type=pathlib.Path,
            default=[],
            action="append",
            help="Look in this directory",
        )
        self.parser.add_argument(
            "filenames",
            nargs="+",
            help="Look for this file or files",
        )
        self.parser.add_argument(
            "--limit",
            dest="limit",
            default=1,
            type=int,
            help="Output this many results",
        )
        self.parser.add_argument(
            "--absolute",
            dest="absolute",
            action="store_true",
            default=False,
            help="Output absolute paths instead of relative paths",
        )

    def setup(self) -> None:
        """Do something after parsing args but before main."""
        super().setup()
        if self.args.verbose:
            easy_logging.easy_initialize_logging("DEBUG", stream=sys.stderr)
        else:
            easy_logging.easy_initialize_logging(stream=sys.stderr)

    def main(self) -> None:
        """Interact with sonatype nexus."""
        super().main()
        in_dirs = self.args.in_dirs or [pathlib.Path().cwd()]
        filenames = self.args.filenames
        limit = self.args.limit
        if limit < 1:
            raise ValueError("--limit must be greater than 0")
        found = 0
        for found_file in find_up_many(in_dirs, filenames, absolute=self.args.absolute):
            print(found_file)
            found += 1
            if found == limit:
                break
        if self.args.limit > 1 or found != limit:
            logger.debug("Done finding %s files of %s", found, self.args.limit)


def main() -> None:
    """Run the command via the command-line entrypoint."""
    Main().main()


if __name__ == "__main__":
    main()
