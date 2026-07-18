"""ASS analysis, normalization, classification, and font requirements."""

from .analyzer import build_analysis
from .constants import ANALYSIS_BUNDLE_SCHEMA_VERSION, ANALYSIS_SCHEMA_VERSION
from .event_ids import (
    build_event_ids, canonical_event_input, canonical_text_input, canonicalize_segments,
    event_id_strategy, hash_canonical_input, normalize_visible_text,
    recompute_event_id, recompute_text_id, resolve_input_fields, text_id_strategy,
)
from .execution import (
    ANALYZE_ASS_STAGE, NORMALIZE_ASS_STAGE, RECONSTRUCT_ASS_STAGE,
    run_ass_analysis, run_ass_normalization, run_ass_reconstruction,
)
from .io import (
    combine_analyses, export_analysis, export_analysis_bundle, get_analysis_event,
    get_bundle_event, index_analysis_events, index_bundle_events, load_analysis,
    load_analysis_bundle, serialize_analysis, validate_analysis_payload,
)
from .parser import parse_ass_document, read_ass_document
from .profiles import (
    AssAnalysisProfile, AssMetadataPolicy, AssReconstructionProfile,
    EffectCollapsePolicy, EventIdPolicy, ProjectGarbagePolicy, TextSplitRule,
)
from .reconstruction import ReconstructionResult, encode_reconstructed, reconstruct_standard_ass

__all__ = [
    "ANALYSIS_BUNDLE_SCHEMA_VERSION", "ANALYSIS_SCHEMA_VERSION", "ANALYZE_ASS_STAGE",
    "NORMALIZE_ASS_STAGE", "RECONSTRUCT_ASS_STAGE", "AssAnalysisProfile",
    "AssMetadataPolicy", "AssReconstructionProfile", "EffectCollapsePolicy", "EventIdPolicy",
    "ProjectGarbagePolicy", "ReconstructionResult", "TextSplitRule", "build_analysis",
    "build_event_ids", "canonical_event_input", "canonical_text_input", "canonicalize_segments",
    "combine_analyses", "event_id_strategy", "text_id_strategy", "export_analysis", "export_analysis_bundle",
    "get_analysis_event", "get_bundle_event", "hash_canonical_input",
    "index_analysis_events", "index_bundle_events", "load_analysis",
    "load_analysis_bundle", "parse_ass_document", "read_ass_document",
    "normalize_visible_text", "recompute_event_id", "recompute_text_id",
    "reconstruct_standard_ass", "resolve_input_fields",
    "run_ass_analysis", "run_ass_normalization", "run_ass_reconstruction",
    "encode_reconstructed", "serialize_analysis", "validate_analysis_payload",
]
