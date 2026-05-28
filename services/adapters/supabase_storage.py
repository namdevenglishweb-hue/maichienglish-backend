"""Supabase Storage adapter — uses the official `supabase-py` SDK.

Sync SDK calls are dispatched via `asyncio.to_thread()` so they don't
block the FastAPI event loop. See `MEDIA_UPLOAD.md` §6.3 for rationale.
"""

import asyncio
import logging
import uuid

from supabase import Client, create_client

from services.storage_service import EXT_FOR_MIME, StorageService, UploadResult

logger = logging.getLogger(__name__)


class SupabaseStorageAdapter(StorageService):
    def __init__(self, settings) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "Supabase storage adapter requires SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY env vars."
            )
        self.base_url: str = settings.supabase_url.rstrip("/")
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )

    async def create_signed_upload(
        self,
        bucket: str,
        content_type: str,
        file_size_bytes: int,
    ) -> UploadResult:
        # Extension derived from contentType, not filename — see §8.3.
        ext = EXT_FOR_MIME[content_type]
        path = f"{uuid.uuid4()}{ext}"

        # supabase-py v2 returns dict with 'signedUrl', 'token', 'path'.
        # TTL is fixed at 2h by Supabase, not configurable here.
        result = await asyncio.to_thread(
            self.client.storage.from_(bucket).create_signed_upload_url,
            path,
        )

        signed = result.get("signedUrl") or result.get("signed_url") or ""
        if signed and not signed.startswith("http"):
            signed = f"{self.base_url}/storage/v1{signed}"

        token = result.get("token") or ""
        public_url = f"{self.base_url}/storage/v1/object/public/{bucket}/{path}"

        return UploadResult(
            upload_url=signed,
            public_url=public_url,
            token=token,
            path=path,
            bucket=bucket,
        )

    async def delete_file(self, bucket: str, path: str) -> None:
        await asyncio.to_thread(
            self.client.storage.from_(bucket).remove,
            [path],
        )
