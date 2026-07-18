#!/usr/bin/env python3
"""Benchmark ASS Event ID hashing and lookup strategies."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import random
import statistics
import sys
import time
import tracemalloc
from typing import Any, Callable
import zlib

import xxhash

from bmlsub.ass_analysis.event_ids import canonical_event_input
from bmlsub.ass_analysis.parser import read_ass_document
from bmlsub.ass_analysis.text import text_features


HashFunction = Callable[[bytes], str]
HASHERS: dict[str, HashFunction] = {
    "xxh3_64": lambda value: xxhash.xxh3_64_hexdigest(value, seed=0),
    "xxh64": lambda value: xxhash.xxh64_hexdigest(value, seed=0),
    "blake2b_64": lambda value: hashlib.blake2b(value, digest_size=8).hexdigest(),
    "sha256_64": lambda value: hashlib.sha256(value).hexdigest()[:16],
    "crc32": lambda value: f"{zlib.crc32(value) & 0xffffffff:08x}",
}
MODES = ("visible_text", "raw_text", "all_fields")


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def timed_rounds(operation: Callable[[], Any], rounds: int) -> dict[str, float]:
    operation()
    elapsed = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        elapsed.append(time.perf_counter_ns() - started)
    return {
        "median_ns": statistics.median(elapsed),
        "p95_ns": percentile(elapsed, 0.95),
    }


def load_real_events(paths: list[Path]) -> list[tuple[Any, tuple[str, ...]]]:
    result = []
    for path in paths:
        document = read_ass_document(path)
        result.extend((event, document.event_format) for event in document.events)
    return result


def synthetic_events(count: int, seed: int) -> list[tuple[dict[str, str], tuple[str, ...]]]:
    randomizer = random.Random(seed)
    event_format = ("layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text")
    result = []
    for index in range(count):
        text = f"line-{randomizer.randrange(max(count // 2, 1))}-{index % 97}"
        if index % 19 == 0:
            text = r"{\blur1.6}" + text
        if index % 211 == 0:
            text = r"{\p1}m 0 0 l 10 10{\p0}"
        result.append(({
            "layer": str(index % 5), "start": f"0:00:{index % 60:02d}.00",
            "end": f"0:00:{(index + 2) % 60:02d}.00", "style": f"Style-{index % 12}",
            "name": "", "marginl": "0", "marginr": "0", "marginv": "0",
            "effect": "", "text": text,
        }, event_format))
    return result


def canonical_inputs(events: list[tuple[Any, tuple[str, ...]]], mode: str) -> list[bytes]:
    from bmlsub.ass_analysis.models import AssEvent

    result = []
    for ordinal, (value, event_format) in enumerate(events):
        event = value if isinstance(value, AssEvent) else AssEvent(
            line_number=ordinal + 1, ordinal=ordinal, record_type="dialogue",
            raw="", fields=value,
        )
        selected_mode = "line_fields" if mode == "all_fields" else mode
        plain = text_features(event.text)["plain_text"] if mode == "visible_text" else None
        canonical, _, _ = canonical_event_input(
            event, event_format=event_format, mode=selected_mode, plain_text=plain,
        )
        result.append(canonical)
    return result


def benchmark_hashing(events: list[tuple[Any, tuple[str, ...]]], rounds: int) -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for mode in MODES:
        canonical_timing = timed_rounds(lambda: canonical_inputs(events, mode), rounds)
        inputs = canonical_inputs(events, mode)
        algorithms = {}
        for name, hasher in HASHERS.items():
            timing = timed_rounds(lambda h=hasher: [h(value) for value in inputs], rounds)
            output = [hasher(value) for value in inputs]
            output_inputs: dict[str, set[bytes]] = {}
            for digest, canonical in zip(output, inputs):
                output_inputs.setdefault(digest, set()).add(canonical)
            elapsed = timing["median_ns"]
            algorithms[name] = {
                **timing,
                "ns_per_event": elapsed / max(len(inputs), 1),
                "events_per_second": len(inputs) * 1_000_000_000 / max(elapsed, 1),
                "observed_hash_collisions": sum(
                    len(values) - 1 for values in output_inputs.values() if len(values) > 1
                ),
            }
        fastest_core = min(algorithms, key=lambda name: algorithms[name]["median_ns"])
        end_to_end = {
            name: canonical_timing["median_ns"] + data["median_ns"]
            for name, data in algorithms.items()
        }
        modes[mode] = {
            "canonicalization": canonical_timing,
            "input_lengths": {
                "minimum": min(map(len, inputs), default=0),
                "median": statistics.median(map(len, inputs)) if inputs else 0,
                "maximum": max(map(len, inputs), default=0),
            },
            "distinct_inputs": len(set(inputs)),
            "duplicate_inputs": len(inputs) - len(set(inputs)),
            "algorithms": algorithms,
            "fastest_hash_core": fastest_core,
            "fastest_end_to_end": min(end_to_end, key=end_to_end.get),
        }
    return modes


def benchmark_lookups(ids: list[str], rounds: int, seed: int) -> dict[str, Any]:
    events = [{"event_id": value, "ordinal": index} for index, value in enumerate(ids)]
    list_index = list(events)
    dict_index = {item["event_id"]: item for item in events}
    set_index = set(ids)
    source_id = "benchmark-source"
    bundle_index = {(source_id, item["event_id"]): item for item in events}
    randomizer = random.Random(seed)
    hit_count = min(10_000, len(ids))
    hits = randomizer.sample(ids, hit_count) if hit_count else []
    misses = [f"e_{index:016x}" for index in range(hit_count)]
    queries = hits + misses
    randomizer.shuffle(queries)

    def measure_build(factory: Callable[[], Any]) -> dict[str, float]:
        tracemalloc.start()
        timing = timed_rounds(factory, rounds)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {**timing, "peak_bytes": peak}

    structures = {
        "list_scan": {
            "build": measure_build(lambda: list(events)),
            "lookup": timed_rounds(
                lambda: [next((item for item in list_index if item["event_id"] == query), None)
                         for query in queries], rounds,
            ),
        },
        "dict_event": {
            "build": measure_build(lambda: {item["event_id"]: item for item in events}),
            "lookup": timed_rounds(lambda: [dict_index.get(query) for query in queries], rounds),
        },
        "set_membership": {
            "build": measure_build(lambda: set(ids)),
            "lookup": timed_rounds(lambda: [query in set_index for query in queries], rounds),
        },
        "bundle_dict": {
            "build": measure_build(lambda: {(source_id, item["event_id"]): item for item in events}),
            "lookup": timed_rounds(
                lambda: [bundle_index.get((source_id, query)) for query in queries], rounds,
            ),
        },
    }
    for data in structures.values():
        elapsed = data["lookup"]["median_ns"]
        data["lookup"]["queries_per_second"] = len(queries) * 1_000_000_000 / max(elapsed, 1)
    return {
        "query_count": len(queries), "hit_count": len(hits), "miss_count": len(misses),
        "structures": structures,
        "fastest_existence_lookup": min(
            ("list_scan", "set_membership"),
            key=lambda name: structures[name]["lookup"]["median_ns"],
        ),
        "fastest_event_retrieval": min(
            ("list_scan", "dict_event"),
            key=lambda name: structures[name]["lookup"]["median_ns"],
        ),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# ASS Event ID Benchmark", "",
        f"- Selected production algorithm: `{report['selected_production_algorithm']}`",
        f"- Dataset events: {report['dataset']['event_count']}",
        f"- Fastest existence lookup: `{report['lookup']['fastest_existence_lookup']}`",
        f"- Fastest Event retrieval: `{report['lookup']['fastest_event_retrieval']}`", "",
        "## Hashing", "",
    ]
    for mode, data in report["hashing"].items():
        lines.extend([
            f"### {mode}", "",
            f"- Fastest hash core: `{data['fastest_hash_core']}`",
            f"- Fastest end-to-end: `{data['fastest_end_to_end']}`",
            f"- Distinct inputs: {data['distinct_inputs']}",
            f"- Duplicate inputs: {data['duplicate_inputs']}", "",
        ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ass", action="append", default=[], type=Path)
    parser.add_argument("--synthetic", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    if args.rounds <= 0 or args.synthetic < 0:
        parser.error("rounds must be positive and synthetic must be nonnegative")
    events = load_real_events(args.ass)
    if args.synthetic:
        events.extend(synthetic_events(args.synthetic, args.seed))
    if not events:
        parser.error("provide at least one --ass file or --synthetic count")
    hashing = benchmark_hashing(events, args.rounds)
    visible_inputs = canonical_inputs(events, "visible_text")
    ids = ["e_" + xxhash.xxh3_64_hexdigest(value, seed=0) for value in visible_inputs]
    report = {
        "schema_version": "ass-event-id-benchmark-v1",
        "selected_production_algorithm": "xxh3_64",
        "environment": {
            "python": sys.version.split()[0], "platform": platform.platform(),
            "xxhash": xxhash.VERSION,
        },
        "parameters": {"seed": args.seed, "rounds": args.rounds},
        "dataset": {
            "event_count": len(events), "ass_files": [str(path) for path in args.ass],
            "synthetic_count": args.synthetic,
        },
        "hashing": hashing,
        "lookup": benchmark_lookups(ids, args.rounds, args.seed),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown_report(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
