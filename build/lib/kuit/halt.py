import inspect
import linecache
import sys
from functools import cache
from pathlib import Path

from rich.console import Console
from rich.pretty import traverse
from rich.traceback import Frame, Stack, Trace, Traceback

LOCALS_MAX_LENGTH = 10
LOCALS_MAX_STRING = 80


@cache
def get_basename(value: str) -> str:
    return str(Path(value).resolve()).rsplit(".", 1)[0]


def capture_frames() -> list[Frame]:
    chain = []
    helper = inspect.currentframe()
    frame = helper.f_back if helper is not None else None  # this module
    del helper
    while frame is not None:
        chain.append(frame)
        frame = frame.f_back
    chain.reverse()

    frames = []
    self_name = get_basename(__file__)
    for current in chain:
        filename = current.f_code.co_filename
        if (
            filename.startswith("<frozen importlib")
            or get_basename(filename) == self_name
        ):
            continue
        locals_ = None
        if filename != __file__:
            locals_ = {
                key: traverse(
                    value,
                    max_length=LOCALS_MAX_LENGTH,
                    max_string=LOCALS_MAX_STRING,
                )
                for key, value in current.f_locals.items()
                if not key.startswith("__")
            } or None
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


def here(value: int = 127) -> None:
    console = Console(stderr=True)
    console.print(
        Traceback(
            trace=Trace(
                stacks=[
                    Stack(
                        exc_type="halt",
                        exc_value="manual halt reached",
                        frames=capture_frames(),
                    ),
                ],
            ),
            show_locals=True,
        ),
    )
    sys.exit(value)
