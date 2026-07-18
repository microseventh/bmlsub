"""Media inspection, registration, track selection, and extraction."""

from .extraction import (
    ATTACHMENT_PROFILE_VERSION,
    ATTACHMENT_STAGE,
    AUDIO_PROFILE_VERSION,
    AUDIO_STAGE,
    SUBTITLE_PROFILE_VERSION,
    SUBTITLE_STAGE,
    list_media_tracks,
    run_attachment_extraction,
    run_audio_extraction,
    run_subtitle_extraction,
)
from .models import MediaStreamSummary, MediaSummary, VideoPurpose
from .probe import FFprobeClient
from .tracks import (
    ATTACHMENT_NAMING_VERSION,
    ATTACHMENT_VALIDATOR_VERSION,
    MEDIA_VALIDATOR_VERSION,
    OUTPUT_NAMING_VERSION,
    TRACK_SELECTION_VERSION,
    AttachmentCandidate,
    AudioOutputMode,
    TrackCandidate,
    TrackKind,
    attachment_candidates_from_artifact,
    candidates_from_artifact,
    select_track,
)
from .video import (
    VIDEO_PROBE_SCHEMA_VERSION,
    VIDEO_REGISTRATION_STAGE,
    VideoRegistrationOptions,
    get_current_artifact,
    list_current_artifacts,
    resolve_video,
    run_video_registration,
)

__all__ = [
    "ATTACHMENT_NAMING_VERSION", "ATTACHMENT_PROFILE_VERSION", "ATTACHMENT_STAGE",
    "ATTACHMENT_VALIDATOR_VERSION", "AUDIO_PROFILE_VERSION", "AUDIO_STAGE",
    "AttachmentCandidate", "AudioOutputMode", "FFprobeClient",
    "MEDIA_VALIDATOR_VERSION", "MediaStreamSummary", "MediaSummary",
    "OUTPUT_NAMING_VERSION", "SUBTITLE_PROFILE_VERSION", "SUBTITLE_STAGE",
    "TRACK_SELECTION_VERSION", "TrackCandidate", "TrackKind",
    "VIDEO_PROBE_SCHEMA_VERSION", "VIDEO_REGISTRATION_STAGE", "VideoPurpose",
    "VideoRegistrationOptions", "attachment_candidates_from_artifact",
    "candidates_from_artifact", "get_current_artifact", "list_current_artifacts",
    "list_media_tracks", "resolve_video", "run_attachment_extraction",
    "run_audio_extraction", "run_subtitle_extraction", "run_video_registration",
    "select_track",
]
