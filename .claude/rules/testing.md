# Testing Rules

## 테스트 작성 시점

- 새 파이프라인 단계 추가 시 검증 스크립트 필수
- 새 외부 API 통합 시 mock 테스트 필수
- 버그 수정 시 재현 테스트 먼저 작성 (TDD)

## 마커 사용

- `@pytest.mark.slow`: 영상 처리 등 시간 오래 걸리는 테스트
- `@pytest.mark.external`: 실 API 호출 (CI에서는 skip)
- 기본 실행은 빠른 테스트만 (`pytest -m "not slow and not external"`)

## Mock 데이터

- 외부 API mock 응답은 `tests/fixtures/`에 JSON으로 저장
- Gemini 응답 샘플은 `tests/fixtures/gemini/`
- YouTube API 응답은 `tests/fixtures/youtube/`

## 영상 처리 검증

- 출력 영상은 항상 다음 자동 검증:
  - 해상도 (1080×1920)
  - fps (30)
  - 코덱 (H.264 + AAC)
  - 파일 크기 (≤ 25MB per 90초, duration 비례, 최소 3MB 허용; libx264 crf22 화질 우선 기준)
  - 총 길이 (요청한 duration ±0.5초)

## 커버리지 목표

- 새 코드: 80%+ 커버리지
- 핵심 파이프라인 함수: 100%
- 외부 API 어댑터: mock 기반 테스트 필수
