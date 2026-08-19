from typing import Any, ClassVar


class Vision24Error(Exception):
    code = "internal_error"
    http_status = 500
    default_message = "Something went wrong."
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class ValidationError(Vision24Error):
    code = "validation_error"
    http_status = 400
    default_message = "The request was not valid."


class UnauthenticatedError(Vision24Error):

    code = "unauthenticated"
    http_status = 401
    default_message = "Authentication is required."
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class ForbiddenError(Vision24Error):

    code = "forbidden"
    http_status = 403
    default_message = "You do not have permission to do that."


class NotFoundError(Vision24Error):
    code = "not_found"
    http_status = 404
    default_message = "Not found."


class ConflictError(Vision24Error):
    code = "conflict"
    http_status = 409
    default_message = "That conflicts with the current state."


class JobBusyError(ConflictError):
    code = "job_busy"
    default_message = "This source already has a queued or running job."


class UnavailableError(Vision24Error):
    code = "unavailable"
    http_status = 503
    default_message = "That capability is not available on this deployment."


class TTSUnavailableError(UnavailableError):
    code = "tts_unavailable"
    default_message = "Speech synthesis is not available on this deployment."


class UpstreamError(Vision24Error):
    code = "upstream_error"
    http_status = 502
    default_message = "An upstream service failed."


class AIProviderError(UpstreamError):
    code = "ai_provider_error"
    default_message = "The AI provider could not be reached."


class StorageError(UpstreamError):
    code = "storage_error"
    default_message = "Object storage is unreachable."


class CaptureError(UpstreamError):
    code = "capture_error"
    default_message = "Could not capture from the camera."


class ReplayError(UpstreamError):
    code = "replay_error"
    default_message = "Could not start the replay feed."


class ConfigurationError(Vision24Error):
    code = "configuration_error"
    default_message = "The server is misconfigured."


class AnalysisError(Vision24Error):
    code = "analysis_error"
    default_message = "Video analysis failed."
