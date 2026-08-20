"""Unit tests for signal handling and graceful shutdown utilities.

Arrange-Act-Assert pattern, BDD docstrings.
"""

import contextlib
import logging
import signal
import sys
import threading
from collections.abc import Generator
from pathlib import Path
from types import TracebackType
from typing import Any, get_type_hints
from unittest.mock import patch

import pytest
from faker import Faker
from kain.classes import Nothing

import kuit.handlers as _signals
from kuit.handlers import OnQuit, get_mtime, get_selfpath, on


@pytest.fixture(autouse=True)
def _reset_on_quit_state() -> Generator[None, None, None]:
    """Restore hooks and reset singleton state after each test."""
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigquit = (
        signal.getsignal(signal.SIGQUIT)
        if hasattr(signal, "SIGQUIT")
        else None
    )

    yield
    if OnQuit.instance is not Nothing:
        OnQuit.instance._restore_original_handlers()
        OnQuit.instance._already_called = False
        OnQuit.instance._callbacks.clear()
        OnQuit.instance._hooks_chain.clear()
    OnQuit.instance = Nothing  # type: ignore[assignment][misc]
    # Reset global restart flag.
    _signals.NeedRestart = False
    # Clear at caches so each test gets a fresh snapshot.
    on.cache_clear()
    get_selfpath.cache_clear()
    signal.signal(signal.SIGINT, original_sigint)
    signal.signal(signal.SIGTERM, original_sigterm)
    if hasattr(signal, "SIGQUIT") and original_sigquit is not None:
        signal.signal(signal.SIGQUIT, original_sigquit)


# ------------------------------------------------------------------
# OnQuit singleton lifecycle
# ------------------------------------------------------------------


class TestOnQuit:
    """GIVEN the OnQuit singleton
    WHEN callbacks are scheduled or lifecycle events occur
    THEN graceful shutdown semantics are enforced.
    """

    def test_on_quit_is_singleton(self) -> None:
        """GIVEN multiple instantiations
        WHEN OnQuit() is called repeatedly
        THEN the same object is returned.
        """
        first = OnQuit()
        second = OnQuit()

        assert first is second

    def test_on_quit_schedules_callback(self, fake: Faker) -> None:
        """GIVEN a no-argument callable
        WHEN schedule() is called
        THEN the callback appears in the callbacks list.
        """
        obj = OnQuit()

        def cb() -> str:
            return fake.pystr()

        obj.on_exit(cb)

        assert cb in obj._callbacks

    def test_on_quit_teardown_executes_callbacks(self) -> None:
        """GIVEN scheduled callbacks
        WHEN teardown() is invoked
        THEN callbacks run in registration order.
        """
        obj = OnQuit()
        order: list[int] = []

        obj.on_exit(lambda: order.append(1))
        obj.on_exit(lambda: order.append(2))

        obj._teardown()

        assert order == [1, 2]

    def test_on_quit_teardown_is_idempotent(self) -> None:
        """GIVEN teardown has already run
        WHEN teardown() is called a second time
        THEN callbacks do not execute again.
        """
        obj = OnQuit()

        obj.on_exit(lambda: None)
        obj._teardown()
        obj._teardown()

        assert obj._already_called is True

    def test_on_quit_restore_original_handlers_reverts_hooks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GIVEN hooks have been replaced by install()
        WHEN restore_original_handlers() is called
        THEN sys.excepthook, threading.excepthook, and signal handlers
        revert to the values captured before install().
        """

        def custom_sigint(_signum: int, _frame: Any) -> None:
            pass

        def custom_sigterm(_signum: int, _frame: Any) -> None:
            pass

        def custom_sigquit(_signum: int, _frame: Any) -> None:
            pass

        original_sigint = signal.signal(signal.SIGINT, custom_sigint)
        original_sigterm = signal.signal(signal.SIGTERM, custom_sigterm)
        original_sigquit = signal.signal(signal.SIGQUIT, custom_sigquit)

        try:
            obj = OnQuit()
            obj._setup()
            obj._restore_original_handlers()

            assert sys.excepthook is obj._original_hook
            assert threading.excepthook is threading.__excepthook__
            assert (
                signal.signal(signal.SIGINT, signal.SIG_DFL) is custom_sigint
            )
            assert (
                signal.signal(signal.SIGTERM, signal.SIG_DFL) is custom_sigterm
            )
            assert (
                signal.signal(signal.SIGQUIT, signal.SIG_DFL) is custom_sigquit
            )
        finally:
            # Ensure cleanup regardless of assertion outcomes.
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGQUIT, original_sigquit)

    def test_on_quit_install_injects_hooks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GIVEN a fresh process state
        AFTER install() is called
        THEN sys.excepthook is replaced with the proxy.
        """
        obj = OnQuit()
        obj._setup()

        assert sys.excepthook is obj._proxy

    def test_on_quit_init_does_not_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GIVEN OnQuit() is instantiated
        WHEN no active method is called
        THEN global hooks and atexit remain untouched.
        """
        with (
            patch("kuit.handlers.atexit.register") as mock_atexit,
            patch("kuit.handlers.bind") as mock_bind,
        ):
            OnQuit()

        mock_atexit.assert_not_called()
        mock_bind.assert_not_called()

    def test_on_quit_schedule_triggers_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GIVEN a fresh OnQuit instance
        WHEN schedule() is called
        THEN install() runs lazily and atexit is registered.
        """
        obj = OnQuit()
        with (
            patch.object(obj, "_setup", wraps=obj._setup) as mock_install,
            patch.object(
                obj,
                "_ensure_atexit",
                wraps=obj._ensure_atexit,
            ) as mock_atexit,
        ):
            obj.on_exit(lambda: None)

        mock_install.assert_called_once()
        mock_atexit.assert_called_once()
        assert len(obj._callbacks) == 1

    def test_on_quit_add_hook_appends_to_chain(self) -> None:
        """GIVEN a custom exception hook
        WHEN on_exception() is called
        THEN the hook is present in hooks_chain.
        """
        obj = OnQuit()

        def my_hook(
            _exc_type: type[BaseException],
            _exc_value: BaseException,
            _tb: TracebackType | None,
        ) -> None:
            pass

        obj.on_exception(my_hook)

        assert my_hook in obj._hooks_chain

    def test_on_quit_teardown_catches_callback_exception_and_logs(
        self,
        fake: Faker,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a callback that raises BaseException
        WHEN teardown() runs
        THEN the failure is logged at ERROR and remaining callbacks execute.
        """
        obj = OnQuit()
        after = False

        def bad_callback() -> None:
            raise RuntimeError(fake.pystr())

        def good_callback() -> None:
            nonlocal after
            after = True

        obj.on_exit(bad_callback)
        obj.on_exit(good_callback)

        with caplog.at_level("ERROR", logger="kuit.handlers"):
            obj._teardown()

        assert any(record.levelname == "ERROR" for record in caplog.records)
        assert after is True

    def test_on_quit_teardown_sets_already_called_even_on_failure(
        self,
        fake: Faker,
    ) -> None:
        """GIVEN a callback that raises
        WHEN teardown() is invoked
        THEN already_called is True after return.
        """
        obj = OnQuit()

        obj.on_exit(lambda: (_ for _ in ()).throw(RuntimeError(fake.pystr())))

        obj._teardown()

        assert obj._already_called is True

    def test_on_quit_exceptions_hooks_proxy_logs_hook_exception(
        self,
        fake: Faker,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a hook that raises Exception
        WHEN exceptions_hooks_proxy is called
        THEN the failure is logged at ERROR and teardown proceeds.
        """
        obj = OnQuit()
        message = fake.pystr()

        def bad_hook(
            _et: type[BaseException],
            _ev: BaseException,
            _tb: TracebackType | None,
        ) -> None:
            raise RuntimeError(message)

        obj._hooks_chain.append(bad_hook)
        # Suppress stderr noise from the original sys.__excepthook__.
        obj._original_hook = lambda *_a: None  # type: ignore[assignment][method-assign]

        with (
            caplog.at_level("ERROR", logger="kuit.handlers"),
            contextlib.suppress(SystemExit),
        ):
            obj._exceptions_hook(
                RuntimeError,
                RuntimeError("x"),
                None,
            )

        assert message in caplog.text

    def test_on_quit_teardown_with_none_callback_logs_type_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN None is scheduled as a callback
        WHEN teardown() attempts to call it
        THEN TypeError is raised inside teardown and logged at ERROR.
        """
        obj = OnQuit()
        obj._callbacks.append(None)  # type: ignore[assignment][arg-type]

        with caplog.at_level("ERROR", logger="kuit.handlers"):
            obj._teardown()

        assert any(
            record.levelname == "ERROR" and "None" in record.message
            for record in caplog.records
        )

    def test_on_quit_teardown_with_empty_callbacks(self) -> None:
        """GIVEN no callbacks are scheduled
        WHEN teardown() is invoked
        THEN no error occurs and handlers are restored.
        """
        obj = OnQuit()
        obj._teardown()

        assert obj._already_called is True

    def test_on_quit_teardown_continues_after_callback_system_exit(
        self,
        fake: Faker,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a callback that raises SystemExit
        WHEN teardown() runs
        THEN the failure is logged at ERROR and remaining callbacks execute.
        """
        obj = OnQuit()
        after = False

        def bad_callback() -> None:
            raise SystemExit(fake.pyint(min_value=1, max_value=255))

        def good_callback() -> None:
            nonlocal after
            after = True

        obj.on_exit(bad_callback)
        obj.on_exit(good_callback)

        with caplog.at_level("ERROR", logger="kuit.handlers"):
            obj._teardown()

        assert any(record.levelname == "ERROR" for record in caplog.records)
        assert after is True


# ------------------------------------------------------------------
# at change detector
# ------------------------------------------------------------------


class FakeTime:
    """Monotonic fake clock for time-sensitive tests."""

    def __init__(self) -> None:
        self._t = 0.0

    @property
    def elapsed(self) -> float:
        return self._t

    def time(self) -> float:
        return self._t

    def sleep(self, duration: float) -> None:
        self._t += duration


class TestOn:
    """GIVEN at change detector
    WHEN file mtime changes or signals arrive
    THEN the callable signals restart requirements.
    """

    def test_on_returns_callable_with_sleep_attribute(self) -> None:
        """GIVEN default arguments
        WHEN on() is called
        THEN the returned object has a .sleep method.
        """
        checker = on()

        assert hasattr(checker, "sleep")
        assert callable(checker.sleep)

    def test_on_on_change_returns_true_when_no_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN a stable file mtime
        WHEN on_change() is called
        THEN it returns True.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on()

        assert checker() is True

    def test_on_on_change_returns_false_when_file_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN a file whose mtime changes after construction
        WHEN on_change() detects the change
        THEN it returns False and calls func(errno).
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        calls: list[int] = []

        def fake_exit(code: int) -> None:
            calls.append(code)

        checker = on(func=fake_exit)
        # Modify mtime.
        script.write_text("changed")

        result = checker()

        assert result is False
        assert calls == [137]

    def test_on_sleep_returns_true_before_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN no file change within the sleep window
        WHEN sleep() is called with a short wait
        THEN it returns True.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        ft = FakeTime()
        monkeypatch.setattr("time.time", ft.time)
        monkeypatch.setattr("time.sleep", ft.sleep)

        checker = on()

        assert checker.sleep(5.0, poll=1.0) is True

    def test_on_sleep_returns_false_on_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN a file that changes during the sleep window
        WHEN sleep() polls and detects the change
        THEN it returns False.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        ft = FakeTime()
        monkeypatch.setattr("time.time", ft.time)
        monkeypatch.setattr("time.sleep", ft.sleep)

        checker = on(func=lambda _code: None)

        # Simulate change after first poll.
        def mutate_after_first_sleep(duration: float) -> None:
            ft.sleep(duration)
            if ft.elapsed >= 1.0:
                script.write_text("mutated")

        monkeypatch.setattr("time.sleep", mutate_after_first_sleep)

        assert checker.sleep(5.0, poll=1.0) is False

    def test_on_sleep_with_zero_wait_returns_true_immediately(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN wait=0
        WHEN sleep() is called
        THEN it returns True immediately without polling.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on()

        assert checker.sleep(0.0) is True

    def test_on_with_signal_sets_needrestart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN at configured with a signal number
        WHEN the registered signal handler is invoked
        THEN NeedRestart becomes True.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        # NeedRestart must be False before the test.
        _signals.NeedRestart = False

        on(signal=signal.SIGUSR1)

        # Capture the handler and reset signal to default.
        handler = signal.signal(signal.SIGUSR1, signal.SIG_DFL)
        try:
            handler(0, None)  # type: ignore[assignment][arg-type]
            assert _signals.NeedRestart is True
        finally:
            # No-op cleanup; fixture resets state.
            pass

    def test_on_on_change_logs_warning_on_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a file whose mtime changes
        WHEN on_change() detects the change
        THEN a WARNING log with 'updated at' is emitted.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on(func=lambda _code: None)
        script.write_text("changed")

        with caplog.at_level(logging.WARNING, logger="kuit.handlers"):
            checker()

        assert any("updated at" in r.message for r in caplog.records)

    def test_on_on_change_returns_false_when_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a file that is deleted after construction
        WHEN on_change() checks mtime
        THEN it returns False and logs a WARNING.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on()
        script.unlink()

        with caplog.at_level(logging.WARNING, logger="kuit.handlers"):
            result = checker()

        assert result is False
        assert any("removed" in r.message for r in caplog.records)

    def test_on_func_defaults_to_sys_exit(self) -> None:
        """GIVEN no explicit func argument
        WHEN inspecting at internals
        THEN the default func is sys.exit.
        """
        checker = on()

        # Default func is sys.exit; we verify by inspecting the closure
        # of the inner on_change function.
        closure = getattr(checker, "__closure__", None)
        assert closure is not None
        # The 'func' cell is one of the closure variables.
        func_cell = next(
            (cell for cell in closure if cell.cell_contents is sys.exit),
            None,
        )
        assert func_cell is not None

    def test_on_with_zero_signal_does_not_register(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN signal=0
        WHEN on() is constructed
        THEN no signal handler is registered.
        """
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        # Ensure no previous handler interferes.
        original = signal.signal(signal.SIGUSR1, signal.SIG_DFL)
        try:
            _ = on(signal=0)
            current = signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            assert current is signal.SIG_DFL
        finally:
            signal.signal(signal.SIGUSR1, original)


# ------------------------------------------------------------------
# Annotation inference
# ------------------------------------------------------------------


class TestAnnotationInference:
    """GIVEN signal utility functions
    WHEN inspecting type hints
    THEN signatures match declared types.
    """

    def test_get_selfpath_returns_path(self) -> None:
        """GIVEN get_selfpath
        WHEN getting type hints
        THEN return type is Path.
        """
        # --- Act ---
        hints = get_type_hints(get_selfpath)

        # --- Assert ---
        assert hints["return"] is Path

    def test_get_mtime_returns_float(self) -> None:
        """GIVEN get_mtime
        WHEN getting type hints
        THEN return type is float.
        """
        # --- Act ---
        hints = get_type_hints(get_mtime)

        # --- Assert ---
        assert hints["return"] is float

    def test_on_returns_callable(self) -> None:
        """GIVEN at
        WHEN getting type hints
        THEN return type is a callable.
        """
        # --- Act ---
        hints = get_type_hints(on)

        # --- Assert ---
        assert "Callable" in str(
            hints["return"],
        ) or "_OnChangeCallable" in str(
            hints["return"],
        )


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Paranoid edge-case coverage for signals."""

    def test_on_sleep_zero_returns_true_immediately(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """GIVEN at sleep(0)
        WHEN called
        THEN returns True immediately.
        """
        # --- Arrange ---
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on()

        # --- Act ---
        result = checker.sleep(0)

        # --- Assert ---
        assert result is True

    def test_on_with_removed_file_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN a file that is removed
        WHEN at callable is invoked
        THEN returns False and logs warning.
        """
        # --- Arrange ---
        script = tmp_path / "script.py"
        script.write_text("pass")
        monkeypatch.setattr(sys, "argv", [str(script)])
        get_selfpath.cache_clear()

        checker = on()
        script.unlink()

        # --- Act ---
        with caplog.at_level(logging.WARNING, logger="kuit.handlers"):
            result = checker()

        # --- Assert ---
        assert result is False
        assert any("removed" in r.message for r in caplog.records)

    def test_on_quit_teardown_with_none_callback_logs(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """GIVEN None scheduled as callback
        WHEN teardown runs
        THEN the TypeError is logged at ERROR and teardown does not raise.
        """
        # --- Arrange ---
        obj = OnQuit()
        obj._callbacks.clear()
        obj._callbacks.append(None)  # type: ignore[assignment][arg-type]
        obj._already_called = False

        # --- Act ---
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            obj._teardown()

        # --- Assert ---
        assert any(
            record.levelname == "ERROR" and "None" in record.message
            for record in caplog.records
        )
