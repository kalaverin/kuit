"""Debug tripwire: importing this module logs the stack trace and exits.

Drop ``import kuit.halt`` anywhere to find out WHO reaches that import and
WHEN: a FATAL record goes to the logging stream, rich renders the full
import-time call stack to stderr (with locals, import machinery hidden),
and the process exits with 127. Debug-only feature — the heavy rich import
on the halt path is intentional.
"""

import inspect
import linecache
import sys
from datetime import UTC, datetime
from logging import getLogger

from rich.console import Console
from rich.panel import Panel
from rich.pretty import traverse
from rich.traceback import Frame, Stack, Trace, Traceback

getLogger(__name__).fatal("halt")

_EXIT_CODE = 127

_LOCALS_MAX_LENGTH = 10
_LOCALS_MAX_STRING = 80


def _capture_frames() -> list[Frame]:
    """Build rich frames from the live call stack (outermost first).

    The import machinery (frozen importlib frames) is hidden: it is pure
    noise between the importer and this module. This module's own locals
    are dropped for the same reason.
    """
    chain = []
    helper = inspect.currentframe()
    frame = helper.f_back if helper is not None else None  # this module
    del helper
    while frame is not None:
        chain.append(frame)
        frame = frame.f_back
    chain.reverse()

    frames = []
    for current in chain:
        filename = current.f_code.co_filename
        if filename.startswith("<frozen importlib"):
            continue
        locals_ = None
        if filename != __file__:
            locals_ = (
                {
                    key: traverse(
                        value,
                        max_length=_LOCALS_MAX_LENGTH,
                        max_string=_LOCALS_MAX_STRING,
                    )
                    for key, value in current.f_locals.items()
                    if not key.startswith("__")
                }
                or None
            )
        frames.append(
            Frame(
                filename=filename,
                lineno=current.f_lineno,
                name=current.f_code.co_name,
                line=linecache.getline(filename, current.f_lineno).strip(),
                locals=locals_,
            ),
        )
    return frames


now = datetime.now(UTC).isoformat(timespec="milliseconds")
console = Console(stderr=True)
console.print(
    Panel(
        f"triggered at {now}\nexiting with code {_EXIT_CODE}",
        title="[bold red]HALT[/]",
        border_style="red",
    ),
)
console.print(
    Traceback(
        trace=Trace(
            stacks=[
                Stack(
                    exc_type="halt",
                    exc_value="import kuit.halt reached",
                    frames=_capture_frames(),
                ),
            ],
        ),
        show_locals=True,
    ),
)

sys.exit(_EXIT_CODE)
