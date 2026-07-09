"""1차 백로그 실행에서 Gemini API retry 끝까지 실패한 3개 재처리 (1회용)."""
from __future__ import annotations

import sys
import time

from app.pipeline.analyze import analyze
from app.pipeline.approve import sync_to_notion
from app.pipeline.ingest import ingest
from app.pipeline.transcribe import transcribe
from app.utils.logger import get_logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger(__name__)

RETRY: list[tuple[str, str]] = [
    ("26-P006", "Lv-dE1jPVHQ"),
]


def main() -> None:
    start = time.time()
    print(f"=== retry_failed start: {len(RETRY)} videos ===", flush=True)
    ok = 0
    failed: list[str] = []
    for idx, (iid, yid) in enumerate(RETRY, 1):
        print(f"\n[{idx}/{len(RETRY)}] {iid} ({yid})", flush=True)
        try:
            video = ingest(yid)
            transcript = transcribe(video)
            result = analyze(video, transcript)
            sync_to_notion(video, result)
            print(f"  done: {len(result.moments)} moments", flush=True)
            ok += 1
        except Exception as e:
            log.warning("retry_failed.failed", youtube_id=yid, error=str(e))
            print(f"  failed: {e}", flush=True)
            failed.append(iid)
    elapsed = int(time.time() - start)
    print(
        f"\n=== retry_failed done in {elapsed}s — ok={ok}/{len(RETRY)} failed={failed} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
