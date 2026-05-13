"""P014 Gemini 호출 raw 디버깅 (1회용).

retry 안 쓰고 1번 호출 → finish_reason/safety_ratings/raw text까지 출력.
"""
from __future__ import annotations

import json
import sys

from app.config import settings
from app.pipeline.analyze import (
    SYSTEM_INSTRUCTION,
    _build_user_prompt,
    _build_user_prompt_peak_mode,
)
from app.pipeline.retention import detect_peak_regions, fetch_retention
from app.storage.db import get_connection
from app.storage.models import Transcript

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

YID = "3QYQ81GBFcc"


def main() -> None:
    # 1) Transcript 로드
    transcript_path = settings.transcripts_dir / f"{YID}.json"
    transcript = Transcript.model_validate_json(
        transcript_path.read_text(encoding="utf-8"),
    )
    print(f"transcript: {len(transcript.segments)} segments")

    # 2) Retention fetch + peak_regions
    conn = get_connection()
    row = conn.execute(
        "SELECT id, youtube_id, title, duration, internal_id FROM videos "
        "WHERE youtube_id = ?", (YID,),
    ).fetchone()
    conn.close()
    print(f"video: {dict(row)}")

    from app.storage.models import Video
    video = Video(
        id=row["id"], youtube_id=row["youtube_id"], title=row["title"] or "",
        duration=row["duration"] or 0,
        local_path=settings.samples_dir / f"{YID}.mp4",
        internal_id=row["internal_id"],
    )

    try:
        curve = fetch_retention(video)
    except Exception as e:
        print(f"retention failed: {e}")
        curve = None

    peak_regions = []
    if curve is not None and curve.audience_watch_ratio:
        peak_regions = detect_peak_regions(
            curve, video.duration,
            cluster_gap_sec=settings.retention_peak_cluster_gap_sec,
            region_pad_left_sec=settings.retention_peak_pad_left_sec,
            region_pad_right_sec=settings.retention_peak_pad_right_sec,
            min_region_sec=settings.retention_peak_min_region_sec,
            max_region_sec=settings.retention_peak_max_region_sec,
        )
    print(f"peak_regions: {len(peak_regions)}")

    # 3) Prompt 빌드
    if peak_regions:
        prompt = _build_user_prompt_peak_mode(
            transcript, peak_regions, settings.gemini_max_moments,
        )
        mode = "peak"
    else:
        prompt = _build_user_prompt(transcript, settings.gemini_max_moments)
        mode = "free"
    print(f"mode: {mode}, prompt_chars: {len(prompt)}")

    # 4) Gemini 직접 호출 — retry 없음
    import google.generativeai as genai

    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={
            "temperature": settings.gemini_temperature,
            "response_mime_type": "application/json",
        },
    )

    print("\n=== calling generate_content (no retry) ===\n")
    try:
        resp = model.generate_content(
            prompt,
            request_options={"timeout": settings.gemini_request_timeout_sec},
        )
    except Exception as e:
        print(f"!!! generate_content RAISED: {type(e).__name__}: {e}")
        return

    # 5) 응답 상세 출력
    print(f"prompt_feedback: {getattr(resp, 'prompt_feedback', None)}")
    cands = getattr(resp, "candidates", None) or []
    print(f"candidates: {len(cands)}")
    for i, c in enumerate(cands):
        fr = getattr(c, "finish_reason", None)
        sr = getattr(c, "safety_ratings", None)
        print(f"  cand[{i}] finish_reason: {fr}")
        print(f"  cand[{i}] safety_ratings: {sr}")
        content = getattr(c, "content", None)
        parts = getattr(content, "parts", []) if content else []
        for j, p in enumerate(parts):
            txt = getattr(p, "text", None)
            print(f"    part[{j}] text ({len(txt or '')} chars): "
                  f"{(txt or '')[:1000]}")

    # 6) text 시도
    try:
        text = (resp.text or "").strip()
        print(f"\n=== resp.text ({len(text)} chars) ===")
        print(text[:2000])
        try:
            data = json.loads(text)
            print(f"\nJSON parsed: moments={len(data.get('moments', []))}")
        except json.JSONDecodeError as e:
            print(f"\nJSON parse FAILED: {e}")
    except Exception as e:
        print(f"\nresp.text RAISED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
