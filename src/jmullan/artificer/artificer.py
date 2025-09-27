"""Generic utility methods."""

import inspect
import types
from typing import Any


def _get_caller_source(frame: types.FrameType | None) -> str | None:
    if frame is None:
        return None
    parent_frame = frame.f_back
    if parent_frame is not None:
        frame = parent_frame
    frame_info = inspect.getframeinfo(frame)
    if frame_info is None:
        return None
    code_context = frame_info.code_context
    if code_context is None or not code_context:
        return None
    src = code_context[0].removeprefix("validate_not_none(").removesuffix(")")
    if src.startswith("self."):
        src = src.removeprefix("self.")
        if "self" in frame.f_locals:
            old_self = frame.f_locals["self"]
            if old_self and hasattr(old_self, "__class__"):
                class_name = old_self.__class__.__name__
                src = f"{class_name}.{src}"
    return src


def validate_not_none(value: Any | None) -> None:  # noqa: ANN401
    """Almost the same thing as `assert thing is not None` but free of linter complaints."""
    if value is None:
        src = _get_caller_source(inspect.currentframe())
        if src is None:
            raise ValueError("Got unexpected None")
        message = f"{src} must not be None"
        raise ValueError(message)
