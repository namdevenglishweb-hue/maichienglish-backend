# Mai Chi English — Media Upload Specification

> **Scope**: how admin uploads audio/image files, how URLs flow into exam
> content, and how files are stored in Supabase Storage (with a provider-agnostic
> adapter layer for future migration to AWS S3 or other providers).
>
> Complements [`MAICHIENGLISH_BACKEND_PLAN.md`](MAICHIENGLISH_BACKEND_PLAN.md)
> §3.5 (materials JSONB) and §3.10 (storage buckets).

---

## Document Information

| Field | Value |
|-------|-------|
| **Version** | 1.0 |
| **Date** | May 2026 |
| **Status** | Design — not yet implemented |
| **Audience** | Backend developer, Frontend developer |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Where files are referenced](#2-where-files-are-referenced)
3. [Storage backend](#3-storage-backend)
4. [Upload flow (signed URL)](#4-upload-flow-signed-url)
5. [API specification](#5-api-specification)
6. [Backend architecture (adapter pattern)](#6-backend-architecture-adapter-pattern)
7. [Frontend integration guide](#7-frontend-integration-guide)
8. [Validation rules](#8-validation-rules)
9. [Bucket access policy](#9-bucket-access-policy)
10. [Orphan file cleanup](#10-orphan-file-cleanup)
11. [Provider migration](#11-provider-migration)
12. [Edge cases & decisions](#12-edge-cases--decisions)

---

## 1. Overview

Admin uploads audio and image files for use in exam content. Files are
stored in Supabase Storage; only their public URLs are persisted in the
database (inside JSONB fields). The upload flow uses **signed URLs** so
files go directly from the browser to storage without passing through the
backend server (Render), saving RAM and bandwidth.

```
Admin browser                   Backend (Render)              Supabase Storage
     │                               │                              │
     │  1. POST /api/admin/upload    │                              │
     │     {bucket, filename, ...}   │                              │
     ├──────────────────────────────►│                              │
     │                               │  2. createSignedUploadUrl()  │
     │                               ├─────────────────────────────►│
     │                               │◄─────────────────────────────┤
     │  3. {uploadUrl, publicUrl}    │                              │
     │◄──────────────────────────────┤                              │
     │                               │                              │
     │  4. PUT uploadUrl (binary)    │                              │
     ├─────────────────────────────────────────────────────────────►│
     │  5. 200 OK                    │                              │
     │◄─────────────────────────────────────────────────────────────┤
     │                               │                              │
     │  6. HEAD publicUrl (verify)   │                              │
     ├─────────────────────────────────────────────────────────────►│
     │  7. 200 OK (file exists)      │                              │
     │◄─────────────────────────────────────────────────────────────┤
     │                               │                              │
     │  8. Use publicUrl in          │                              │
     │     materials[i].url or       │                              │
     │     options[i].image_url      │                              │
```

Key properties:
- **File never passes through BE** — FE uploads directly to storage
- **`service_role` key stays server-side** — FE never sees it
- **BE generates the final URL before upload** — path is UUID-based,
  deterministic
- **FE verifies upload success** — HEAD request on `publicUrl` before
  using it in exam content

---

## 2. Where files are referenced

Files are referenced as URL strings inside JSONB columns. There is no
separate `files` or `uploads` table — URLs are the only link between
the database and storage.

### 2.1 Section materials (audio + images)

```jsonc
// sections.materials JSONB
[
  {
    "type": "audio",
    "label": "Track 1",
    "url": "https://xxx.supabase.co/storage/v1/object/public/audio/a1b2c3d4.mp3"
  },
  {
    "type": "image",
    "label": "Form",
    "url": "https://xxx.supabase.co/storage/v1/object/public/images/e5f6g7h8.png",
    "alt": "City Bus Tours form with blank fields"
  }
]
```

### 2.2 Question options (MC with images)

```jsonc
// questions.question_data JSONB
{
  "stem": "How did the woman travel?",
  "options": [
    {"image_url": "https://xxx.supabase.co/storage/v1/object/public/images/c3a1b2d4-5678-9abc-def0-111111111111.png"},
    {"image_url": "https://xxx.supabase.co/storage/v1/object/public/images/d4e5f6a7-1234-5678-abcd-222222222222.png"},
    {"image_url": "https://xxx.supabase.co/storage/v1/object/public/images/e8f9a0b1-abcd-ef01-2345-333333333333.png"}
  ],
  "correct_index": 2
}
```

### 2.3 Summary

| Location | JSONB field | File types |
|---|---|---|
| `sections.materials` | `[].url` (type=audio) | `.mp3`, `.m4a`, `.wav` |
| `sections.materials` | `[].url` (type=image) | `.png`, `.jpg`, `.webp` |
| `questions.question_data` | `.options[].image_url` | `.png`, `.jpg`, `.webp` |

---

## 3. Storage backend

### 3.1 Supabase Storage buckets

Two buckets (already created per
[DEPLOYMENT.md](DEPLOYMENT.md) §3.1):

| Bucket | Content | Current policy | Required policy |
|--------|---------|----------------|-----------------|
| `audio` | Listening audio files | **private** (as initially created) | **public-read** (see [§9](#9-bucket-access-policy)) |
| `images` | Question/section images | **private** | **public-read** |

> **Action required before first upload**: toggle both buckets from
> private to public on the Supabase Dashboard. See [§9.2](#92-setup-one-time-supabase-dashboard)
> for step-by-step. Without this, `publicUrl` returns 400/404 and the
> whole flow breaks.

### 3.2 Path convention

Files are stored with **UUID filenames** to avoid:
- Filename collisions
- URL encoding issues (Vietnamese/special characters in original names)
- Path traversal attacks

```
{bucket}/{uuid}.{ext}

Examples:
  audio/a1b2c3d4-5678-9abc-def0-123456789abc.mp3
  images/fedcba98-7654-3210-fedc-ba9876543210.png
```

The original filename is NOT preserved in storage. If needed for display,
store it in the JSONB alongside the URL (e.g., `"label": "ket-p5.mp3"`).

### 3.3 Public URL format

```
https://{PROJECT_REF}.supabase.co/storage/v1/object/public/{bucket}/{path}

Example:
https://zmptvzrfqusvhdfycdkd.supabase.co/storage/v1/object/public/audio/a1b2c3d4.mp3
```

This URL is stable, cacheable, and does not expire (public bucket).

---

## 4. Upload flow (signed URL)

### 4.1 Why signed URLs?

| Alternative | Problem |
|---|---|
| FE → BE → Storage (proxy) | File passes through Render (512MB RAM free tier). Large audio = OOM risk. |
| FE → Storage direct (anon key) | Exposes Supabase key to client. Complex RLS on storage. |
| **FE → BE for URL → FE uploads directly** | ✅ File skips BE. Key stays server-side. BE validates before issuing. |

### 4.2 Step-by-step

| Step | Actor | Action | Detail |
|------|-------|--------|--------|
| 1 | FE | `POST /api/admin/upload` | Send metadata: bucket, filename, contentType, fileSizeBytes |
| 2 | BE | Validate | Check admin role, bucket name, content type, file size |
| 3 | BE | Generate path | `{bucket}/{uuid}.{ext}` — deterministic |
| 4 | BE | Call Supabase API | `createSignedUploadUrl(path)` → signed URL (5 min TTL) |
| 5 | BE | Return to FE | `{uploadUrl, publicUrl, path, expiresIn}` |
| 6 | FE | `PUT uploadUrl` | Upload binary file directly to Supabase Storage |
| 7 | FE | `HEAD publicUrl` | **Verify** file exists before using URL |
| 8 | FE | Use `publicUrl` | Store in materials/question via section/question create/update API |

### 4.3 Timeline of a single upload

```
t=0s    FE calls POST /api/admin/upload
t=0.3s  BE returns {uploadUrl, publicUrl}
t=0.5s  FE starts PUT to uploadUrl
t=3s    Upload completes (e.g., 3MB audio on 8Mbps upload)
t=3.2s  FE sends HEAD publicUrl → 200 (file confirmed)
t=3.5s  FE shows "Upload thành công" + preview
t=...   Admin creates section with materials: [{type:"audio", url: publicUrl}]
```

---

## 5. API specification

### 5.1 Request signed upload URL

```
POST /api/admin/upload
Authorization: Bearer <admin-token>
```

**Request:**
```jsonc
{
  "bucket": "audio",                    // "audio" | "images"
  "filename": "ket-listening-p5.mp3",   // original name (used only to extract extension)
  "contentType": "audio/mpeg",          // MIME type (validated server-side)
  "fileSizeBytes": 3500000              // file size in bytes (validated against limits)
}
```

**Response 200:**
```jsonc
{
  "status": 200,
  "data": {
    "uploadUrl": "https://xxx.supabase.co/storage/v1/upload/sign/audio/a1b2c3d4.mp3?token=...",
    "publicUrl": "https://xxx.supabase.co/storage/v1/object/public/audio/a1b2c3d4.mp3",
    "path": "a1b2c3d4.mp3",
    "bucket": "audio",
    "expiresIn": 300
  }
}
```

**Errors:**

| Status | Detail | Cause |
|---|---|---|
| 403 | `Admin access required` | Caller is not admin |
| 400 | `Invalid bucket "xxx"; allowed: audio, images` | Bad bucket name |
| 400 | `Content type "text/plain" not allowed for bucket "audio"` | MIME mismatch |
| 400 | `File size 60000000 exceeds limit of 52428800 bytes for bucket "audio"` | Too large |
| 400 | `Cannot determine file extension from filename "noext"` | Missing extension |
| 503 | `Storage service unavailable` | Supabase API down |

### 5.2 Delete file (v1: Dashboard only)

The `StorageService` interface includes a `delete_file()` method (§6.2)
but **v1 does not expose a DELETE endpoint**. Admins who need to remove
a file can do so via the Supabase Dashboard → Storage → select file →
Delete.

A future `DELETE /api/admin/upload/{bucket}/{path}` endpoint may be
added when orphan cleanup or admin file management becomes a priority.

### 5.3 Batch upload (future — not v1)

For bulk exam import (Excel + zip of audio/images), a batch variant may
be added later:

```
POST /api/admin/upload/batch
Body: [{bucket, filename, contentType, fileSizeBytes}, ...]
Response: [{uploadUrl, publicUrl, ...}, ...]
```

Not needed for v1 — admin uploads one file at a time via the UI.

---

## 6. Backend architecture (adapter pattern)

The storage interaction is abstracted behind a `StorageService` interface
so swapping providers (Supabase → S3 → GCS) requires changing **one
adapter file**, not routes or business logic.

### 6.1 File structure

```
services/
  storage_service.py          # Abstract interface + factory + UploadResult dataclass
adapters/
  __init__.py
  supabase_storage.py         # Current provider implementation
  s3_storage.py               # Future (stub until needed)
```

### 6.2 Interface

```python
# services/storage_service.py

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class UploadResult:
    """Returned by create_signed_upload — everything FE needs."""
    upload_url: str       # signed PUT URL (time-limited)
    public_url: str       # permanent URL to store in DB
    path: str             # storage path within bucket
    bucket: str           # bucket name
    expires_in: int       # seconds until upload_url expires

class StorageService(ABC):
    """Provider-agnostic file storage interface."""

    @abstractmethod
    async def create_signed_upload(
        self,
        bucket: str,
        filename: str,
        content_type: str,
    ) -> UploadResult:
        """Generate a signed URL for direct upload from the browser.

        Args:
            bucket: "audio" or "images"
            filename: original filename (used to extract extension only)
            content_type: MIME type

        Returns:
            UploadResult with upload URL + public URL
        """
        ...

    @abstractmethod
    async def delete_file(self, bucket: str, path: str) -> None:
        """Delete a file from storage. Used for cleanup."""
        ...


def get_storage_service() -> StorageService:
    """Factory — returns the configured provider adapter.

    Controlled by STORAGE_PROVIDER env var (default "supabase").
    Add to config/settings.py:
        storage_provider: str = Field(default="supabase", alias="STORAGE_PROVIDER")
    """
    from config.settings import get_settings
    settings = get_settings()
    provider = getattr(settings, 'storage_provider', 'supabase')
    if provider == 's3':
        from adapters.s3_storage import S3StorageAdapter
        return S3StorageAdapter(settings)
    else:
        from adapters.supabase_storage import SupabaseStorageAdapter
        return SupabaseStorageAdapter(settings)
```

### 6.3 Supabase adapter (current)

```python
# adapters/supabase_storage.py

import uuid
from pathlib import PurePosixPath

import httpx

from services.storage_service import StorageService, UploadResult

class SupabaseStorageAdapter(StorageService):
    """Supabase Storage implementation using the REST API."""

    def __init__(self, settings):
        self.base_url = settings.supabase_url
        self.service_key = settings.supabase_service_role_key
        self.upload_ttl = 300  # 5 minutes

    async def create_signed_upload(self, bucket, filename, content_type):
        ext = PurePosixPath(filename).suffix.lower()   # ".mp3"
        file_id = str(uuid.uuid4())
        path = f"{file_id}{ext}"                        # "a1b2c3d4.mp3"

        # Supabase Storage REST API: create signed upload URL.
        # Docs: https://supabase.com/docs/reference/api (Storage section).
        # SDK equivalent: storage.from_(bucket).createSignedUploadUrl(path).
        # NOTE: verify exact endpoint + expiresIn field name against current
        # Supabase API version during implementation — minor differences
        # between versions have been observed.
        api_url = f"{self.base_url}/storage/v1/object/upload/sign/{bucket}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                },
                json={"expiresIn": self.upload_ttl},
            )
            resp.raise_for_status()
            signed_data = resp.json()

        upload_url = f"{self.base_url}/storage/v1{signed_data['url']}"
        public_url = f"{self.base_url}/storage/v1/object/public/{bucket}/{path}"

        return UploadResult(
            upload_url=upload_url,
            public_url=public_url,
            path=path,
            bucket=bucket,
            expires_in=self.upload_ttl,
        )

    async def delete_file(self, bucket, path):
        api_url = f"{self.base_url}/storage/v1/object/{bucket}/{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                api_url,
                headers={"Authorization": f"Bearer {self.service_key}"},
            )
            resp.raise_for_status()
```

### 6.4 S3 adapter (future — stub)

```python
# adapters/s3_storage.py

class S3StorageAdapter(StorageService):
    """AWS S3 implementation. Not needed yet — placeholder."""

    def __init__(self, settings):
        # Would use boto3 + settings.aws_access_key_id, etc.
        raise NotImplementedError("S3 adapter not implemented yet")
```

### 6.5 Route (provider-agnostic)

```python
# api/admin/routes.py (addition)

from services.storage_service import get_storage_service

@router.post("/upload", response_model=UploadResponse)
async def request_upload(
    request: UploadRequest,
    admin: dict = Depends(require_admin),
):
    """Generate a signed upload URL for direct browser-to-storage upload."""
    validate_upload_request(request)  # bucket, content_type, size checks
    storage = get_storage_service()
    result = await storage.create_signed_upload(
        bucket=request.bucket,
        filename=request.filename,
        content_type=request.contentType,
    )
    return UploadResponse(data=UploadResponseData(**vars(result)))
```

→ Route does not import Supabase SDK, httpx, or any provider-specific code.

---

## 7. Frontend integration guide

### 7.1 Upload a file

> **Prerequisite**: [§9.2 setup](#92-setup-one-time-supabase-dashboard)
> must be completed first (buckets toggled to public). Without it, the
> HEAD verify in step 3 will fail with 400/404.

```ts
interface UploadResult {
  uploadUrl: string;
  publicUrl: string;
  path: string;
  bucket: string;
  expiresIn: number;
}

async function uploadFile(
  file: File,
  bucket: 'audio' | 'images',
): Promise<string> {
  // 1. Request signed URL from BE
  const result = await api<UploadResult>('/api/admin/upload', {
    method: 'POST',
    body: JSON.stringify({
      bucket,
      filename: file.name,
      contentType: file.type,
      fileSizeBytes: file.size,
    }),
  });

  // 2. Upload file directly to storage
  const uploadResp = await fetch(result.uploadUrl, {
    method: 'PUT',
    headers: {'Content-Type': file.type},
    body: file,
  });
  if (!uploadResp.ok) {
    throw new Error(`Upload failed: ${uploadResp.status}`);
  }

  // 3. Verify file exists
  const verifyResp = await fetch(result.publicUrl, {method: 'HEAD'});
  if (!verifyResp.ok) {
    throw new Error('File verification failed — upload may not have completed');
  }

  // 4. Return the permanent URL for use in materials/questions
  return result.publicUrl;
}
```

### 7.2 Use in section materials

```ts
// After upload completes:
const audioUrl = await uploadFile(audioFile, 'audio');
const imageUrl = await uploadFile(imageFile, 'images');

// Include in section create/update:
await api(`/api/exams/${examId}/sections`, {
  method: 'POST',
  body: JSON.stringify({
    partLabel: 'Part 2',
    type: 'fill_blank',
    maxAudioPlays: 3,
    materials: [
      {type: 'audio', label: 'Track 1', url: audioUrl},
      {type: 'image', label: 'Form', url: imageUrl, alt: 'Bus tour form'},
      {type: 'text', content: 'Name of guide: {{gap:1}}'},
    ],
    // ...
  }),
});
```

### 7.3 Use in question options (image MC)

```ts
const carUrl = await uploadFile(carFile, 'images');
const trainUrl = await uploadFile(trainFile, 'images');
const busUrl = await uploadFile(busFile, 'images');

await api(`/api/sections/${sectionId}/questions`, {
  method: 'POST',
  body: JSON.stringify({
    question_type: 'multiple_choice',
    question_data: {
      stem: 'How did the woman travel to work?',
      options: [
        {image_url: carUrl},
        {image_url: trainUrl},
        {image_url: busUrl},
      ],
      correct_index: 2,
    },
  }),
});
```

### 7.4 Upload progress UI

```tsx
function FileUploader({bucket, onUploaded}: {
  bucket: 'audio' | 'images';
  onUploaded: (url: string) => void;
}) {
  const [status, setStatus] = useState<'idle' | 'uploading' | 'verifying' | 'done' | 'error'>('idle');
  const [progress, setProgress] = useState(0);

  async function handleFile(file: File) {
    try {
      setStatus('uploading');

      // 1. Get signed URL
      const result = await api<UploadResult>('/api/admin/upload', {
        method: 'POST',
        body: JSON.stringify({
          bucket,
          filename: file.name,
          contentType: file.type,
          fileSizeBytes: file.size,
        }),
      });

      // 2. Upload with progress tracking via XMLHttpRequest
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () => xhr.status === 200 ? resolve() : reject(new Error(`${xhr.status}`));
        xhr.onerror = () => reject(new Error('Network error'));
        xhr.open('PUT', result.uploadUrl);
        xhr.setRequestHeader('Content-Type', file.type);
        xhr.send(file);
      });

      // 3. Verify
      setStatus('verifying');
      const verify = await fetch(result.publicUrl, {method: 'HEAD'});
      if (!verify.ok) throw new Error('Verification failed');

      setStatus('done');
      onUploaded(result.publicUrl);
    } catch (e) {
      setStatus('error');
    }
  }

  return (
    <div>
      <input type="file"
        accept={bucket === 'audio' ? 'audio/*' : 'image/*'}
        onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])} />
      {status === 'uploading' && <progress value={progress} max={100} />}
      {status === 'verifying' && <span>Verifying...</span>}
      {status === 'done' && <span>Upload complete</span>}
      {status === 'error' && <span>Upload failed — try again</span>}
    </div>
  );
}
```

---

## 8. Validation rules

Server-side validation happens **before** issuing the signed URL (step 2).
The file itself is never seen by the server — only metadata.

### 8.1 Allowed content types

| Bucket | Allowed MIME types | File extensions |
|---|---|---|
| `audio` | `audio/mpeg`, `audio/mp4`, `audio/x-m4a`, `audio/m4a`, `audio/wav`, `audio/webm` | `.mp3`, `.m4a`, `.wav`, `.webm` |
| `images` | `image/png`, `image/jpeg`, `image/webp` | `.png`, `.jpg`, `.jpeg`, `.webp` |

Reject anything not in the whitelist → `400` with diagnostic.

### 8.2 File size limits

| Bucket | Max size | Rationale |
|---|---|---|
| `audio` | **50 MB** | KET listening audio ~3-5 min = ~5-10 MB at 128kbps. 50 MB gives ample headroom. |
| `images` | **10 MB** | KET images are diagrams/forms, typically < 1 MB. 10 MB covers high-res scans. |

Reject oversized → `400` with size info.

### 8.3 Filename sanitization

- Extract extension from original filename (`PurePosixPath(filename).suffix`)
- Reject if no extension or unrecognized extension
- Replace the entire filename with a UUID: `{uuid4()}.{ext}`
- Original filename is discarded server-side (stored nowhere unless
  admin puts it in `materials[i].label`)

### 8.4 Client-side pre-validation (recommended)

FE should validate **before** calling the API to avoid unnecessary
round-trips:

```ts
const LIMITS = {
  audio: {maxBytes: 50 * 1024 * 1024, accept: ['audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/m4a', 'audio/wav', 'audio/webm']},
  images: {maxBytes: 10 * 1024 * 1024, accept: ['image/png', 'image/jpeg', 'image/webp']},
};

function validateFile(file: File, bucket: 'audio' | 'images'): string | null {
  const limit = LIMITS[bucket];
  if (!limit.accept.includes(file.type)) return `File type ${file.type} not allowed`;
  if (file.size > limit.maxBytes) return `File too large (max ${limit.maxBytes / 1024 / 1024}MB)`;
  return null;  // valid
}
```

---

## 9. Bucket access policy

### 9.1 Decision: public-read

| Policy | Chosen | Reason |
|--------|--------|--------|
| **Public-read** | ✅ | Content is educational (exam audio/images), not sensitive. URLs are stable, cacheable, no expiry. Anyone who has the URL can access — but URLs are only exposed to authenticated users via the API. |
| Private + signed download | ❌ | URLs expire → cached content breaks, FE must refresh URLs periodically, more API calls. Over-engineered for educational content. |

### 9.2 Setup (one-time, Supabase Dashboard)

Two steps, both required. Without step A the `publicUrl` path returns
400/404; without step B the RLS layer blocks anonymous reads.

**Step A — toggle bucket to public** (changes URL routing):

For each bucket (`audio`, `images`):
1. Supabase Dashboard → **Storage** → click the bucket name
2. Click the **⚙️ settings** icon (or three-dot menu) → **Make public**
3. Confirm. The `/object/public/{bucket}/...` URL prefix now serves
   files without auth.

> This is a **change from initial setup** — [DEPLOYMENT.md §3.1](DEPLOYMENT.md)
> created both buckets as private. Toggle them now.

**Step B — add RLS read policy** (allows `anon`/`authenticated` SELECT):

```sql
-- Run in Supabase SQL Editor (once per project, covers both buckets):
CREATE POLICY "Public read access"
  ON storage.objects FOR SELECT
  USING (bucket_id IN ('audio', 'images'));
```

Upload remains restricted — only `service_role` (used by BE's signed URL
flow) can write. No RLS policy for INSERT is needed for public users.

### 9.3 CORS on storage (for direct PUT from browser)

Supabase Storage must allow PUT from the FE origin. Check Supabase
Dashboard → **Settings** → **API** → **CORS**. Ensure the FE origin
(`http://localhost:3000`, `https://maichienglish.vercel.app`) is listed,
or use `*` for dev.

---

## 10. Orphan file cleanup

An orphan is a file in storage whose URL is not referenced by any
`sections.materials` or `questions.question_data` row.

**Causes:**
- Admin uploads a file but never uses it in any section/question
- Admin replaces a file URL in materials but doesn't delete the old file
- Upload completes but FE crashes before saving the URL to a section

### 10.1 v1 — no cleanup (acceptable)

Storage is cheap (~$0.02/GB/month on Supabase Free tier). Orphans
accumulate slowly (admin uploads only). Monitor total bucket size
monthly; manual cleanup via Supabase Dashboard if needed.

### 10.2 v2 — background sweep (future)

Add a script or scheduled job:

```python
# scripts/cleanup_orphans.py (future)
# 1. List all files in audio/ and images/ buckets
# 2. Query all URLs referenced in sections.materials and questions.question_data
# 3. Diff → files not referenced = orphans
# 4. Delete orphans older than 7 days (grace period)
```

Run weekly via cron or Render cron job. Not needed for v1.

---

## 11. Provider migration

When migrating from Supabase to AWS S3 (or other provider):

### 11.1 What changes

| Component | Change needed |
|-----------|---------------|
| `adapters/supabase_storage.py` | Replace with `adapters/s3_storage.py` |
| `config/settings.py` | Add `AWS_*` env vars; set `STORAGE_PROVIDER=s3` |
| Bucket CORS | Configure on S3 / CloudFront |
| Public URL format | `https://bucket.s3.region.amazonaws.com/path` |

### 11.2 What does NOT change

| Component | Reason |
|-----------|--------|
| `POST /api/admin/upload` | Same request/response contract |
| FE upload flow | Same signed URL pattern |
| DB data (JSONB URLs) | Just strings — portable |
| Routes, schemas, other services | Don't import storage SDK |

### 11.3 Data migration

Per project policy: **start fresh** when changing providers. No file
migration needed. Old Supabase project can be shut down; new S3 bucket
starts empty.

---

## 12. Edge cases & decisions

| # | Scenario | Decision |
|---|----------|----------|
| 1 | FE gets signed URL but upload fails (network drop) | `publicUrl` is a dead link. FE detects via HEAD → retry or discard. No server-side state to clean up (no DB row was created). |
| 2 | FE uploads successfully but crashes before HEAD verify | File exists in storage as orphan. No harm — orphan cleanup (§10) handles it eventually. Admin re-uploads. |
| 3 | FE uploads then saves URL in materials, but later admin removes the material | File becomes orphan in storage. Same as #2 — cleanup sweep handles it. |
| 4 | Signed URL expires before FE uploads (>5 min) | FE gets 403 from storage PUT. Should re-request via `POST /api/admin/upload` and retry. |
| 5 | Two admins upload files with the same original filename | No conflict — server generates unique UUID paths. Both uploads succeed independently. |
| 6 | Admin uploads a 50MB audio file | Accepted by validation. FE shows progress bar. Direct-to-storage avoids Render memory pressure. |
| 7 | Non-admin tries `POST /api/admin/upload` | 403 `"Admin access required"` — same as all admin endpoints. |
| 8 | Admin tries to upload a `.exe` file as "audio" | `contentType` validation rejects: `400 "Content type application/x-msdownload not allowed for bucket audio"`. |
| 9 | Admin uploads then wants to replace file at same URL | Not possible — UUID paths are unique. Upload new file → get new URL → update materials/question data → old file becomes orphan. |
| 10 | Storage bucket is full (Supabase free tier: 1GB) | Supabase returns error on upload → FE gets non-200 from PUT. Show "Storage full — contact admin". Upgrade Supabase plan or clean orphans. |

---

## Appendix: New files to create

| File | Purpose |
|------|---------|
| `services/storage_service.py` | Abstract `StorageService` interface + `UploadResult` dataclass + `get_storage_service()` factory |
| `adapters/__init__.py` | Package marker |
| `adapters/supabase_storage.py` | Supabase Storage adapter (signed URL via REST API) |
| `adapters/s3_storage.py` | Stub — `raise NotImplementedError` |
| `api/admin/schemas.py` | Add `UploadRequest` + `UploadResponse` schemas |
| `api/admin/routes.py` | Add `POST /upload` endpoint |
| `config/settings.py` | Add `STORAGE_PROVIDER` env var (default `"supabase"`) |
| `requirements.txt` | Add `httpx>=0.28,<1` (used by Supabase adapter for REST calls) |

**No database migration needed** — upload flow uses existing JSONB fields.
No new tables.

---

## Appendix: Suggested TypeScript types

```ts
// Request
interface UploadRequest {
  bucket: 'audio' | 'images';
  filename: string;          // original name, e.g. "ket-p5.mp3"
  contentType: string;       // MIME, e.g. "audio/mpeg"
  fileSizeBytes: number;
}

// Response from POST /api/admin/upload
interface UploadResult {
  uploadUrl: string;         // signed PUT URL (5 min TTL)
  publicUrl: string;         // permanent URL to store in DB
  path: string;              // storage path, e.g. "a1b2c3d4.mp3"
  bucket: 'audio' | 'images';
  expiresIn: number;         // seconds until uploadUrl expires
}

// Validation constants (mirror server-side)
const UPLOAD_LIMITS = {
  audio: {
    maxBytes: 50 * 1024 * 1024,
    allowedTypes: ['audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/m4a', 'audio/wav', 'audio/webm'],
  },
  images: {
    maxBytes: 10 * 1024 * 1024,
    allowedTypes: ['image/png', 'image/jpeg', 'image/webp'],
  },
} as const;
```
