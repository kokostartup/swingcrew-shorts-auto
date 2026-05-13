"""SQLite 캐시 및 캘리브레이션 데이터 저장.

Tables:
    - videos: 처리된 미드폼 메타데이터
    - shorts: 생성된 숏츠 정보
    - calibration: 채널별 threshold 학습 데이터
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.storage.models import Video

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id TEXT UNIQUE NOT NULL,
    title TEXT,
    duration INTEGER,
    published_at TEXT,
    retention_fetched_at TEXT,
    processed_at TEXT,
    internal_id TEXT
);

CREATE TABLE IF NOT EXISTS shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id INTEGER REFERENCES videos(id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    score REAL,
    scene_type TEXT,
    status TEXT DEFAULT 'proposed',
    approved_at TEXT,
    published_urls TEXT,
    notion_page_id TEXT,
    scheduled_at TEXT,
    generated_path TEXT,
    internal_id TEXT,
    face_center_x REAL,
    face_segments TEXT,
    opening_line TEXT,
    publish_meta_json TEXT
);

CREATE TABLE IF NOT EXISTS calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at TEXT NOT NULL,
    percentile_70_score REAL,
    top_short_patterns TEXT
);
"""

# 기존 DB에 누락된 컬럼 (ALTER TABLE은 IF NOT EXISTS 지원 X).
_VIDEOS_MIGRATIONS = {
    "internal_id": "TEXT",
}
_SHORTS_MIGRATIONS = {
    "notion_page_id": "TEXT",
    "scheduled_at": "TEXT",
    "generated_path": "TEXT",
    "internal_id": "TEXT",
    "face_center_x": "REAL",
    "face_segments": "TEXT",
    "opening_line": "TEXT",
    "publish_meta_json": "TEXT",
    "pushed_at": "TEXT",
    # 영빈 노션 Hook override (없으면 cached analysis copy 사용).
    "copy1": "TEXT",
    "copy2": "TEXT",
}


def _migrate_columns(
    conn: sqlite3.Connection, table: str, columns: dict[str, str],
) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for name, typ in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.commit()


def init_db(db_path: Path) -> None:
    """SQLite DB 초기화 (마이그레이션 포함)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate_columns(conn, "videos", _VIDEOS_MIGRATIONS)
    _migrate_columns(conn, "shorts", _SHORTS_MIGRATIONS)
    conn.close()


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """init_db 자동 호출 + Row factory 설정된 SQLite 연결."""
    if db_path is None:
        from app.config import settings

        db_path = settings.sqlite_path
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_video(
    conn: sqlite3.Connection,
    *,
    youtube_id: str,
    title: str,
    duration: int,
    published_at: str | None = None,
    internal_id: str | None = None,
) -> int:
    """videos 테이블에 INSERT or UPDATE. row id 반환."""
    cur = conn.execute(
        """
        INSERT INTO videos (youtube_id, title, duration, published_at, internal_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(youtube_id) DO UPDATE SET
            title = excluded.title,
            duration = excluded.duration,
            published_at = excluded.published_at,
            internal_id = COALESCE(excluded.internal_id, videos.internal_id)
        RETURNING id
        """,
        (youtube_id, title, duration, published_at, internal_id),
    )
    row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def get_video_by_youtube_id(
    conn: sqlite3.Connection, youtube_id: str,
) -> Video | None:
    """youtube_id로 Video 조회. 없으면 None."""
    from app.config import settings
    from app.storage.models import Video as _Video

    cur = conn.execute(
        "SELECT * FROM videos WHERE youtube_id = ?", (youtube_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return _Video(
        id=row["id"],
        youtube_id=row["youtube_id"],
        title=row["title"] or "",
        duration=row["duration"] or 0,
        published_at=row["published_at"],
        retention_fetched_at=row["retention_fetched_at"],
        processed_at=row["processed_at"],
        internal_id=row["internal_id"],
        local_path=settings.samples_dir / f"{youtube_id}.mp4",
    )


def mark_processed(conn: sqlite3.Connection, youtube_id: str) -> None:
    """processed_at에 현재 UTC ISO 시각 기록."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE videos SET processed_at = ? WHERE youtube_id = ?",
        (now, youtube_id),
    )
    conn.commit()
