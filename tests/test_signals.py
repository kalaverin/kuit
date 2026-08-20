"""Tests for kuit.handlers module."""

from __future__ import annotations

import signal
import sys
import threading
from functools import partial
from types import TracebackType
from typing import Any, Never
from unittest.mock import ANY, MagicMock, patch

import pytest
from kain.classes import Nothing

from kuit.handlers import (
    NeedRestart,
    OnQuit,
    get_mtime,
    get_selfpath,
    on,
)


@pytest.fixture(autouse=True)
def _isolate_on_quit() -> None:
    """Restore global state and reset the OnQuit singleton after each test."""
    # Save the original state so we can restore it after the test.
    original_excepthook = sys.excepthook
    original_threading_hook = threading.excepthook
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigquit = (
        signal.getsignal(signal.SIGQUIT)
        if hasattr(signal, "SIGQUIT")
        else None
    )

    # Ensure a clean baseline before each test.
    sys.excepthook = sys.__excepthook__
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)

    # Reset singleton state so the next test gets a fresh instance.
    OnQuit.instance = Nothing  # type: ignore[assignment][attr-defined]

    yield

    # Tear down any created instance and restore hooks.
    inst = OnQuit.instance
    if inst is not Nothing and hasattr(inst, "_restore_original_handlers"):
        inst._restore_original_handlers()

    OnQuit.instance = Nothing  # type: ignore[assignment][attr-defined]
    sys.excepthook = original_excepthook
    threading.excepthook = original_threading_hook
    signal.signal(signal.SIGINT, original_sigint)
    signal.signal(signal.SIGTERM, original_sigterm)
    if hasattr(signal, "SIGQUIT") and original_sigquit is not None:
        signal.signal(signal.SIGQUIT, original_sigquit)


def _fresh_instance() -> Any:
    """Create a fresh OnQuit singleton instance."""
    OnQuit.instance = Nothing  # type: ignore[assignment][attr-defined]
    return OnQuit()


def _make_except_hook_args(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_traceback: TracebackType | None,
    thread: threading.Thread | None = None,
) -> threading.ExceptHookArgs:
    """Build threading.ExceptHookArgs from positional tuple (structseq)."""
    if thread is None:
        thread = threading.current_thread()
    return threading.ExceptHookArgs(
        (exc_type, exc_value, exc_traceback, thread),
    )


class TestOnSystemExit:
    def test_on_quit_is_singleton(self) -> None:
        inst1 = _fresh_instance()
        inst2 = OnQuit()
        assert inst1 is inst2

    def test_schedule_calls_callback_on_teardown(self) -> None:
        callback = MagicMock()
        inst = _fresh_instance()
        inst.on_exit(callback)
        inst._teardown()
        callback.assert_called_once_with()

    def test_teardown_is_idempotent(self) -> None:
        callback = MagicMock()
        inst = _fresh_instance()
        inst.on_exit(callback)
        inst._teardown()
        inst._teardown()
        assert callback.call_count == 1

    def test_teardown_catches_callback_exceptions(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        callback = MagicMock(side_effect=RuntimeError("boom"))
        inst = _fresh_instance()
        inst.on_exit(callback)
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            inst._teardown()
        assert "boom" in caplog.text

    def test_threading_handler_skips_system_exit(self) -> None:
        inst = _fresh_instance()
        args = _make_except_hook_args(SystemExit, SystemExit(1), None)
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_not_called()

    def test_threading_handler_skips_none_exc_type(self) -> None:
        inst = _fresh_instance()
        args = _make_except_hook_args(None, None, None)
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_not_called()

    def test_threading_handler_proxies_other_exceptions(self) -> None:
        inst = _fresh_instance()
        tb: TracebackType | None = None
        try:
            raise ValueError("test")
        except ValueError:
            tb = sys.exc_info()[2]
        args = _make_except_hook_args(ValueError, ValueError("test"), tb)
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_called_once_with(
                ValueError,
                args.exc_value,
                tb,
            )

    def test_restore_original_handlers_resets_sigquit(self) -> None:
        inst = _fresh_instance()
        inst._setup()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._restore_original_handlers()
        calls = [c[0][0] for c in mock_bind.call_args_list]
        assert signal.SIGINT in calls
        assert signal.SIGTERM in calls
        assert signal.SIGQUIT in calls
        assert signal.SIGHUP not in calls

    def test_exceptions_hooks_proxy_calls_hooks_and_teardown(self) -> None:
        inst = _fresh_instance()
        hook_calls: list[tuple[Any, ...]] = []
        original_calls: list[tuple[Any, ...]] = []

        def hook(*args: Any) -> None:
            hook_calls.append(args)

        def original_hook(*args: Any) -> None:
            original_calls.append(args)

        inst.on_exception(hook)
        inst._original_hook = original_hook
        inst._exceptions_hook(
            RuntimeError,
            RuntimeError("boom"),
            None,
        )
        assert len(hook_calls) == 1
        assert hook_calls[0][0] is RuntimeError
        assert str(hook_calls[0][1]) == "boom"
        assert hook_calls[0][2] is None
        assert len(original_calls) == 1
        assert original_calls[0][0] is RuntimeError
        assert str(original_calls[0][1]) == "boom"
        assert original_calls[0][2] is None
        assert inst._already_called is True

    def test_signal_handler_calls_teardown_and_exits(self) -> None:
        inst = _fresh_instance()
        with patch.object(inst, "_teardown") as mock_teardown:
            with pytest.raises(SystemExit) as exc_info:
                inst._exit(15, None)
            assert exc_info.value.code == 1
            mock_teardown.assert_called_once()

    def test_inject_hook_replaces_excepthook(self) -> None:
        original = sys.excepthook
        inst = _fresh_instance()
        inst._setup()
        assert sys.excepthook is inst._proxy
        sys.excepthook = original

    def test_teardown_restores_handlers(self) -> None:
        original_excepthook = sys.excepthook
        inst = _fresh_instance()
        inst._setup()
        inst._teardown()
        assert sys.excepthook is original_excepthook
        assert threading.excepthook is threading.__excepthook__

    def test_on_quit_init_does_not_install(self) -> None:
        """Construction must not touch global hooks, handlers, or atexit."""
        with (
            patch("kuit.handlers.atexit.register") as mock_atexit,
            patch("kuit.handlers.bind") as mock_bind,
        ):
            _fresh_instance()
        mock_atexit.assert_not_called()
        mock_bind.assert_not_called()

    def test_install_registers_hooks_and_handlers(self) -> None:
        """install() registers excepthook, threading hook, signals, atexit."""
        inst = _fresh_instance()
        with (
            patch("kuit.handlers.atexit.register") as mock_atexit,
            patch("kuit.handlers.bind") as mock_bind,
        ):
            inst._setup()
        mock_atexit.assert_not_called()
        sigs = {c.args[0] for c in mock_bind.call_args_list}
        assert signal.SIGINT in sigs
        assert signal.SIGTERM in sigs
        assert sys.excepthook is inst._proxy
        assert getattr(threading.excepthook, "__func__", None) is (
            inst._threading_handler.__func__
        )

    def test_install_is_idempotent(self) -> None:
        """Repeated install() calls must not register hooks again."""
        inst = _fresh_instance()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._setup()
            inst._setup()
        # SIGINT, SIGTERM, SIGQUIT = 3 calls total.
        assert mock_bind.call_count == 3

    def test_schedule_triggers_install_and_atexit(self) -> None:
        """schedule() lazily installs hooks and registers atexit once."""
        inst = _fresh_instance()
        with (
            patch.object(inst, "_setup", wraps=inst._setup) as mock_install,
            patch.object(
                inst,
                "_ensure_atexit",
                wraps=inst._ensure_atexit,
            ) as mock_atexit,
        ):
            inst.on_exit(lambda: None)
            inst.on_exit(lambda: None)
        # install() and _ensure_atexit() are invoked from each schedule()
        # but both are idempotent.
        assert mock_install.call_count == 2
        assert mock_atexit.call_count == 2
        assert len(inst._callbacks) == 2

    def test_restore_original_handlers_restores_signal_handlers(self) -> None:
        """restore_original_handlers() restores saved signal handlers."""
        inst = _fresh_instance()
        custom_sigint = lambda _signum, _frame: None  # noqa: E731
        custom_sigterm = lambda _signum, _frame: None  # noqa: E731
        signal.signal(signal.SIGINT, custom_sigint)
        signal.signal(signal.SIGTERM, custom_sigterm)
        try:
            inst._setup()
            inst._restore_original_handlers()
            assert (
                signal.signal(signal.SIGINT, signal.SIG_DFL) is custom_sigint
            )
            assert (
                signal.signal(signal.SIGTERM, signal.SIG_DFL) is custom_sigterm
            )
        finally:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)


class TestOn:
    def test_get_selfpath_returns_absolute_path(self) -> None:
        path = get_selfpath()
        assert path.is_absolute()

    def test_get_mtime_returns_float(self) -> None:
        mtime = get_mtime()
        assert isinstance(mtime, float)
        assert mtime > 0.0

    def test_on_returns_callable_with_sleep_attr(self) -> None:
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on()
        assert callable(checker)
        assert hasattr(checker, "sleep")
        assert callable(checker.sleep)

    def test_on_on_change_true_when_no_changes(self) -> None:
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on()
        assert checker() is True

    def test_on_sleep_method_with_zero_wait(self) -> None:
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on()
        assert checker.sleep(0.0) is True
        assert checker.sleep(0.0, poll=0.1) is True

    def test_on_registers_signal_handler(self) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        with patch("kuit.handlers.bind") as mock_bind:
            on(func=mock_exit, signal=signal.SIGUSR1)
            mock_bind.assert_called_once_with(signal.SIGUSR1, ANY)
        handler = mock_bind.call_args[0][1]
        assert sig_mod.NeedRestart is False
        handler(1, None)
        assert sig_mod.NeedRestart is True
        sig_mod.NeedRestart = False

    def test_on_on_change_triggers_when_needrestart_set(self) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on(func=mock_exit, signal=signal.SIGUSR1)
        sig_mod.NeedRestart = True
        try:
            assert checker() is False
            mock_exit.assert_called_once_with(137)
        finally:
            sig_mod.NeedRestart = False

    def test_on_on_change_file_not_found(self) -> None:
        mock_exit = MagicMock()
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            mock_mtime.return_value = 1.0
            checker = on(func=mock_exit)
            mock_mtime.side_effect = FileNotFoundError
            assert checker() is False

    def test_on_sleep_with_short_wait(self) -> None:
        mock_exit = MagicMock()
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on(func=mock_exit)
        result = checker.sleep(0.01, poll=0.001)
        assert result is True


class TestModuleAttributes:
    def test_needrestart_is_bool(self) -> None:
        assert isinstance(NeedRestart, bool)

    def test_all_exports_exist(self) -> None:
        import kuit.handlers as mod

        for name in mod.__all__:
            assert hasattr(mod, name)


# ============================================================================
# Expanded tests below
# ============================================================================


class TestOnQuitScheduleVariations:
    """Tests for scheduling various callable types."""

    def test_schedule_plain_function(self) -> None:
        calls: list[int] = []
        inst = _fresh_instance()
        inst.on_exit(lambda: calls.append(1))
        inst._teardown()
        assert calls == [1]

    def test_schedule_lambda(self) -> None:
        flag = []
        inst = _fresh_instance()
        inst.on_exit(lambda: flag.append("ok"))
        inst._teardown()
        assert flag == ["ok"]

    def test_schedule_bound_method(self) -> None:
        class C:
            def __init__(self) -> None:
                self.val = 0

            def incr(self) -> None:
                self.val += 1

        obj = C()
        inst = _fresh_instance()
        inst.on_exit(obj.incr)
        inst._teardown()
        assert obj.val == 1

    def test_schedule_callable_class_instance(self) -> None:
        class Counter:
            def __init__(self) -> None:
                self.count = 0

            def __call__(self) -> None:
                self.count += 1

        counter = Counter()
        inst = _fresh_instance()
        inst.on_exit(counter)
        inst._teardown()
        assert counter.count == 1

    def test_schedule_functools_partial(self) -> None:
        results: list[int] = []
        inst = _fresh_instance()
        inst.on_exit(partial(results.append, 42))
        inst._teardown()
        assert results == [42]

    def test_duplicate_schedule_calls_callback_twice(self) -> None:
        calls: list[int] = []
        inst = _fresh_instance()
        inst.on_exit(lambda: calls.append(1))
        inst.on_exit(lambda: calls.append(1))
        inst._teardown()
        assert calls == [1, 1]

    def test_schedule_preserves_registration_order(self) -> None:
        order: list[str] = []
        inst = _fresh_instance()
        inst.on_exit(lambda: order.append("a"))
        inst.on_exit(lambda: order.append("b"))
        inst.on_exit(lambda: order.append("c"))
        inst._teardown()
        assert order == ["a", "b", "c"]

    def test_schedule_returns_none(self) -> None:
        inst = _fresh_instance()
        result = inst.on_exit(lambda: None)
        assert result is None


class TestOnQuitTeardownExtended:
    """Extended teardown behavior tests."""

    def test_teardown_sets_already_called(self) -> None:
        inst = _fresh_instance()
        assert inst._already_called is False
        inst._teardown()
        assert inst._already_called is True

    def test_teardown_with_empty_callbacks_ok(self) -> None:
        inst = _fresh_instance()
        assert inst._teardown() is None
        assert inst._already_called is True

    def test_teardown_catches_baseexception_in_callback(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        inst = _fresh_instance()
        inst.on_exit(lambda: (_ for _ in ()).throw(SystemExit("die")))
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            inst._teardown()
        assert "die" in caplog.text

    def test_teardown_restores_handlers_even_if_callback_raises(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        original_excepthook = sys.excepthook
        inst = _fresh_instance()
        inst.on_exit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            inst._teardown()
        assert sys.excepthook is original_excepthook

    def test_teardown_runs_callbacks_in_order(self) -> None:
        order: list[int] = []
        inst = _fresh_instance()
        inst.on_exit(lambda: order.append(1))
        inst.on_exit(lambda: order.append(2))
        inst._teardown()
        assert order == [1, 2]

    def test_multiple_teardown_calls_no_additional_callbacks(self) -> None:
        counter = MagicMock()
        inst = _fresh_instance()
        inst.on_exit(counter)
        inst._teardown()
        inst._teardown()
        inst._teardown()
        assert counter.call_count == 1


class TestOnQuitHooksExtended:
    """Extended tests for exception hooks and proxy behavior."""

    def test_add_hook_appends_to_hooks_chain(self) -> None:
        inst = _fresh_instance()
        inst.on_exception(lambda *_: None)
        assert len(inst._hooks_chain) == 1

    def test_exception_in_hook_logs_and_continues(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        inst = _fresh_instance()
        calls: list[str] = []

        def bad_hook(*_) -> Never:
            calls.append("bad")
            raise RuntimeError("hook boom")

        def good_hook(*_) -> None:
            calls.append("good")

        inst.on_exception(bad_hook)
        inst.on_exception(good_hook)
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            inst._exceptions_hook(RuntimeError, RuntimeError("x"), None)
        assert calls == ["bad", "good"]
        assert "hook boom" in caplog.text
        assert inst._already_called is True

    def test_multiple_hooks_all_called(self) -> None:
        inst = _fresh_instance()
        count = 0

        def hook(*_) -> None:
            nonlocal count
            count += 1

        inst.on_exception(hook)
        inst.on_exception(hook)
        inst.on_exception(hook)
        inst._exceptions_hook(RuntimeError, RuntimeError("x"), None)
        assert count == 3

    def test_exceptions_hooks_proxy_reinjects_if_excepthook_changed(
        self,
    ) -> None:
        inst = _fresh_instance()
        inst._setup()
        other_hook = MagicMock()
        sys.excepthook = other_hook
        inst._exceptions_hook(RuntimeError, RuntimeError("x"), None)
        # teardown() restores sys.excepthook, so we verify by side-effects:
        assert other_hook in inst._hooks_chain
        assert inst._already_called is True

    def test_exceptions_hooks_proxy_recursion_safety(self) -> None:
        """Ensure that changing excepthook inside a hook does not recurse."""
        inst = _fresh_instance()
        call_count = 0

        def mutating_hook(*_) -> None:
            nonlocal call_count
            call_count += 1
            sys.excepthook = lambda *_: None

        inst.on_exception(mutating_hook)
        # The proxy should run once, append the lambda, and finish without
        # entering an infinite loop because it only checks at the top.
        inst._exceptions_hook(RuntimeError, RuntimeError("x"), None)
        assert call_count == 1
        assert inst._already_called is True

    def test_exceptions_hooks_proxy_calls_original_hook(self) -> None:
        inst = _fresh_instance()
        original = MagicMock()
        inst._original_hook = original
        inst._exceptions_hook(ValueError, ValueError("v"), None)
        assert original.call_count == 1
        args = original.call_args[0]
        assert args[0] is ValueError
        assert str(args[1]) == "v"
        assert args[2] is None

    def test_inject_hook_replaces_excepthook_explicitly(self) -> None:
        inst = _fresh_instance()
        sys.excepthook = sys.__excepthook__
        inst._inject_hook()
        assert sys.excepthook is inst._proxy


class TestOnQuitThreadingExtended:
    """Extended threading exception handler tests."""

    @pytest.mark.parametrize(
        "exc_type,should_proxy",
        (
            (SystemExit, False),
            (None, False),
            (ValueError, True),
            (RuntimeError, True),
            (TypeError, True),
            (KeyboardInterrupt, True),
        ),
    )
    def test_threading_handler_various_exceptions(
        self,
        exc_type: type[BaseException] | None,
        should_proxy: bool,
    ) -> None:
        inst = _fresh_instance()
        args = _make_except_hook_args(
            exc_type,
            RuntimeError("x") if exc_type else None,
            None,
        )
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            if should_proxy:
                proxy.assert_called_once()
            else:
                proxy.assert_not_called()

    def test_threading_handler_with_custom_thread(self) -> None:
        inst = _fresh_instance()
        custom_thread = threading.Thread(target=lambda: None, name="custom")
        tb: TracebackType | None = None
        try:
            raise ValueError("t")
        except ValueError:
            tb = sys.exc_info()[2]
        args = _make_except_hook_args(
            ValueError,
            ValueError("t"),
            tb,
            thread=custom_thread,
        )
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_called_once_with(
                ValueError,
                args.exc_value,
                tb,
            )

    def test_threading_handler_with_baseexception(self) -> None:
        inst = _fresh_instance()
        args = _make_except_hook_args(
            ArithmeticError,
            ArithmeticError("a"),
            None,
        )
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_called_once()

    def test_inject_threading_hook_replaces_hook(self) -> None:
        inst = _fresh_instance()
        threading.excepthook = threading.__excepthook__
        inst._inject_threading_hook()
        assert (
            getattr(threading.excepthook, "__func__", None)
            is inst._threading_handler.__func__
        )


class TestOnQuitSignalExtended:
    """Extended signal handler tests."""

    @pytest.mark.parametrize(
        "sig",
        (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT),
    )
    def test_signal_handler_exits_with_code_one(self, sig: int) -> None:
        inst = _fresh_instance()
        with patch.object(inst, "_teardown") as mock_teardown:
            with pytest.raises(SystemExit) as exc_info:
                inst._exit(sig, None)
            assert exc_info.value.code == 1
            mock_teardown.assert_called_once()

    @pytest.mark.parametrize(
        "sig",
        (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT),
    )
    def test_inject_signal_handler_binds_signal(self, sig: int) -> None:
        inst = _fresh_instance()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._inject_handler()
        calls = [c[0][0] for c in mock_bind.call_args_list]
        assert sig in calls

    def test_inject_signal_handler_does_not_bind_sighup(self) -> None:
        inst = _fresh_instance()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._inject_handler()
        calls = [c[0][0] for c in mock_bind.call_args_list]
        assert getattr(signal, "SIGHUP", -1) not in calls

    @pytest.mark.parametrize(
        "sig",
        (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT),
    )
    def test_restore_original_handlers_restores_signal(self, sig: int) -> None:
        inst = _fresh_instance()
        original = signal.signal(sig, signal.SIG_DFL)
        try:
            inst._setup()
            with patch("kuit.handlers.bind") as mock_bind:
                inst._restore_original_handlers()
            calls = {c[0][0]: c[0][1] for c in mock_bind.call_args_list}
            assert calls[sig] is original
        finally:
            signal.signal(sig, original)

    def test_restore_original_handlers_does_not_touch_sighup(self) -> None:
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP not available on this platform")
        inst = _fresh_instance()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._restore_original_handlers()
        calls = [c[0][0] for c in mock_bind.call_args_list]
        assert signal.SIGHUP not in calls

    def test_restore_original_handlers_resets_sys_excepthook(self) -> None:
        original = sys.__excepthook__
        inst = _fresh_instance()
        sys.excepthook = lambda *_: None
        inst._restore_original_handlers()
        assert sys.excepthook is original

    def test_restore_original_handlers_resets_threading_excepthook(
        self,
    ) -> None:
        inst = _fresh_instance()
        threading.excepthook = lambda *_: None
        inst._restore_original_handlers()
        assert threading.excepthook is threading.__excepthook__


class TestOnExtended:
    """Extended at tests."""

    @pytest.fixture(autouse=True)
    def _clear_on_cache(self) -> None:
        on.cache_clear()  # type: ignore[assignment][attr-defined]

    def test_on_change_detects_mtime_change(self) -> None:
        mock_exit = MagicMock()
        checker = on(func=mock_exit)
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            mock_mtime.return_value = 999999.0
            assert checker() is False
            mock_exit.assert_called_once_with(137)

    def test_on_change_returns_true_when_stable(self) -> None:
        checker = on()
        assert checker() is True

    def test_on_change_with_sleep_parameter(self) -> None:
        checker = on()
        with patch("kuit.handlers.time.sleep") as mock_sleep:
            checker(sleep=0.5)
            mock_sleep.assert_called_once_with(0.5)

    def test_on_change_uses_default_sleep_from_kw(self) -> None:
        checker = on(sleep=0.3)
        with patch("kuit.handlers.time.sleep") as mock_sleep:
            checker()
            mock_sleep.assert_called_once_with(0.3)

    def test_sleep_zero_wait_returns_true(self) -> None:
        checker = on()
        assert checker.sleep(0.0) is True

    def test_sleep_polling_returns_true_when_stable(self) -> None:
        checker = on()
        result = checker.sleep(0.01, poll=0.001)
        assert result is True

    def test_sleep_stops_polling_on_change(self) -> None:
        mock_exit = MagicMock()
        checker = on(func=mock_exit)
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            # first call inside on_change sees changed mtime
            mock_mtime.return_value = 999999.0
            result = checker.sleep(1.0, poll=0.001)
            assert result is False
            mock_exit.assert_called_once_with(137)

    def test_needrestart_triggers_with_custom_errno(self) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        checker = on(func=mock_exit, signal=signal.SIGUSR1, errno=42)
        sig_mod.NeedRestart = True
        try:
            assert checker() is False
            mock_exit.assert_called_once_with(42)
        finally:
            sig_mod.NeedRestart = False

    def test_needrestart_without_signal_does_nothing(self) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        checker = on(func=mock_exit, signal=0)
        sig_mod.NeedRestart = True
        try:
            # signal=0 means no handler registered, so NeedRestart + signal=0
            # does NOT trigger because condition is `NeedRestart and signal`
            assert checker() is True
            mock_exit.assert_not_called()
        finally:
            sig_mod.NeedRestart = False

    def test_custom_func_return_value_ignored(self) -> None:
        mock_exit = MagicMock(return_value="ignored")
        checker = on(func=mock_exit)
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            mock_mtime.return_value = 888888.0
            assert checker() is False
            mock_exit.assert_called_once()

    def test_signal_registration_and_handler(self) -> None:
        mock_exit = MagicMock()
        with patch("kuit.handlers.bind") as mock_bind:
            on(func=mock_exit, signal=signal.SIGUSR2)
            mock_bind.assert_called_once_with(signal.SIGUSR2, ANY)

    def test_signal_unregistration_via_flag_reset(self) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        checker = on(func=mock_exit, signal=signal.SIGUSR1)
        sig_mod.NeedRestart = True
        try:
            checker()
            # After first call, flag is still True but func was called.
            # Actually func(sys.exit) would exit the process; with mock it
            # doesn't reset the flag. We just verify the behavior.
            mock_exit.assert_called_once()
        finally:
            sig_mod.NeedRestart = False

    def test_on_invalid_path_file_not_found(self) -> None:
        mock_exit = MagicMock()
        checker = on(func=mock_exit)
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            mock_mtime.side_effect = FileNotFoundError
            assert checker() is False

    def test_on_logs_on_mtime_change(self) -> None:
        mock_exit = MagicMock()
        checker = on(func=mock_exit)
        with patch("kuit.handlers.get_mtime") as mock_mtime:
            mock_mtime.return_value = 777777.0
            with patch("kuit.handlers.logger") as mock_logger:
                checker()
                mock_logger.warning.assert_called()
                args = " ".join(
                    str(c) for c in mock_logger.warning.call_args[0]
                )
                assert "updated at" in args or "stop" in args

    def test_on_sleep_uses_poll_kw_default(self) -> None:
        checker = on(poll=0.05)
        with patch("kuit.handlers.time.sleep") as mock_sleep:
            checker.sleep(0.1)
            # should sleep at least once with poll=0.05
            assert mock_sleep.call_count >= 1
            assert mock_sleep.call_args[0][0] == pytest.approx(0.05, abs=0.01)

    def test_multiple_quit_at_instances_different_signals(self) -> None:
        mock_exit_a = MagicMock()
        mock_exit_b = MagicMock()
        with patch("kuit.handlers.bind"):
            checker_a = on(func=mock_exit_a, signal=signal.SIGUSR1)
            checker_b = on(func=mock_exit_b, signal=signal.SIGUSR2)
            # Because at is cached, if args differ we get
            # different objects
            assert checker_a is not checker_b

    def test_on_change_returns_false_on_file_not_found(self) -> None:
        checker = on()
        with patch("kuit.handlers.get_mtime", side_effect=FileNotFoundError):
            assert checker() is False

    def test_sleep_deadline_zero_returns_true(self) -> None:
        checker = on()
        assert checker.sleep(0) is True


class TestOnParametrized:
    """Parametrized at edge cases."""

    @pytest.mark.parametrize(
        "wait,poll",
        (
            (0.0, 0.0),
            (0.0, 0.1),
            (0.01, 0.001),
            (0.02, 0.005),
        ),
    )
    def test_sleep_various_short_timeouts(
        self,
        wait: float,
        poll: float,
    ) -> None:
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on()
        result = checker.sleep(wait, poll=poll)
        assert result is True

    @pytest.mark.parametrize("errno_val", (0, 1, 137, 255, 999))
    def test_custom_errno_values(self, errno_val: int) -> None:
        import kuit.handlers as sig_mod

        mock_exit = MagicMock()
        on.cache_clear()  # type: ignore[assignment][attr-defined]
        checker = on(
            func=mock_exit,
            signal=signal.SIGUSR1,
            errno=errno_val,
        )
        sig_mod.NeedRestart = True
        try:
            checker()
            mock_exit.assert_called_once_with(errno_val)
        finally:
            sig_mod.NeedRestart = False


class TestOnQuitIntegration:
    """Integration-level tests for OnQuit."""

    def test_full_flow_signal_then_teardown_idempotent(self) -> None:
        inst = _fresh_instance()
        cb = MagicMock()
        inst.on_exit(cb)
        with pytest.raises(SystemExit):
            inst._exit(signal.SIGTERM, None)
        # teardown already called by signal_handler
        assert inst._already_called is True
        cb.assert_called_once()
        # second teardown does nothing
        inst._teardown()
        cb.assert_called_once()

    def test_full_flow_exception_then_teardown_idempotent(self) -> None:
        inst = _fresh_instance()
        cb = MagicMock()
        inst.on_exit(cb)
        inst._exceptions_hook(RuntimeError, RuntimeError("boom"), None)
        assert inst._already_called is True
        cb.assert_called_once()
        inst._teardown()
        cb.assert_called_once()

    def test_schedule_after_teardown_ignored(self) -> None:
        inst = _fresh_instance()
        cb = MagicMock()
        inst._teardown()
        inst.on_exit(cb)
        # callback was added after teardown, won't run on subsequent teardown
        # because already_called is True
        inst._teardown()
        cb.assert_not_called()

    def test_singleton_instance_persists_across_accesses(self) -> None:
        a = _fresh_instance()
        b = OnQuit()
        c = OnQuit()
        assert a is b is c

    def test_multiple_callbacks_all_fire(self) -> None:
        calls: list[int] = []
        inst = _fresh_instance()
        for i in range(5):
            inst.on_exit(lambda i=i: calls.append(i))
        inst._teardown()
        assert calls == [0, 1, 2, 3, 4]

    def test_exceptions_hooks_proxy_logs_on_original_hook_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        inst = _fresh_instance()
        inst._original_hook = lambda *_: (_ for _ in ()).throw(
            RuntimeError("orig"),
        )
        with caplog.at_level("ERROR", logger="kuit.handlers"):
            inst._exceptions_hook(ValueError, ValueError("v"), None)
        assert "orig" in caplog.text

    def test_restore_original_handlers_idempotent(self) -> None:
        inst = _fresh_instance()
        inst._restore_original_handlers()
        # Should not raise when called twice
        inst._restore_original_handlers()
        assert sys.excepthook is inst._original_hook

    def test_threading_handler_delegates_keyboard_interrupt(self) -> None:
        inst = _fresh_instance()
        args = _make_except_hook_args(
            KeyboardInterrupt,
            KeyboardInterrupt("ki"),
            None,
        )
        with patch.object(inst, "_proxy") as proxy:
            inst._threading_handler(args)
            proxy.assert_called_once()

    def test_signal_handler_frame_argument_ignored(self) -> None:
        inst = _fresh_instance()
        with patch.object(inst, "_teardown") as mock_teardown:
            with pytest.raises(SystemExit):
                inst._exit(signal.SIGINT, MagicMock())
            mock_teardown.assert_called_once()

    def test_inject_signal_handler_overwrites_existing(self) -> None:
        inst = _fresh_instance()
        with patch("kuit.handlers.bind") as mock_bind:
            inst._inject_handler()
            assert mock_bind.call_count == 3

    def test_add_hook_returns_none(self) -> None:
        inst = _fresh_instance()
        result = inst.on_exception(lambda *_: None)
        assert result is None
