---
title: kuit
description: Graceful shutdown and file-change detection for Python
---

[ref: #kuit]

# kuit

`kuit` is a tiny Python library for graceful process shutdown and development-time auto-restart.

It gives you three public entry points:

- `kuit.register` — register callbacks that run when the process exits.
- `kuit.add_hook` — append custom handlers to the uncaught-exception chain.
- `kuit.on` — detect file changes or incoming signals and exit cleanly.

[ref: #installation]

## Installation

Install with `uv`:

```bash
uv add kuit
```

Or with any PEP 517-compatible tool:

```bash
pip install kuit
```

[ref: #register]

## Registering shutdown callbacks with `kuit.register`

Use `kuit.register` as a decorator or as a plain function call.
The callback runs exactly once on `SIGINT`, `SIGTERM`, `SIGQUIT`, or an unhandled exception.

```python
import kuit

@kuit.register
def close_db():
    print("closing database connection")

@kuit.register
def flush_logs():
    print("flushing logs")
```

Callbacks are executed in registration order.
Exceptions raised by one callback are logged and do not stop the next callback from running.

[ref: #add-hook]

## Adding exception hooks with `kuit.add_hook`

`kuit.add_hook` lets you insert a custom function into the chain that runs before the original `sys.excepthook`.

```python
import kuit

def notify_sentry(exc_type, exc_value, traceback):
    print(f"reporting {exc_type.__name__}: {exc_value}")

kuit.add_hook(notify_sentry)
```

[ref: #on]

## Detecting file changes and signals with `kuit.on`

`kuit.on` builds a callable that returns `True` while the application should keep running and `False` when it should exit.
By default it watches the mtime of `sys.argv[0]`; if the file changes, it calls `func(errno)` and returns `False`.

```python
import signal
import kuit

checker = kuit.on(signal=signal.SIGUSR1)

while checker.sleep(60):
    pass  # main loop work
```

`checker()` performs a single check immediately.
`checker.sleep(wait, poll=...)` blocks for up to `wait` seconds, polling every `poll` seconds, and returns as soon as a change is detected or the deadline is reached.

[ref: #on-options]

### `kuit.on` options

All arguments are keyword-only.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `func` | `Callable[[int], Any]` | `sys.exit` | Function called when the process should exit; receives `errno`. |
| `signal` | `int` | `0` | POSIX signal number to listen for; set to `0` to disable signal handling. |
| `errno` | `int` | `137` | Exit code passed to `func` on change detection. |
| `sleep` | `float` | `0.0` | Default seconds to sleep inside `checker()` before returning. |
| `poll` | `float` | `2.5` | Default polling interval used by `checker.sleep()`. |

[ref: #full-example]

## Full example

```python
import signal
import kuit

@kuit.register
def cleanup():
    print("shutting down")

def log_uncaught(exc_type, exc_value, traceback):
    print(f"uncaught {exc_type.__name__}: {exc_value}")

kuit.add_hook(log_uncaught)

checker = kuit.on(
    signal=signal.SIGUSR1,
    errno=137,
    poll=2.5,
)

while checker.sleep(60):
    pass
```
