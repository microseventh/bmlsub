"""Source-asset types and registration options."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceAssetKind(str, Enum):
    SUBTITLE = "subtitle"
    FONT = "font"
    CHAPTER = "chapter"
    ATTACHMENT = "attachment"


@dataclass(frozen=True)
class SourceAssetRegistrationOptions:
    kind: SourceAssetKind
    language: str | None = None
    origin: str = "explicit_user_input"

    def __post_init__(self) -> None:
        language = self.language.strip().lower() if self.language else None
        if language and (len(language) > 35 or not all(part.isalnum() for part in language.split("-"))):
            raise ValueError("asset language must be a short language tag")
        if not self.origin.strip():
            raise ValueError("asset registration origin must not be empty")
        object.__setattr__(self, "language", language)
