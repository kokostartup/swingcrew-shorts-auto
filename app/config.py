"""환경 변수 단일 진입점 (pydantic-settings)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Phase 1: 시그니처 레이아웃
    font_path: Path = Path("app/utils/fonts/Pretendard-Black.otf")
    output_dir: Path = Path("outputs")
    sqlite_path: Path = Path("data/state.db")
    use_nvenc: bool = False

    # Phase 2: ingest + transcribe
    samples_dir: Path = Path("data/samples")
    transcripts_dir: Path = Path("data/transcripts")
    whisperx_model: str = "large-v3"
    whisperx_device: str = "cuda"
    whisperx_language: str = "ko"
    whisperx_compute_type: str = "float16"
    whisperx_batch_size: int = 16

    # Phase 3: Gemini analyze
    google_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    analyses_dir: Path = Path("data/analyses")
    gemini_max_moments: int = 5
    gemini_min_gap_sec: float = 30.0
    gemini_temperature: float = 0.3
    gemini_max_prompt_chars: int = 80_000
    gemini_request_timeout_sec: int = 60

    # Phase 4: YouTube OAuth + Retention + Scene + Scoring
    youtube_oauth_client_id: str = ""
    youtube_oauth_client_secret: str = ""
    youtube_channel_id: str = ""
    youtube_api_key: str = ""
    youtube_token_path: Path = Path("data/youtube_token.json")
    retention_dir: Path = Path("data/retention")
    retention_min_days: int = 7
    # Phase 8 학습 루프가 calibration 테이블에서 override 가능한 parameter들.
    retention_peak_cluster_gap_sec: float = 30.0
    retention_peak_pad_left_sec: float = 15.0
    retention_peak_pad_right_sec: float = 30.0
    retention_peak_min_region_sec: float = 60.0
    retention_peak_max_region_sec: float = 120.0
    scene_sample_fps: int = 1
    scene_face_area_threshold: float = 0.20
    scoring_w_gemini_with_retention: float = 0.5
    scoring_w_retention: float = 0.3
    scoring_w_density: float = 0.2
    scoring_w_gemini_cold: float = 0.7
    scoring_w_density_cold: float = 0.3

    # Phase 5: 노션 승인 워크플로우
    notion_token: str = ""
    notion_shorts_db_id: str = ""
    shorts_output_dir: Path = Path("outputs/shorts")

    # Phase 6: 멀티 플랫폼 게시 (Cloudflare R2 + Buffer)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""
    r2_s3_endpoint: str = ""
    buffer_access_token: str = ""
    buffer_organization_id: str = ""  # 비우면 첫 organization 자동 사용
    buffer_youtube_channel_id: str = ""
    buffer_instagram_channel_id: str = ""
    buffer_tiktok_channel_id: str = ""

    # Meta Graph API — Facebook Page 게시용 (메인 Meta App credentials).
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    fb_page_id: str = ""

    # Instagram API setup with Instagram login — 별도 credentials + 별도 OAuth flow.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_access_token: str = ""
    instagram_token_expires_at: str = ""  # ISO timestamp, meta_tokens refresh cron 추적용
    ig_user_id: str = ""

    # Threads API — 별도 OAuth (Meta App과 분리된 credentials).
    threads_app_id: str = ""
    threads_app_secret: str = ""
    threads_access_token: str = ""
    threads_token_expires_at: str = ""  # ISO timestamp, meta_tokens refresh cron 추적용
    threads_user_id: str = ""

    # TikTok Content Posting API.
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_access_token: str = ""
    tiktok_refresh_token: str = ""
    tiktok_open_id: str = ""


settings = Settings()
