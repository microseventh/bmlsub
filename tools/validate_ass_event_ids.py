from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from bmlsub.ass_analysis import (
    AssAnalysisProfile, EventIdPolicy, build_analysis, combine_analyses,
    export_analysis, export_analysis_bundle, get_analysis_event, get_bundle_event,
    index_analysis_events, index_bundle_events, load_analysis, load_analysis_bundle,
    parse_ass_document,
)
from bmlsub.state.models import ArtifactRecord, ValidationStatus, utc_now


def artifact(path: Path, index: int) -> ArtifactRecord:
    stat = path.stat()
    return ArtifactRecord(
        artifact_id=f"validation-subtitle-{index}",
        run_id="validation", stage_id="validation", episode_id="example",
        artifact_type="source.subtitle.ass", path=path,
        size=stat.st_size, mtime_ns=stat.st_mtime_ns,
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        validation_status=ValidationStatus.VALID, created_at=utc_now(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ASS event-ID modes against explicitly supplied subtitle files.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("ass", nargs="+", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    analyses = []
    source_hashes = {}
    modes = {
        "visible_text": EventIdPolicy(),
        "raw_text": EventIdPolicy(mode="raw_text"),
        "fields": EventIdPolicy(mode="fields"),
    }
    mode_summaries = {}
    for mode, policy in modes.items():
        mode_summaries[mode] = []
        for index, source in enumerate(args.ass):
            content = source.read_text(encoding="utf-8-sig")
            document = parse_ass_document(content, path=source)
            value = build_analysis(
                document, source_artifact=artifact(source, index),
                profile=AssAnalysisProfile(event_ids=policy),
            )
            statuses = Counter(item["id"]["status"] for item in value["events"]["items"])
            mode_summaries[mode].append({
                "source": source.name,
                "event_count": len(document.events),
                "statuses": dict(statuses),
                "duplicate_fallback_count": sum(
                    item["id"]["duplicate_fallback"] for item in value["events"]["items"]
                ),
            })
            if mode == "visible_text":
                analyses.append(value)
                source_hashes[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
                export_analysis(value, args.output / f"{source.name}.analysis.json")

    bundle = combine_analyses(analyses)
    bundle_path = args.output / "analysis-bundle.json"
    export_analysis_bundle(bundle, bundle_path)
    self_check = {
        "analysis_roundtrip": all(
            load_analysis(args.output / f"{source.name}.analysis.json") == analysis
            for source, analysis in zip(args.ass, analyses)
        ),
        "bundle_roundtrip": load_analysis_bundle(bundle_path) == bundle,
        "analysis_index_lookup": True,
        "bundle_index_lookup": True,
    }
    for analysis in analyses:
        analysis_index = index_analysis_events(analysis)
        if not analysis_index:
            continue
        sample_event_id = next(iter(analysis_index))
        bundle_index = index_bundle_events(bundle)
        self_check["analysis_index_lookup"] = (
            self_check["analysis_index_lookup"]
            and get_analysis_event(analysis_index, sample_event_id) is not None
        )
        self_check["bundle_index_lookup"] = (
            self_check["bundle_index_lookup"]
            and get_bundle_event(
                bundle_index, analysis["source"]["artifact_id"], sample_event_id,
            ) is not None
        )

    summary = {
        "schema_version": "ass-event-id-validation-v1",
        "mode_summaries": mode_summaries,
        "source_sha256": source_hashes,
        "bundle": bundle["statistics"],
        "roundtrip_and_indexes": self_check,
    }
    (args.output / "verification-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
