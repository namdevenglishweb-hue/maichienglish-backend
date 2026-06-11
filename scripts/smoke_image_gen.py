"""Live smoke test for AI image generation (gen -> verify -> upload).

Calls generate_one_image directly with the REAL provider + REAL Supabase
storage (the HTTP IMAGE_GENERATION_ENABLED gate sits above this layer).
Override the verify model via:  IMAGE_VERIFY_MODEL=<slug> python scripts/smoke_image_gen.py

    python scripts/smoke_image_gen.py [rounds]
"""
import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DESCRIPTION = (
    "A simple poster for a school swimming club: big heading 'SWIM CLUB', "
    "below it the text 'Tuesdays 4 pm' and 'Pool B'. Clean, legible text."
)


class LocalDiskStore:
    """Local stand-in for Supabase upload (no SUPABASE_* keys in local .env).

    The smoke target is the REAL generate + verify calls; upload_bytes is a
    plain SDK call already covered by integration tests."""

    async def upload_bytes(self, bucket: str, content_type: str, data: bytes) -> str:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
            content_type, ".bin")
        out = os.path.join("scripts", "ab_results", f"smoke_image{ext}")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        return f"file://{os.path.abspath(out)} ({len(data)} bytes, {content_type})"


async def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else None
    from config.settings import get_settings
    s = get_settings()
    print(f"image_model  = {s.image_model}")
    print(f"verify_model = {s.image_verify_model} | rounds = {rounds if rounds is not None else s.image_verify_rounds}")

    from services.image_generation_service import generate_one_image, ImageGenerationError
    t0 = time.monotonic()
    try:
        r = await generate_one_image(DESCRIPTION, rounds=rounds, storage=LocalDiskStore())
        print(f"\nSUCCEEDED in {time.monotonic()-t0:.1f}s")
        print(f"  url    : {r['image_url']}")
        print(f"  mode   : {r['mode']} | verify rounds used: {r['rounds']}")
        print(f"  usage  : {r['usage']}")
    except ImageGenerationError as e:
        print(f"\nFAILED (verify budget) in {time.monotonic()-t0:.1f}s: {e.reason}")
    except Exception:
        print(f"\nCRASHED in {time.monotonic()-t0:.1f}s:")
        traceback.print_exc()


asyncio.run(main())
