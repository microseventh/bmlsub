"""Anibt.net publish API adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

import requests

from ..execution.errors import BmlsubError, ErrorCode
from .external_profiles import AnibtPublishProfile


ANIBT_ADAPTER_VERSION = "anibt-adapter-v7"
ANIBT_RECEIPT_SCHEMA = "anibt-receipt-v2"
_ANIBT_TIMEOUT = (10.0, 60.0)
_MAX_ERROR_TEXT = 500
_MAX_RESPONSE_TEXT = 4_000
_API_FIELD_NAMES = {
    "anime_id_type": "animeIdType",
    "anime_id": "animeId",
    "title": "title",
    "bgm_id": "bgmId",
    "episode_key": "episodeKey",
    "resolution": "resolution",
    "language": "language",
    "subtitle": "subtitle",
    "format": "format",
    "version": "version",
    "file_size": "fileSize",
    "trackers": "trackers",
    "notes": "notes",
    "preview": "preview",
    "nyaa": "nyaa",
    "nyaa_category": "nyaaCategory",
    "nyaa_complete": "nyaaComplete",
    "nyaa_remake": "nyaaRemake",
    "nyaa_description": "nyaaDescription",
    "nyaa_information": "nyaaInformation",
}


class AnibtClient(Protocol):
    @property
    def version(self) -> str: ...

    def publish(self, *, torrent_path: Path, profile: AnibtPublishProfile,
                api_url: str, token: str) -> dict[str, Any]: ...


class RequestsAnibtClient:
    @property
    def version(self) -> str:
        return ANIBT_ADAPTER_VERSION

    def publish(self, *, torrent_path: Path, profile: AnibtPublishProfile,
                api_url: str, token: str) -> dict[str, Any]:
        if not profile.use_torrent_file:
            raise BmlsubError(
                "anibt anime releases require multipart torrent upload",
                code=ErrorCode.INVALID_INPUT,
            )
        if not profile.trackers:
            profile = replace(profile, trackers=("https://tracker.anibt.net/announce",))
        return self._publish_multipart(torrent_path, profile, api_url, token)

    def _publish_multipart(self, torrent_path: Path, profile: AnibtPublishProfile,
                           api_url: str, token: str) -> dict[str, Any]:
        data = self._multipart_fields(profile)
        with torrent_path.open("rb") as torrent_file:
            files = {
                "torrent": (
                    torrent_path.name,
                    torrent_file,
                    "application/x-bittorrent",
                ),
            }
            try:
                response = requests.post(
                    api_url,
                    data=data,
                    files=files,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=_ANIBT_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise BmlsubError(
                    f"anibt API request failed: {type(exc).__name__}",
                    code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                ) from exc
        return self._handle_response(response)

    @staticmethod
    def _multipart_fields(profile: AnibtPublishProfile) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = []
        values = profile.api_fields()
        for attr_name, api_name in _API_FIELD_NAMES.items():
            value = values[attr_name]
            if attr_name in ("language", "trackers"):
                if value:
                    fields.append((api_name, json.dumps(list(value), ensure_ascii=False)))
            elif isinstance(value, bool):
                if value or (profile.nyaa and attr_name in ("nyaa_complete", "nyaa_remake")):
                    fields.append((api_name, "true" if value else "false"))
            elif value is not None and value != "":
                fields.append((api_name, str(value)))
        return fields

    @staticmethod
    def _handle_response(response: requests.Response) -> dict[str, Any]:
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    raw_detail = payload.get("error") or payload.get("message") or ""
                    details = payload.get("details")
                    if isinstance(details, dict) and details:
                        raw_detail = f"{raw_detail}: {json.dumps(details, ensure_ascii=False, sort_keys=True)}"
                    detail = str(raw_detail)
            except (ValueError, TypeError):
                pass
            detail = _bounded_text(detail, _MAX_ERROR_TEXT)
            suffix = f": {detail}" if detail else ""
            raise BmlsubError(
                f"anibt API returned HTTP {response.status_code}{suffix}",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            )
        if len(response.content) > _MAX_RESPONSE_TEXT:
            raise BmlsubError(
                "anibt API returned an oversized response",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            )
        try:
            payload = response.json(object_pairs_hook=dict)
        except ValueError as exc:
            raise BmlsubError(
                "anibt API returned non-JSON response",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            ) from exc
        validate_anibt_response(payload)
        return payload


def _bounded_text(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit] + "…"


def validate_anibt_response(payload: dict[str, Any]) -> None:
    """Validate the anibt.net API response JSON structure.

    Raises BmlsubError if the response indicates a failed publish
    or has an unexpected structure.
    """
    if not isinstance(payload, dict):
        raise BmlsubError(
            "anibt API response is not a JSON object",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        )
    ok = payload.get("ok")
    if ok is not True:
        error_msg = payload.get("error") or payload.get("message") or "unknown error"
        raise BmlsubError(
            f"anibt publish failed: {error_msg}",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        )
    data = payload.get("data")
    result = payload.get("result")
    if data is not None and not isinstance(data, dict):
        raise BmlsubError(
            "anibt API response data is not a JSON object",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        )
    if result is not None and not isinstance(result, dict):
        raise BmlsubError(
            "anibt API response result is not a JSON object",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        )


def build_receipt_payload(*, torrent_artifact_id: str, profile: AnibtPublishProfile,
                          api_response: dict[str, Any], mode: str,
                          torrent_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    response_data = api_response.get("data") or api_response.get("result")
    if not isinstance(response_data, dict):
        response_data = {}
    response_summary = {
        key: value for key, value in response_data.items()
        if key in {"id", "_id", "releaseId", "previewId", "previewUrl", "url", "expiresAt", "expiresIn"}
        and isinstance(value, (str, int, float, bool))
    }
    for key in ("id", "releaseId", "previewId", "previewUrl", "url", "expiresAt", "expiresIn", "message"):
        value = api_response.get(key)
        if isinstance(value, (str, int, float, bool)):
            response_summary.setdefault(key, value)
    payload: dict[str, Any] = {
        "schema_version": ANIBT_RECEIPT_SCHEMA,
        "torrent_artifact_id": torrent_artifact_id,
        "profile": profile.receipt_summary(),
        "publish": {
            "ok": True,
            "response": response_summary,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "preview": profile.preview,
        },
    }
    if torrent_meta:
        payload["torrent"] = torrent_meta
    return payload
