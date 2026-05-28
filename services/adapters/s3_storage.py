"""AWS S3 adapter — placeholder until needed.

When implemented, this will use boto3's `generate_presigned_url` for the
upload URL. The `file_size_bytes` arg lets it choose multipart vs single
PUT (S3 best practice is multipart >5MB).
"""

from services.storage_service import StorageService, UploadResult


class S3StorageAdapter(StorageService):
    def __init__(self, settings) -> None:
        raise NotImplementedError("S3 adapter not implemented yet")

    async def create_signed_upload(
        self,
        bucket: str,
        content_type: str,
        file_size_bytes: int,
    ) -> UploadResult:
        raise NotImplementedError

    async def delete_file(self, bucket: str, path: str) -> None:
        raise NotImplementedError
