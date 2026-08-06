"""Canonical operation registry for the compact workstation CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationSpec:
    name: str
    label: str
    input_kind: str
    can_rebuild: bool = True
    external: bool = False
    irreversible: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "input_kind": self.input_kind,
            "can_build": True,
            "can_rebuild": self.can_rebuild,
            "external": self.external,
            "irreversible": self.irreversible,
        }


_SPECS = (
    OperationSpec("bgminfo", "Series metadata", "config"),
    OperationSpec("ensub", "Extract subtitles", "video"),
    OperationSpec("trans", "Extract audio and transcribe", "media"),
    OperationSpec("pubinfo", "Delivery configuration", "config"),
    OperationSpec("encode", "Encode or package video", "video"),
    OperationSpec("torrent", "Create Torrent", "content"),
    OperationSpec("upr2", "Upload to R2", "content", external=True),
    OperationSpec("dlvps", "Download to VPS", "receipt", external=True),
    OperationSpec("seed", "Seed with qBittorrent", "torrent", external=True),
    OperationSpec(
        "anibt", "Publish to Anibt", "torrent", can_rebuild=False,
        external=True, irreversible=True,
    ),
)

OPERATION_REGISTRY = {item.name: item for item in _SPECS}
OPERATION_NAMES = tuple(OPERATION_REGISTRY)


def operation_spec(name: str) -> OperationSpec:
    try:
        return OPERATION_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown operation: {name}") from exc
