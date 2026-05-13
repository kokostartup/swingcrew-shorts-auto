---
description: 시그니처 레이아웃 엔진 테스트 (Phase 1 산출물)
---

# /test-template <video-path>

$1 영상에 시그니처 레이아웃을 4가지 scene strategy 모두 적용해 테스트.

작업 순서:
1. 입력 영상 메타데이터 확인 (`ffprobe`로 해상도/fps/codec)
2. 4가지 strategy 순차 실행:
   - `talking_head_crop` → `outputs/test/talking_head.mp4`
   - `letterbox_16_9` → `outputs/test/letterbox.mp4`
   - `fullbody_track` → `outputs/test/fullbody.mp4`
   - `split_4panel_sequence` → `outputs/test/split_4panel.mp4`
3. 각 출력의 메타데이터 검증 (1080×1920 / 30fps / H.264 + AAC)
4. 처리 시간 + 파일 크기 리포트
5. 4개 모두 검증 통과 시 ✅, 하나라도 실패 시 ❌

`video-engineer` 서브에이전트 + `ffmpeg-patterns` 스킬 사용.
