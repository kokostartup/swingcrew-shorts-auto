"""백로그 22개 미드폼 일괄 처리 (영빈 1회용).

노션 created desc 디폴트 정렬에서 큰 번호가 위·작은 번호가 아래로 가도록
**낮은 번호부터** 순차 push. P/B 인터리브 (같은 숫자면 P 먼저).
"""
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

BACKLOG: list[tuple[str, str]] = [
    ("26-P002", "88qzNpPkZYU"),
    ("26-B002", "nM_gTaVktwM"),
    ("26-P003", "BDH6x6sDX5Y"),
    ("26-B003", "-4lJlqkRZ7Q"),
    ("26-P004", "JoKK-yA195I"),
    ("26-B004", "Vhqs01UbAvM"),
    ("26-P005", "bI1X-3CzqrU"),
    ("26-B005", "wyoodSh7t-8"),
    ("26-P006", "Lv-dE1jPVHQ"),
    ("26-B006", "6dl3oe124Nk"),
    ("26-P007", "q7mhUH4OunI"),
    ("26-B007", "7SGxpZoRRbQ"),
    ("26-P008", "j2WG13jhmCE"),
    ("26-B008", "xqsL1gmJ1a0"),
    ("26-P009", "NTTUDRcNzSA"),
    ("26-P010", "O75_HX8CYlc"),
    ("26-P011", "T2b6QABAEDI"),
    ("26-P012", "BprLGji8cL4"),
    ("26-P013", "tM4KJj2wlw8"),
    ("26-P014", "3QYQ81GBFcc"),
    ("26-P015", "9yoZayGczOA"),
    ("26-P016", "elVfUEZoldw"),
]


def main() -> None:
    total = len(BACKLOG)
    start = time.time()
    print(f"=== run_backlog start: {total} videos ===", flush=True)
    ok = 0
    failed: list[str] = []
    for idx, (internal_id, yid) in enumerate(BACKLOG, 1):
        elapsed = int(time.time() - start)
        print(f"\n[{idx}/{total}] {internal_id} ({yid}) — elapsed {elapsed}s", flush=True)
        try:
            video = ingest(yid)
            transcript = transcribe(video)
            result = analyze(video, transcript)
            sync_to_notion(video, result)
            print(
                f"  done: {video.internal_id} ({len(result.moments)} moments)",
                flush=True,
            )
            ok += 1
        except Exception as e:
            log.warning("run_backlog.failed", youtube_id=yid, error=str(e))
            print(f"  failed: {e}", flush=True)
            failed.append(internal_id)
    elapsed = int(time.time() - start)
    print(
        f"\n=== run_backlog done in {elapsed}s — "
        f"ok={ok}/{total} failed={failed} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
