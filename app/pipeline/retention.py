"""YouTube Analytics 잔존율 fetch (Day 7+ 영상만, cold start fallback)."""
from datetime import UTC, datetime
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.storage.db import get_connection
from app.storage.models import RetentionCurve, Video
from app.utils.logger import get_logger

log = get_logger(__name__)


def _days_since(published_at: str | None) -> int | None:
    """ISO 날짜 → 오늘까지 일 수. 파싱 실패 시 None."""
    if not published_at:
        return None
    try:
        pub = datetime.fromisoformat(published_at)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=UTC)
        return (datetime.now(UTC) - pub).days
    except ValueError:
        return None


def _channel_id_for(channel: str) -> str:
    """ko/en → settings의 채널 ID."""
    if channel == "en":
        return settings.youtube_channel_id_en
    return settings.youtube_channel_id


def _is_channel_match(video: Video) -> bool:
    """video가 등록된 채널(ko 또는 en)에 속하는지."""
    return bool(_channel_id_for(video.channel))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=False,
)
def _query_analytics(
    youtube_id: str, published_at: str, channel: str = "ko",
) -> dict[str, Any]:
    """YouTube Analytics API 호출 (channel scope)."""
    from app.integrations.youtube import build_analytics_client

    analytics = build_analytics_client(channel)
    end_date = datetime.now(UTC).strftime("%Y-%m-%d")
    return analytics.reports().query(  # type: ignore[no-any-return]
        ids=f"channel=={_channel_id_for(channel)}",
        startDate=published_at[:10],
        endDate=end_date,
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={youtube_id}",
        sort="elapsedVideoTimeRatio",
    ).execute()


def _parse_response(youtube_id: str, resp: dict[str, Any]) -> RetentionCurve:
    """Analytics 응답 → RetentionCurve."""
    rows = resp.get("rows") or []
    elapsed: list[float] = []
    awr: list[float] = []
    rrp: list[float] = []
    for row in rows:
        if len(row) < 3:
            continue
        elapsed.append(float(row[0]))
        awr.append(float(row[1]))
        rrp.append(float(row[2]))
    return RetentionCurve(
        youtube_id=youtube_id,
        elapsed_ratios=elapsed,
        audience_watch_ratio=awr,
        relative_retention_performance=rrp,
        fetched_at=datetime.now(UTC).isoformat(),
    )


def detect_peak_regions(
    curve: RetentionCurve,
    video_duration: int,
    *,
    spike_threshold: float | None = None,
    cluster_gap_sec: float = 30.0,
    region_pad_left_sec: float = 15.0,
    region_pad_right_sec: float = 30.0,
    min_region_sec: float = 60.0,
    max_region_sec: float = 120.0,
) -> list[tuple[float, float, float]]:
    """audience_watch_ratio 1차 미분 spike 기반 viral 영역 검출.

    알고리즘:
      1. 인접 sample slope 계산 (per second).
      2. slope > 0 spike 위치 찾기 (시청자 재유입 신호).
      3. slope >= spike_threshold만 strong spike로 채택.
         None이면 양수 slope 평균 사용.
      4. 인접 spike (cluster_gap_sec 이내) 클러스터링.
      5. 클러스터 양옆에 pad 추가 → region.
      6. min/max region length로 normalize.

    Phase 8 학습 루프가 spike_threshold/cluster_gap_sec/pad를 calibration
    테이블에서 주입할 수 있도록 모든 parameter는 함수 인자로 외부화.

    반환: [(region_start_sec, region_end_sec, max_spike_strength), ...]
    """
    if not curve.audience_watch_ratio or not curve.elapsed_ratios:
        return []
    if video_duration <= 0:
        return []

    awr = curve.audience_watch_ratio
    times = [r * video_duration for r in curve.elapsed_ratios]

    # 1. slope 계산.
    slopes: list[tuple[float, float]] = []  # (time, slope)
    for i in range(1, len(awr)):
        dt = times[i] - times[i - 1]
        slope = (awr[i] - awr[i - 1]) / dt if dt > 0 else 0.0
        slopes.append((times[i], slope))
    if not slopes:
        return []

    # 2. 양수 slope만.
    positive = [(t, s) for t, s in slopes if s > 0]
    if not positive:
        return []

    # 3. threshold (None이면 양수 평균 — Phase 8에서 학습값 주입).
    if spike_threshold is None:
        spike_threshold = sum(s for _, s in positive) / len(positive)

    strong = [(t, s) for t, s in positive if s >= spike_threshold]
    if not strong:
        return []

    # 4. 클러스터링.
    clusters: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for t, s in strong:
        if not current or t - current[-1][0] <= cluster_gap_sec:
            current.append((t, s))
        else:
            clusters.append(current)
            current = [(t, s)]
    if current:
        clusters.append(current)

    # 5. region 생성.
    regions: list[tuple[float, float, float]] = []
    for c in clusters:
        start = max(0.0, c[0][0] - region_pad_left_sec)
        end = min(float(video_duration), c[-1][0] + region_pad_right_sec)
        peak_strength = max(s for _, s in c)
        # 6. min_region_sec 미달 시 양쪽 확장.
        if end - start < min_region_sec:
            extra = (min_region_sec - (end - start)) / 2
            start = max(0.0, start - extra)
            end = min(float(video_duration), end + extra)
        # max_region_sec 초과 시 절단 (중심 기준).
        if end - start > max_region_sec:
            mid = (start + end) / 2
            start = max(0.0, mid - max_region_sec / 2)
            end = min(float(video_duration), mid + max_region_sec / 2)
        regions.append((start, end, peak_strength))

    log.info(
        "retention.peak_regions_detected",
        youtube_id=curve.youtube_id,
        spike_threshold=round(spike_threshold, 6),
        strong_spikes=len(strong),
        regions=len(regions),
    )
    return regions


def fetch_retention(video: Video) -> RetentionCurve | None:
    """Day 7+ 영상의 잔존율 fetch. 캐시 hit / cold start / 실패 시 None."""
    out_path = settings.retention_dir / f"{video.youtube_id}.json"
    if out_path.exists():
        log.info("retention.cache_hit", youtube_id=video.youtube_id)
        return RetentionCurve.model_validate_json(
            out_path.read_text(encoding="utf-8"),
        )

    if not _is_channel_match(video):
        log.info(
            "retention.skip_no_channel_id",
            youtube_id=video.youtube_id,
        )
        return None

    days = _days_since(video.published_at)
    if days is None:
        log.info(
            "retention.skip_no_published_at",
            youtube_id=video.youtube_id,
        )
        return None
    if days < settings.retention_min_days:
        log.info(
            "retention.cold_start",
            youtube_id=video.youtube_id, days=days,
            min_days=settings.retention_min_days,
        )
        return None

    try:
        resp = _query_analytics(
            video.youtube_id, video.published_at or "", channel=video.channel,
        )
    except Exception as e:
        log.warning(
            "retention.fetch_failed",
            youtube_id=video.youtube_id, error=str(e),
        )
        return None

    curve = _parse_response(video.youtube_id, resp)
    if not curve.elapsed_ratios:
        log.warning(
            "retention.empty_curve", youtube_id=video.youtube_id,
        )
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        curve.model_dump_json(indent=2),
        encoding="utf-8",
    )

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE videos SET retention_fetched_at = ? WHERE youtube_id = ?",
            (curve.fetched_at, video.youtube_id),
        )
        conn.commit()
    finally:
        conn.close()

    log.info(
        "retention.fetched",
        youtube_id=video.youtube_id,
        data_points=len(curve.elapsed_ratios),
    )
    return curve
