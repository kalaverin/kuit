from typing import cast

from kuit.handlers import OnQuit, on

registry: OnQuit = cast("OnQuit", OnQuit())

and_call = registry.on_exit
and_intercept = registry.on_exception


__all__ = (
    "and_call",
    "and_intercept",
    "on",
)
