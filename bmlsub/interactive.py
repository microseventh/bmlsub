"""Language-aware helpers for human-facing interactive CLI output."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal


UILanguage = Literal["zh", "en"]

_UI_LANGUAGE: ContextVar[UILanguage] = ContextVar("bmlsub_ui_language", default="zh")


def set_ui_language(language: UILanguage) -> None:
    if language not in {"zh", "en"}:
        raise ValueError("unsupported UI language")
    _UI_LANGUAGE.set(language)


def ui_language() -> UILanguage:
    return _UI_LANGUAGE.get()


def ui_text(zh: str, en: str) -> str:
    return zh if ui_language() == "zh" else en


def default_prompt(label: str, default: str) -> str:
    if ui_language() == "zh":
        return f"{label}（直接按 Enter 使用默认值：{default}）: "
    return f"{label} (Press Enter to use the default: {default}): "


def optional_prompt(label: str) -> str:
    if ui_language() == "zh":
        return f"{label}（直接按 Enter 留空）: "
    return f"{label} (Press Enter to leave blank): "


def confirmation_prompt(label: str, *, default: bool = False) -> str:
    if ui_language() == "zh":
        choice = "是" if default else "否"
        return f"{label}（直接按 Enter 使用默认值：{choice}）[y/n]: "
    choice = "yes" if default else "no"
    return f"{label} (Press Enter to use the default: {choice}) [y/n]: "
