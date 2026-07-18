"""Versioned constants for ASS analysis and normalization."""

ANALYSIS_SCHEMA_VERSION = "ass-analysis-v4"
LEGACY_ANALYSIS_SCHEMA_VERSIONS = {"ass-analysis-v1", "ass-analysis-v2", "ass-analysis-v3"}
ANALYSIS_BUNDLE_SCHEMA_VERSION = "ass-analysis-bundle-v2"
PARSER_VERSION = "ass-document-v1"
TAG_PARSER_VERSION = "ass-tags-v2"
CLASSIFIER_VERSION = "ass-classifier-v3"
EVENT_ID_STRATEGY_VERSION = "ass-event-all-fields-xxh3_64-v2"
EVENT_ID_INPUT_VERSION = "ass-event-id-input-v2"
TEXT_ID_STRATEGY_VERSION = "ass-visible-text-xxh3_64-v1"
TEXT_ID_INPUT_VERSION = "ass-text-id-input-v1"
EFFECT_GROUPER_VERSION = "ass-effect-grouper-v1"
FONT_RESOLVER_VERSION = "ass-font-resolver-v1"
SERIALIZER_VERSION = "ass-serializer-v1"
RECONSTRUCTOR_VERSION = "ass-reconstructor-v4"
ANALYSIS_VALIDATOR_VERSION = "ass-analysis-validator-v4"
NORMALIZED_VALIDATOR_VERSION = "ass-normalized-validator-v1"
RECONSTRUCTED_VALIDATOR_VERSION = "ass-reconstructed-validator-v4"
ANALYSIS_NAMING_VERSION = "ass-analysis-naming-v1"
NORMALIZED_NAMING_VERSION = "ass-normalized-naming-v1"
RECONSTRUCTED_NAMING_VERSION = "ass-reconstructed-naming-v1"

ASS_SUBTITLE_TYPES = {
    "source.subtitle.ass",
    "generated.subtitle.ass",
    "generated.subtitle.ass.normalized",
    "subtitle.cht.ass",
    "workstation.subtitle.chs",
    "workstation.subtitle.cht",
    "workstation.subtitle.delivery.cht",
}
FONT_ARTIFACT_TYPES = {"source.font", "generated.font"}
VIDEO_ARTIFACT_TYPES = {"source.video", "reference.video", "generated.video.hevc"}
