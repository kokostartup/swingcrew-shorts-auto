"""Phase 6 publish 파이프라인 유닛 테스트 (mock 기반)."""
from app.pipeline.publish import _build_buffer_text, _scheduled_at_to_utc_iso
from app.pipeline.publish_meta import PublishMeta


def test_scheduled_at_kst_to_utc() -> None:
    # KST 09:00 → UTC 00:00
    out = _scheduled_at_to_utc_iso("2026-05-20T09:00:00.000+09:00")
    assert out == "2026-05-20T00:00:00Z"


def test_scheduled_at_naive_treated_as_kst() -> None:
    # tzinfo 없으면 KST 가정
    out = _scheduled_at_to_utc_iso("2026-05-20T15:00:00")
    assert out == "2026-05-20T06:00:00Z"


def test_scheduled_at_utc_input() -> None:
    # 이미 UTC면 그대로
    out = _scheduled_at_to_utc_iso("2026-05-20T00:00:00+00:00")
    assert out == "2026-05-20T00:00:00Z"


def test_build_buffer_text_appends_hashtags() -> None:
    meta = PublishMeta(
        title="t", description="설명입니다.",
        hashtags=["#골프", "#shorts"],
    )
    out = _build_buffer_text(meta)
    assert "설명입니다." in out
    assert "#골프 #shorts" in out


def test_build_buffer_text_skips_duplicate_hashtags() -> None:
    """description에 이미 해시태그 포함됐으면 다시 안 붙임."""
    meta = PublishMeta(
        title="t",
        description="설명입니다.\n\n#골프 #shorts",
        hashtags=["#골프", "#shorts"],
    )
    out = _build_buffer_text(meta)
    # 해시태그 한 번만 등장
    assert out.count("#골프") == 1


def test_build_buffer_text_truncates_2200() -> None:
    # description은 PublishMeta 한도 2000자. 거기에 해시태그 추가 후 2200으로 cap.
    meta = PublishMeta(
        title="t",
        description="가" * 2000,
        hashtags=["#a"] * 200,
    )
    out = _build_buffer_text(meta)
    assert len(out) <= 2200
