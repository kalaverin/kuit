from collections.abc import Callable
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Protocol

from kain.classes import Singleton

__all__ = ("add_hook", "on", "register")

NeedRestart: bool

class _OnChangeCallable(Protocol):
    def __call__(self, *, sleep: float = 0.0) -> bool: ...
    sleep: Callable[[float, float], bool]

class OnQuit(metaclass=Singleton):
    callbacks: list[Callable[[], Any]]
    hooks_chain: list[
        Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ],
    ]
    original_hook: Callable[
        [type[BaseException], BaseException, TracebackType | None],
        Any,
    ]
    already_called: bool
    _proxy: Callable[..., Any]
    _installed: bool
    _atexit_registered: bool
    _original_sigint: Callable[..., Any] | None
    _original_sigterm: Callable[..., Any] | None
    _original_sigquit: Callable[..., Any] | None
    instance: Any

    def __init__(self) -> None: ...
    def _setup(self) -> None: ...
    def _ensure_atexit(self) -> None: ...
    def inject_hook(self) -> None: ...
    def _exceptions_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType | None,
    ) -> None: ...
    def _inject_handler(self) -> None: ...
    def _exit(
        self,
        _signum: int,
        _frame: FrameType | None,
    ) -> None: ...
    def _inject_threading_hook(self) -> None: ...
    def threading_handler(self, args: Any) -> None: ...
    def _restore_original_handlers(self) -> None: ...
    def quit(self, func: Callable[[], Any]) -> None: ...
    def add_hook(
        self,
        func: Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ],
    ) -> None: ...
    def _teardown(self) -> None: ...

def register(func: Callable[[], Any]) -> None: ...
def add_hook(
    func: Callable[
        [type[BaseException], BaseException, TracebackType | None],
        Any,
    ],
) -> None: ...
def get_selfpath() -> Path: ...
def get_mtime() -> float: ...
def on(
    *,
    func: Callable[..., Any] = ...,
    signal: int = 0,
    errno: int = 137,
    **kw: Any,
) -> _OnChangeCallable: ...

registry: OnQuit
