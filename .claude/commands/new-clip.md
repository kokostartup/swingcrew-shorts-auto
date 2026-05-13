---
description: 단일 YouTube 영상 처리 (end-to-end 수동 실행)
---

# /new-clip <youtube-id>

$1 영상에 대해 전체 파이프라인 실행.

작업 순서:
1. `ingest` → yt-dlp로 영상 다운로드, SQLite videos에 등록
2. `transcribe` → WhisperX로 word-level transcript
3. `retention` → YouTube Analytics (Day 7+ 영상만, 아니면 skip)
4. `analyze` → Gemini로 매직 모먼트 후보 추출
5. `score` → 멀티 시그널 점수화 (or Gemini 단독 cold start)
6. `scene` → 각 후보 구간의 Scene 자동 분류
7. `edit + template` → 클립 생성 (시그니처 레이아웃)
8. `publish` → 노션 DB 적재 (실제 게시는 영빈 ✅ 토글 후)

각 단계 결과는 SQLite 캐시. 중단 시 재실행하면 캐시된 단계는 skip.
출력 영상은 `outputs/<youtube_id>/clip_<n>.mp4` 형식.

진행 상황은 structlog JSON으로 stdout. `--dry-run` 옵션으로 분석까지만.
