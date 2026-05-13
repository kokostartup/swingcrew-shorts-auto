# PRD: SwingCrew Shorts Automation

## 비즈니스 컨텍스트

- **채널**: SwingCrew (~300K 구독자, 골프 바이오메카닉)
- **연계 브랜드**: HUBOR (골프 글러브, YouTube Shopping 연동)
- **미드폼 평균 조회수**: ~18K
- **현재 페인포인트**: 미드폼 1개당 숏츠 수동 편집 + 3개 플랫폼 업로드에
  과도한 시간 투입. 한 영상의 가치를 충분히 회수 못 함.

## 핵심 페르소나

- **영빈** (CEO, 비개발자)
- Claude Code로 직접 개발 진행
- 손작업 시간 5분 이내가 목표
- 콘텐츠 품질과 브랜드 일관성을 비용보다 우선시

## 5단계 파이프라인

1. **수집** (ingest)
   - YouTube에서 신규 미드폼 감지
   - yt-dlp로 원본 영상 다운로드
   - 메타데이터 SQLite 저장

2. **분석** (analyze)
   - WhisperX로 transcript 생성 (word-level timestamp)
   - YouTube Analytics에서 잔존율 곡선 fetch (영상 발행 7일 후)
   - Gemini로 매직 모먼트 후보 추출 (5단 구조: hook→문제→인사이트→데모→결과)
   - 채널 캘리브레이션 기반 threshold로 1~8개 동적 결정

3. **편집** (edit + template)
   - 각 클립 90초 이내로 컷
   - Scene 자동 분류 (토킹헤드/풀바디/분할화면/클로즈업)
   - Scene별 9:16 reframe 전략 적용
   - SwingCrew 시그니처 레이아웃 적용 (검정박스 + 카피 + 자막)

4. **승인** (approve)
   - 노션 DB에 후보 클립 적재 (썸네일 + 미리보기 + 추출 근거)
   - 영빈이 모바일에서 ✅/❌ 토글
   - 승인된 클립만 다음 단계로

5. **게시** (publish)
   - YouTube Shorts: API 업로드 + 미드폼 링크 고정 댓글
   - Instagram Reels: Graph API
   - TikTok: Buffer 경유 또는 드래프트
   - 게시 결과를 노션에 기록

## 5가지 핵심 기능

### F1. 동적 숏츠 개수
영상 길이가 아닌 콘텐츠 품질 밀도 기반 결정.
- 채널 과거 데이터 percentile 70 점수를 threshold로 사용
- Non-max suppression (최소 30초 간격)
- Safety bounds: min(1, video_length // 600), max(3, video_length // 120)

### F2. 잔존율 기반 매직 모먼트
- YouTube Analytics API의 audienceWatchRatio + relativeRetentionPerformance
- 리와치 피크 (잔존율 상승 구간) 우선
- 고지대 정점 (relative_perf > 1.1) 차순위
- Cold start (Day 0~7): Gemini 단독 추출
- 데이터 성숙 후 (Day 7+): 멀티 시그널 점수화

### F3. 시그니처 레이아웃 (실제 SwingCrew 숏츠 기준)
- 9:16 캔버스 (1080×1920)
- **3-zone 구성** (가시 영역은 2개):
  - 상단 검정박스 1080×480
  - 영상 영역 1080×1350 (4:5 비율, 영상이 너무 작아지지 않도록)
  - 하단 1080×90 검정 padding (Shorts/Reels UI가 자동 overlay되는 영역)
- 영상 영역 비율을 4:5로 잡은 이유: letterbox로 16:9 원본을 fit하면 영상이 너무 작아짐. 4:5 crop이면 좌우 약 55%만 잘리고 영역을 꽉 채움.
- **하단 검정 영역에 텍스트/그래픽 그리지 않는다** — YouTube Shorts/Reels/TikTok이 채널 아이콘·제목·재생 컨트롤을 그 자리에 자동 overlay하므로, 우리가 그리면 정보가 겹친다
- 상단 검정박스: 2줄 카피
  - 1줄: 흰색 굵은 카피 (예: "프로들의 스윙 연습")
  - 2줄: 노란색 강조 카피 (예: "이걸 연결해야 합니다!")
- **자막은 원본 미드폼에 이미 burn-in되어 있으므로 숏츠 측에서 추가 작업 없음**
  - Phase 1 편집 단계에서 ASS 자막 합성 단계 생략
  - Transcript(WhisperX)는 Gemini 매직 모먼트 추출 + 상단 카피 자동 생성용으로만 사용

### F4. Scene-aware 9:16 변환
- talking_head_crop: 얼굴 중심 zoom
- letterbox_16_9: 풀바디 보존 (검정 padding)
- fullbody_track: MediaPipe Pose 동적 추적
- split_4panel_sequence: 분할화면 4명 순차 재생

### F5. 멀티 플랫폼 게시
- Buffer API (PoC) → Ayrshare (Production)
- YouTube 고정 댓글로 미드폼 링크 자동 연결
- 노션 DB에 게시 URL 자동 기록

## 성공 지표

- 미드폼 1개당 평균 4~5개 숏츠 자동 추출
- 영빈 손작업 시간 5분 이내 (노션 승인만)
- 생성된 숏츠 평균 조회수 ≥ 채널 기존 숏츠 평균
- 시그니처 레이아웃 일관성 100%

## 비범위 (Not in scope)

- 새 영상 콘텐츠 생성 (AI 더빙 등)
- 라이브 스트림 클립 추출
- 영어 더빙 (별도 프로젝트)
- 웹 대시보드 (노션이 UI 대체)
- 다중 사용자 지원

## Phase 로드맵

- **Phase 0**: 프로젝트 셋업 + 하네스 구축 ← 현재
- **Phase 1**: 시그니처 레이아웃 엔진 (FFmpeg filter_complex)
- **Phase 2**: 영상 수집(yt-dlp) + WhisperX 전사
- **Phase 3**: Gemini 후보 추출 + JSON schema 검증
- **Phase 4**: 잔존율 통합 + Scene 자동 분류 + 멀티시그널 스코어링
- **Phase 5**: 노션 승인 워크플로우 (DB 적재 + 토글 감지)
- **Phase 6**: 멀티 플랫폼 게시 (YouTube/IG/TikTok)
- **Phase 7**: GitHub Actions cron 자동화
- **Phase 8**: 캘리브레이션 + 학습 루프 (percentile threshold 업데이트)
