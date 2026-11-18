"""Shared test helpers: import the package from src/ without installing, run a main() and capture output."""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "basic"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def run(main, *argv):
    """-> (exit code, stdout, stderr) of main(argv)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:  # argparse errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def fixture_copy():
    """A throwaway copy of the fixture project, for tests that edit it."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "basic"
        shutil.copytree(FIXTURE, dst)
        yield dst
