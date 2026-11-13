"""Lazy Qwen3-ASR adapter; bmlsub passes the complete WAV in one call."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Mapping

from ..execution import BmlsubError, ErrorCode


DEFAULT_QWEN3_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"


class MlxQwen3AsrBackend:
    """Adapt native MLX Qwen results to the transcript-v1 segment interface.

    Qwen handles long audio internally. Its chunk boundaries are explicitly
    labelled as such; they are not word- or utterance-aligned timestamps.
    """

    def version(self) -> str:
        try:
            return importlib.metadata.version("mlx-qwen3-asr")
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def transcribe(self, audio_path: Path, *, model: str, language: str,
                   decoding: Mapping[str, Any]) -> Mapping[str, Any]:
        unsupported = set(decoding) - {"context", "max_new_tokens"}
        if unsupported:
            raise ValueError("unsupported Qwen3 decoding options: " + ", ".join(sorted(unsupported)))
        if "context" in decoding and not isinstance(decoding["context"], str):
            raise ValueError("Qwen3 context must be a string")
        limit = decoding.get("max_new_tokens")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
            raise ValueError("Qwen3 max_new_tokens must be a positive integer or null")
        try:
            from mlx_qwen3_asr import transcribe
        except ImportError as exc:
            raise BmlsubError(
                "mlx-qwen3-asr is required; install bmlsub[transcription] in the active environment",
                code=ErrorCode.INPUT_MISSING,
                details={"dependency": "mlx-qwen3-asr", "engine": "qwen3"},
            ) from exc
        try:
            result = transcribe(
                str(audio_path), model=model, language=language,
                return_chunks=True, return_timestamps=False, **dict(decoding),
            )
        except Exception as exc:
            raise BmlsubError(
                "MLX Qwen3-ASR transcription failed",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=True,
                details={"engine": "qwen3", "exception_type": type(exc).__name__},
            ) from exc
        chunks = result.chunks
        if not isinstance(chunks, (list, tuple)):
            raise BmlsubError(
                "Qwen3 did not return audio chunk boundaries",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                details={"engine": "qwen3"},
            )
        if not all(isinstance(item, Mapping) for item in chunks):
            raise BmlsubError("Qwen3 returned invalid audio chunks",
                              code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                              details={"engine": "qwen3"})
        truncated = bool(result.truncated) or any(item.get("truncated") for item in chunks)
        if truncated:
            raise BmlsubError(
                "Qwen3 transcript reached its token limit; increase max_new_tokens and retry",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                details={"engine": "qwen3", "truncated": True,
                         "chunk_indices": [item.get("chunk_index") for item in chunks
                                           if item.get("truncated")]},
            )
        if result.text.strip() and not chunks:
            raise BmlsubError(
                "Qwen3 returned text without corresponding audio chunks",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                details={"engine": "qwen3"},
            )
        return {
            "text": result.text, "language": result.language,
            "segments": [dict(item) for item in chunks],
            "engine": "qwen3", "timestamp_granularity": "audio_chunk",
            "asr_metadata": {
                "finish_reason": result.finish_reason, "truncated": truncated,
                "internal_chunk_count": len(chunks),
                "review_required_chunk_indices": [item.get("chunk_index", index)
                                                   for index, item in enumerate(chunks)
                                                   if item.get("finish_reason") == "repetition"],
                "chunks": [dict(item) for item in chunks],
            },
        }
