"""
Example:
    >>> import kuit
    >>> @kuit.register
    ... def cleanup():
    ...     print("Shutting down...")

    >>> # In a daemon/main loop, check for file changes every 2.5s
    >>> checker = kuit.on(signal=signal.SIGUSR1)  # Also exit on SIGUSR1
    >>> while checker.sleep(60):  # Returns False when exiting
    ...     pass
"""
# ruff: noqa: ANN401

import atexit
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from logging import getLogger
from pathlib import Path
from signal import signal as bind
from types import FrameType, TracebackType
from typing import Any, Protocol, cast

from kain import Who
from kain.classes import Singleton

__all__ = (
    "on_exception",
    "on",
    "register",
)

logger = getLogger(__name__)

#: Global flag set to ``True`` when a signal handler requests a restart.
#: Used by ``on`` to detect external restart requests.
NeedRestart: bool = False

#: Type for values accepted and returned by ``signal.signal``.
_SignalHandler = (
    Callable[[int, FrameType | None], Any]
    | signal.Handlers
    | int
    | None
)


class _OnChangeCallable(Protocol):
    def __call__(self, *, sleep: float = 0.0) -> bool: ...

    #: Callable that sleeps while periodically checking for changes.
    sleep: Callable[[float, float], bool]


class OnQuit(metaclass=Singleton):
    def __init__(self) -> None:
        #: List of no-argument functions to call during teardown.
        self.callbacks: list[Callable[[], Any]] = []

        #: List of exception hook functions with signature
        #: ``(exc_type, exc_value, traceback) -> Any``.
        self.hooks_chain: list[
            Callable[
                [type[BaseException], BaseException, TracebackType | None],
                Any,
            ],
        ] = []

        #: Saved reference to the original ``sys.excepthook``.
        self.original_hook: Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ] = sys.excepthook

        #: Guard to ensure teardown runs only once.
        self.already_called: bool = False

        #: Bound method reference for use as ``sys.excepthook`` replacement.
        self._proxy: Callable[..., Any] = self._exceptions_hook

        #: Whether global hooks and handlers have been installed.
        self._installed: bool = False

        #: Whether :meth:`_teardown` has been registered with ``atexit``.
        self._atexit_registered: bool = False

        #: Original signal handlers captured during installation.
        self._original_sigint: _SignalHandler = None
        self._original_sigterm: _SignalHandler = None
        self._original_sigquit: _SignalHandler = None

    def _setup(self) -> None:
        if self._installed:
            return
        self._installed = True
        self.inject_hook()
        self._inject_handler()
        self._inject_threading_hook()

    def _ensure_atexit(self) -> None:
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self._teardown)

    def inject_hook(self) -> None:
        sys.excepthook = self._proxy

    def _exceptions_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        self._setup()
        if sys.excepthook is not self._proxy:
            self.hooks_chain.append(sys.excepthook)
            self.inject_hook()

        for hook in (*self.hooks_chain, self.original_hook):
            try:
                hook(exc_type, exc_value, traceback)
            except Exception as e:
                logger.exception(
                    "Exception hook %s failed",
                    Who.Is(hook),
                    exc_info=e,
                )

        self._teardown()

    def _inject_handler(self) -> None:
        self._original_sigint = bind(signal.SIGINT, self._exit)
        self._original_sigterm = bind(signal.SIGTERM, self._exit)
        if hasattr(signal, "SIGQUIT"):
            self._original_sigquit = bind(signal.SIGQUIT, self._exit)

    def _exit(self, _signum: int, _frame: FrameType | None) -> None:
        self._setup()
        self._teardown()
        sys.exit(1)

    def _inject_threading_hook(self) -> None:
        """Replace ``threading.excepthook`` with our proxy."""
        threading.excepthook = self.threading_handler

    def threading_handler(self, args: threading.ExceptHookArgs) -> None:
        self._setup()
        if args.exc_type is None or args.exc_type is SystemExit:  # type: ignore[redundant-expr]  # pyright: ignore[reportUnnecessaryComparison]
            return

        if args.exc_value is None:
            return
        self._proxy(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    def _restore_original_handlers(self) -> None:
        if self._installed:
            bind(signal.SIGINT, self._original_sigint)
            bind(signal.SIGTERM, self._original_sigterm)
            if hasattr(signal, "SIGQUIT"):
                bind(signal.SIGQUIT, self._original_sigquit)

        sys.excepthook = self.original_hook
        threading.excepthook = threading.__excepthook__  # type: ignore[attr-defined]

    def quit(self, func: Callable[[], Any]) -> None:
        self._setup()
        self._ensure_atexit()
        self.callbacks.append(func)

    def on_exception(
        self,
        func: Callable[
            [type[BaseException], BaseException, TracebackType | None],
            Any,
        ],
    ) -> None:
        self.hooks_chain.append(func)

    def _teardown(self) -> None:
        self._setup()
        if self.already_called:
            return

        try:
            for func in self.callbacks:
                try:
                    func()
                except BaseException as e:
                    logger.exception(
                        "Teardown callback %s failed",
                        Who.Is(func),
                        exc_info=e,
                    )
        finally:
            self.already_called = True
            self._restore_original_handlers()


@cache
def get_selfpath() -> Path:
    return Path(sys.argv[0]).resolve()


def get_mtime() -> float:
    return get_selfpath().stat().st_mtime


@cache
def on(
    *,
    func: Callable[..., Any] = sys.exit,
    signal: int = 0,
    errno: int = 137,
    **kw: Any,
) -> _OnChangeCallable:
    def handler(*_: Any) -> None:
        global NeedRestart  # noqa: PLW0603
        NeedRestart = True
        logger.warning(f"{signal=} received")

    if signal:
        bind(signal, handler)

    # Snapshot the mtime at construction time; future comparisons use this.
    initial_stamp = get_mtime()

    def on_change(*, sleep: float = 0.0) -> bool:
        if NeedRestart and signal:
            logger.warning(f"stop by {signal=}")
            func(errno)
            return False

        try:
            if initial_stamp != (ctime := get_mtime()):
                file = str(get_selfpath())
                when = datetime.fromtimestamp(ctime, tz=UTC)
                logger.warning(
                    f"{file=} updated at {when} "
                    f"({time.time() - ctime:.2f}s ago), stop",
                )
                func(errno)
                return False

        except FileNotFoundError:
            logger.warning(f"{get_selfpath()} removed? stop")
            return False

        if sleep := (sleep or kw.get("sleep", 0.0)):
            time.sleep(sleep)

        return True

    def sleep(wait: float = 0.0, /, poll: float = 0.0) -> bool:
        if not wait:
            return True

        poll = poll or kw.get("poll", 2.5)
        deadline = time.time() + wait

        while (solution := on_change()) and time.time() < deadline:
            time.sleep(poll)
        return solution

    on_change.sleep = sleep  # type: ignore[attr-defined]  # pyright: ignore[reportFunctionMemberAccess]
    return on_change  # type: ignore[return-value]  # pyright: ignore[reportReturnType]


registry = cast("OnQuit", OnQuit())
register = registry.quit
on_exception = registry.on_exception
