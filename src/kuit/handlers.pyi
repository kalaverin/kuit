from collections.abc import Callable
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Protocol

from kain.classes import Singleton

__all__ = (
    "OnQuit",
    "on",
)


NeedRestart: bool
SignalHandler = (
    Callable[[int, FrameType | None], Any]
    | int
    | None
)


class OnChangeCallable(Protocol):
    def __call__(self, *, sleep: float = 0.0) -> bool: ...
    sleep: Callable[[float, float], bool]


class OnQuit(metaclass=Singleton):
    def __new__(cls, *args: Any, **kw: Any) -> OnQuit: ...
    _callbacks: list[Callable[[], Any]]
    _hooks_chain: list[
        Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ],
    ]
    _original_hook: Callable[
        [type[BaseException], BaseException, TracebackType | None],
        Any,
    ]
    _already_called: bool
    _proxy: Callable[..., Any]
    _installed: bool
    _atexit_registered: bool
    _original_sigint: SignalHandler
    _original_sigterm: SignalHandler
    _original_sigquit: SignalHandler
    instance: Any

    def __init__(self) -> None: ...
    def _exceptions_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType | None,
    ) -> None: ...
    def _threading_handler(self, args: Any) -> None: ...
    def _exit(
        self,
        _signum: int,
        _frame: FrameType | None,
    ) -> None: ...
    def _inject_hook(self) -> None: ...
    def _inject_handler(self) -> None: ...
    def _inject_threading_hook(self) -> None: ...
    def _setup(self) -> None: ...
    def _ensure_atexit(self) -> None: ...
    def on_exit(self, func: Callable[[], Any]) -> None: ...
    def on_exception(
        self,
        func: Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ],
    ) -> None: ...
    def _teardown(self) -> None: ...
    def _restore_original_handlers(self) -> None: ...


def get_selfpath() -> Path: ...
def get_mtime() -> float: ...
def on(
    *,
    func: Callable[..., Any] = ...,
    signal: int = 0,
    errno: int = 137,
    **kw: Any,
) -> OnChangeCallable: ...
