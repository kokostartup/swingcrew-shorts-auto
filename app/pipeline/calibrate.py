"""Phase 8 학습 루프 — 채널 통계 → calibration 테이블 → analyze/retention 자동 주입.

두 시그널:
- **C (미드폼 retention)**: 채널 retention curve의 양수 slope 분포 학습 →
  `spike_threshold` percentile 80 추출. detect_peak_regions에 주입.
- **B (게시 7일+ 숏츠)**: 게시 숏츠의 YouTube views 학습 → top 25% scene_type
  분포 + hook 단어 빈도. analyze.py prompt few-shot 동적 갱신용.

영빈 ✅/❌ 통계(A)는 의도적으로 skip — 영빈 본인이 "거의 다 ✅한다"고 말함, 신호 약함.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import settings
from app.storage.db import get_connection
from app.storage.models import RetentionCurve
from app.utils.logger import get_logger

log = get_logger(__name__)


def _percentile(values: list[float], p: float) -> float:
    """단순 percentile (linear interp 없이 sorted index)."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p / 100)
    idx = max(0, min(len(sorted_v) - 1, idx))
    return sorted_v[idx]


def calibrate_midform_retention() -> dict[str, Any]:
    """C: 미드폼 retention curve의 양수 slope 분포 학습.

    detect_peak_regions의 spike_threshold 기본값(양수 평균)은 너무 관대해서
    region이 과하게 검출될 수 있음. 채널 데이터 기반 percentile 80을 학습값으로 사용.
    """
    conn = get_connection()
    try:
        # video duration이 필요 (elapsed_ratios * duration = seconds 단위)
        video_durations: dict[str, int] = {}
        for r in conn.execute("SELECT youtube_id, duration FROM videos"):
            video_durations[r["youtube_id"]] = r["duration"]
    finally:
        conn.close()

    all_slopes: list[float] = []
    video_count = 0
    for fp in settings.retention_dir.glob("*.json"):
        try:
            curve = RetentionCurve.model_validate_json(
                fp.read_text(encoding="utf-8"),
            )
        except Exception as e:
            log.warning("calibrate.retention_load_failed", path=str(fp), error=str(e))
            continue
        duration = video_durations.get(curve.youtube_id, 0)
        if duration <= 0 or not curve.audience_watch_ratio:
            continue
        times = [r * duration for r in curve.elapsed_ratios]
        awr = curve.audience_watch_ratio
        video_slopes_added = 0
        for i in range(1, len(awr)):
            dt = times[i] - times[i - 1]
            if dt <= 0:
                continue
            slope = (awr[i] - awr[i - 1]) / dt
            if slope > 0:
                all_slopes.append(slope)
                video_slopes_added += 1
        if video_slopes_added:
            video_count += 1

    if not all_slopes:
        return {"sample_videos": 0, "sample_slopes": 0}

    return {
        "sample_videos": video_count,
        "sample_slopes": len(all_slopes),
        "spike_threshold_p70": _percentile(all_slopes, 70),
        "spike_threshold_p80": _percentile(all_slopes, 80),
        "spike_threshold_p90": _percentile(all_slopes, 90),
        "spike_threshold_mean": sum(all_slopes) / len(all_slopes),
        # detect_peak_regions에 주입할 추천값 — top 20% spike만 region으로 채택.
        "recommended_spike_threshold": _percentile(all_slopes, 80),
    }


_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
# 조사/지시어/일반 stop words (영빈 채널 한글 콘텐츠)
_STOP_WORDS = {
    "이", "그", "저", "것", "수", "에", "은", "는", "이걸", "그걸", "이거",
    "이게", "그게", "이런", "이렇게", "그래서", "여기", "거기", "있는", "없는",
    "하는", "하면", "하지", "있어", "없어", "있죠", "있다",
}


def _extract_words(text: str | None) -> list[str]:
    if not text:
        return []
    return [
        t for t in _TOKEN_RE.findall(text)
        if len(t) >= 2 and t not in _STOP_WORDS
    ]


def calibrate_published_shorts(min_age_days: int = 7) -> dict[str, Any]:
    """B: 게시 N일+ 숏츠의 YouTube views/watch_time 학습.

    1. shorts WHERE scheduled_at < now - N days AND published_urls IS NOT NULL
    2. published_urls JSON에서 YouTube video_id 추출
    3. YouTube Analytics → views, estimatedMinutesWatched
    4. top 25% (views 기준) 의 scene_type/copy 단어 빈도
    """
    cutoff = (datetime.now(UTC) - timedelta(days=min_age_days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.id, s.internal_id, s.scheduled_at, s.scene_type, "
            "       s.published_urls, s.opening_line "
            "FROM shorts s "
            "WHERE s.scheduled_at IS NOT NULL "
            "  AND s.scheduled_at < ? "
            "  AND s.published_urls IS NOT NULL "
            "  AND s.status IN ('scheduled','published')",
            (cutoff,),
        ).fetchall()
        # copy1/copy2/hook_text는 shorts 테이블에 없음 — analyses cache에서 fetch
        # 단순화: scene_type만 사용. 단어 빈도는 opening_line으로 대용.
    finally:
        conn.close()

    if not rows:
        return {
            "sample_size": 0,
            "min_age_days": min_age_days,
            "note": "게시 7일+ 숏츠 없음. 5/19+ 데이터 쌓이면 재시도.",
        }

    # YouTube Analytics per video
    try:
        from app.integrations.youtube import build_analytics_client
        analytics = build_analytics_client()
    except Exception as e:
        log.warning("calibrate.analytics_client_failed", error=str(e))
        return {"sample_size": len(rows), "fetch_failed": True, "error": str(e)}

    end_date = datetime.now(UTC).strftime("%Y-%m-%d")
    metrics: list[dict[str, Any]] = []
    for r in rows:
        try:
            pu = json.loads(r["published_urls"] or "{}")
        except json.JSONDecodeError:
            continue
        yt_url = pu.get("youtube") or ""
        m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", yt_url)
        if not m:
            continue
        yt_id = m.group(1)
        try:
            resp = analytics.reports().query(
                ids=f"channel=={settings.youtube_channel_id}",
                startDate=r["scheduled_at"][:10],
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,averageViewDuration",
                filters=f"video=={yt_id}",
            ).execute()
            data = (resp.get("rows") or [None])[0]
            if not data:
                continue
            metrics.append({
                "short_id": r["id"],
                "internal_id": r["internal_id"],
                "youtube_id": yt_id,
                "views": int(data[0] or 0),
                "minutes_watched": float(data[1] or 0),
                "avg_view_duration_sec": float(data[2] or 0),
                "scene_type": r["scene_type"],
                "opening_line": r["opening_line"],
            })
        except Exception as e:
            log.warning(
                "calibrate.short_analytics_failed",
                short_id=r["id"], error=str(e),
            )

    if not metrics:
        return {
            "sample_size": len(rows),
            "fetched": 0,
            "note": "Analytics 호출 실패 또는 데이터 없음.",
        }

    # top 25% by views
    sorted_by_views = sorted(metrics, key=lambda m: -m["views"])
    top_n = max(1, len(sorted_by_views) // 4)
    top = sorted_by_views[:top_n]

    all_scene = Counter(m["scene_type"] for m in metrics if m["scene_type"])
    top_scene = Counter(m["scene_type"] for m in top if m["scene_type"])

    top_words: Counter[str] = Counter()
    for m in top:
        for w in _extract_words(m["opening_line"]):
            top_words[w] += 1

    return {
        "sample_size": len(metrics),
        "top_n": top_n,
        "min_age_days": min_age_days,
        "all_scene_distribution": dict(all_scene),
        "top_scene_distribution": dict(top_scene),
        "top_hook_words": top_words.most_common(15),
        "avg_views": sum(m["views"] for m in metrics) / len(metrics),
        "top_avg_views": sum(m["views"] for m in top) / top_n,
        "avg_view_duration_sec": (
            sum(m["avg_view_duration_sec"] for m in metrics) / len(metrics)
        ),
    }


def calibrate() -> int:
    """C + B 통합 → calibration 테이블 새 row 저장.

    Returns: calibration row id.
    """
    log.info("calibrate.start")
    c = calibrate_midform_retention()
    b = calibrate_published_shorts(min_age_days=7)

    patterns = {"midform_retention": c, "published_shorts": b}
    now = datetime.now(UTC).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO calibration (computed_at, percentile_70_score, top_short_patterns) "
            "VALUES (?, NULL, ?) RETURNING id",
            (now, json.dumps(patterns, ensure_ascii=False)),
        )
        row_id = int(cur.fetchone()["id"])
        conn.commit()
    finally:
        conn.close()

    log.info(
        "calibrate.done",
        row_id=row_id,
        midform_videos=c.get("sample_videos", 0),
        spike_threshold=c.get("recommended_spike_threshold"),
        shorts_sample=b.get("sample_size", 0),
    )
    return row_id


def latest_calibration() -> dict[str, Any] | None:
    """최신 calibration row 반환 (analyze.py/retention.py에서 fetch)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT top_short_patterns FROM calibration "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["top_short_patterns"]:
        return None
    try:
        return json.loads(row["top_short_patterns"])
    except json.JSONDecodeError:
        return None


__all__ = [
    "calibrate",
    "calibrate_midform_retention",
    "calibrate_published_shorts",
    "latest_calibration",
]
