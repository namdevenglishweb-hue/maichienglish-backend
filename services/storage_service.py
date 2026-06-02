"""Provider-agnostic file storage interface for media uploads.

Holds the abstract `StorageService` ABC plus the shared validation
constants (`EXT_FOR_MIME`, `ALLOWED_TYPES`, `SIZE_LIMITS`) used by both
the Pydantic request schema and the adapter implementations. See
`MEDIA_UPLOAD.md` for the full design.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Module-level constants (shared by validators + adapters)
# ---------------------------------------------------------------------------

EXT_FOR_MIME: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    # video — for student speaking-answer recordings (WRITING_SPEAKING.md §11)
    "video/webm": ".webm",
    "video/mp4": ".mp4",
}

ALLOWED_TYPES: dict[str, set[str]] = {
    "audio": {
        "audio/mpeg",
        "audio/mp4",
        "audio/x-m4a",
        "audio/m4a",
        "audio/wav",
        "audio/webm",
    },
    "images": {"image/png", "image/jpeg", "image/webp"},
    # Student-uploaded speaking answers — audio OR video.
    # See WRITING_SPEAKING.md §11.1 + FRONTEND_WRITING_SPEAKING.md §3.2.1.
    "student_recordings": {
        "audio/webm", "audio/mp4", "audio/mpeg", "audio/wav",
        "video/webm", "video/mp4",
    },
}

SIZE_LIMITS: dict[str, int] = {
    "audio": 50 * 1024 * 1024,
    "images": 10 * 1024 * 1024,
    "student_recordings": 100 * 1024 * 1024,    # video is larger than audio
}

# ---------------------------------------------------------------------------
# Result + interface
# ---------------------------------------------------------------------------


@dataclass
class UploadResult:
    upload_url: str
    public_url: str
    token: str
    path: str
    bucket: str


class StorageService(ABC):
    """Provider-agnostic file storage interface."""

    @abstractmethod
    async def create_signed_upload(
        self,
        bucket: str,
        content_type: str,
        file_size_bytes: int,
    ) -> UploadResult:
        """Generate a signed URL for direct browser-to-storage upload.

        The implementation derives the file extension from `content_type`
        via EXT_FOR_MIME — filename is NOT trusted (admin could spoof
        `evil.exe` as `audio/mpeg`). A UUID is generated for the path.

        Args:
            bucket: "audio" or "images" (already validated by caller).
            content_type: MIME type (already validated by caller).
            file_size_bytes: size in bytes (already validated by caller —
                passed through so future S3 adapter can pick multipart
                vs single PUT).
        """
        ...

    @abstractmethod
    async def delete_file(self, bucket: str, path: str) -> None:
        """Delete a file from storage. Used by future orphan cleanup."""
        ...


def get_storage_service() -> StorageService:
    """Factory — returns the configured provider adapter.

    Controlled by `STORAGE_PROVIDER` env var (default "supabase").
    """
    from config.settings import get_settings

    settings = get_settings()
    provider = settings.storage_provider
    if provider == "s3":
        from services.adapters.s3_storage import S3StorageAdapter

        return S3StorageAdapter(settings)
    return _build_supabase_adapter(settings)


def _build_supabase_adapter(settings) -> StorageService:
    from services.adapters.supabase_storage import SupabaseStorageAdapter

    return SupabaseStorageAdapter(settings)
