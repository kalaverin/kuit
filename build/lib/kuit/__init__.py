from typing import cast

from zx.quit.halt import here
from zx.quit.handlers import OnQuit, on

registry: OnQuit = cast("OnQuit", OnQuit())

and_call = registry.on_exit
and_intercept = registry.on_exception


__all__ = (
    "and_call",
    "and_intercept",
    "here",
    "on",
)
