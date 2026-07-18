"""Tracker resolution for deterministic torrent creation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol
from urllib.parse import urlparse

import requests


TRACKER_BASELINE_VERSION = "legacy-trackers-43-v1"
TRACKER_BASELINE_SHA256 = "3a684719b5b1e3875061cd134c8dfd0d27c8f6a446b3aaafa902deeb6cfc8c5a"
TRACKER_RESOLVER_VERSION = "ngosang-best-resolver-v1"

LEGACY_TRACKERS: tuple[str, ...] = (
    'http://nyaa.tracker.wf:7777/announce',
    'udp://open.stealth.si:80/announce',
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://exodus.desync.com:6969/announce',
    'udp://tracker.torrent.eu.org:451/announce',
    'https://tracker.bangumi.zip/announce',
    'https://tr.bangumi.moe:9696/announce',
    'http://tr.bangumi.moe:6969/announce',
    'udp://tr.bangumi.moe:6969/announce',
    'http://open.acgtracker.com:1096/announce',
    'http://208.67.16.113:8000/announce',
    'udp://208.67.16.113:8000/announce',
    'http://tracker.ktxp.com:6868/announce',
    'http://tracker.ktxp.com:7070/announce',
    'http://t2.popgo.org:7456/annonce',
    'http://bt.sc-ol.com:2710/announce',
    'http://share.camoe.cn:8080/announce',
    'http://61.154.116.205:8000/announce',
    'http://bt.rghost.net:80/announce',
    'http://tracker.openbittorrent.com:80/announce',
    'http://tracker.publicbt.com:80/announce',
    'http://tracker.prq.to/announce',
    'http://open.nyaatorrents.info:6544/announce',
    'http://opentracker.acgnx.se/announce',
    'http://tracker.acgnx.se/announce',
    'http://t.acg.rip:6699/announce',
    'https://tracker.gbitt.info:443/announce',
    'udp://91.216.110.52:451/announce',
    'udp://open.demonii.com:1337/announce',
    'udp://chihaya.toss.li:9696/announce',
    'udp://bt1.archive.org:6969/announce',
    'udp://bt2.archive.org:6969/announce',
    'udp://52.58.128.163:6969/announce',
    'udp://opentracker.i2p.rocks:6969/announce',
    'udp://retracker.lanta-net.ru:2710/announce',
    'udp://tracker.bittor.pw:1337/announce',
    'http://tracker.corpscorp.online:80/announce',
    'http://tracker.dler.org:6969/announce',
    'http://pow7.com:80/announce',
    'udp://ipv6.tracker.harry.lu:80/announce',
    'http://ipv6.tracker.harry.lu:80/announce',
    'http://tracker.ipv6tracker.ru/announce',
    'https://tracker.anibt.net/announce',
)

if len(LEGACY_TRACKERS) != 43:
    raise RuntimeError("legacy tracker baseline must contain exactly 43 entries")
if hashlib.sha256("\n".join(LEGACY_TRACKERS).encode("utf-8")).hexdigest() != TRACKER_BASELINE_SHA256:
    raise RuntimeError("legacy tracker baseline content changed")


class TrackerListClient(Protocol):
    def fetch(self, url: str, timeout: float) -> bytes: ...


class RequestsTrackerListClient:
    def fetch(self, url: str, timeout: float) -> bytes:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content


@dataclass(frozen=True)
class TrackerResolution:
    trackers: tuple[str, ...]
    best_trackers: tuple[str, ...]
    fetch_status: str
    response_sha256: str | None
    list_sha256: str
    error_type: str | None = None

    def provenance(self, url: str) -> dict[str, str | int | None]:
        return {
            "baseline_version": TRACKER_BASELINE_VERSION,
            "baseline_count": len(LEGACY_TRACKERS),
            "best_url": url,
            "best_count": len(self.best_trackers),
            "final_count": len(self.trackers),
            "fetch_status": self.fetch_status,
            "response_sha256": self.response_sha256,
            "list_sha256": self.list_sha256,
            "resolver_version": TRACKER_RESOLVER_VERSION,
            "error_type": self.error_type,
        }


def parse_tracker_list(payload: bytes) -> tuple[str, ...]:
    text = payload.decode("utf-8-sig")
    values: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        value = raw.strip()
        if not value:
            continue
        if not _valid_tracker(value):
            raise ValueError("tracker list contains an invalid URL")
        if value not in seen:
            seen.add(value)
            values.append(value)
    if not values:
        raise ValueError("tracker list is empty")
    return tuple(values)


def resolve_trackers(url: str, timeout: float, *, client: TrackerListClient | None = None) -> TrackerResolution:
    fetcher = client or RequestsTrackerListClient()
    try:
        payload = fetcher.fetch(url, timeout)
        best = parse_tracker_list(payload)
        response_sha256 = hashlib.sha256(payload).hexdigest()
        status = "succeeded"
        error_type = None
    except Exception as exc:
        best = ()
        response_sha256 = None
        status = "fallback"
        error_type = type(exc).__name__
    trackers = list(LEGACY_TRACKERS)
    seen = set(trackers)
    for tracker in best:
        if tracker not in seen:
            trackers.append(tracker)
            seen.add(tracker)
    final = tuple(trackers)
    return TrackerResolution(
        trackers=final,
        best_trackers=best,
        fetch_status=status,
        response_sha256=response_sha256,
        list_sha256=hashlib.sha256("\n".join(final).encode("utf-8")).hexdigest(),
        error_type=error_type,
    )


def _valid_tracker(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "udp"} or not parsed.netloc:
        return False
    return not any(character.isspace() for character in value)
