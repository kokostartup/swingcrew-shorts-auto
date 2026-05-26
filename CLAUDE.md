# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SwingCrew Shorts Automation - YouTube 미드폼 영상을 자동으로 숏츠로 변환하고
YouTube Shorts / Instagram Reels / TikTok / Threads에 자동 게시하는 시스템.

운영자: 영빈 (SwingCrew CEO, **비개발자**). 노션 DB가 사실상 영빈의 UI —
✅/❌ 토글, Title/Description override, Scene/Time override, Scheduled At 수정 등
모든 운영 액션이 노션에서 일어나고 cron이 SQLite와 양방향 sync.
실행 방식: Windows 로컬 + Task Scheduler (daily 12:20 KST cron).

## Behavioral Guidelines

**These four principles override all other instructions when in conflict.**
**Tradeoff:** These guidelines bias toward caution over speed.
For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?"
If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria
("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs,
fewer rewrites due to overcomplication, and clarifying questions come
before implementation rather than after mistakes.

## Architecture

### 디렉토리
- **app/pipeline/** - Sequential processing steps (ingest → transcribe → analyze →
  score → edit → publish_meta → publish, + schedule/approve/retention/scene/template)
- **app/integrations/** - External API clients (YouTube, Notion, Gemini, Buffer, R2)
- **app/storage/** - SQLite (`data/state.db`) — `videos`, `shorts`, `calibration` 테이블
- **scripts/** - CLI entry points
- **.github/workflows/publish_slot.yml** - FB/IG/Threads 게시 워크플로우 (workflow_dispatch만 — schedule cron 제거됨)
- **infra/cloudflare-worker/** - 정시 cron trigger (GitHub Actions schedule 큐잉 지연 회피용)
- **.claude/** - Claude Code harness (agents, skills, commands, hooks, rules)

각 파이프라인 단계는 독립 실행 가능하며 결과를 SQLite에 캐시 → 재실행 시
expensive op (yt-dlp 다운로드, WhisperX 전사, Gemini 호출) 스킵.

### Daily cron — `scripts/run_daily.py` (시스템의 심장)

매일 12:20 KST에 Windows 작업 스케줄러가 6단계를 순차 실행. **미드폼 ingest는
cron에서 제거** (영빈 결정 2026-05-13): 영빈이 어떤 미드폼을 숏츠화할지 manual로
결정하는 게 control 측면에서 낫다. cron은 영빈 검토 결과의 자동 후처리만.

1. **노션 → SQLite sync** — 영빈이 토글한 ✅/❌, Scene/Time/Title/Desc/Scheduled At
   override를 SQLite로 반영.
2. **ffmpeg + Gemini 메타** — `status='approved'` 모먼트를 ffmpeg로 잘라서 mp4
   생성 + Gemini로 Title/Description/태그 생성 + 노션 push → `status='generated'`.
3. **Scheduled At 자동 할당** — `status='generated'` + `scheduled_at IS NULL`인 행에
   다음 빈 슬롯 할당. 슬롯: 매일 KST 07/11/17/20시, 최소 24h 검토 lead 보장
   (`MIN_LEAD_HOURS=24`). 노션 Scheduled At 컬럼도 동시 업데이트.
4. **YouTube 예약 게시** — Scheduled At 있는 `status='generated'` 모먼트 → R2 업로드(ko만) +
   YouTube private + publishAt 예약 → `status='scheduled'`. FB/IG/Threads는 별도 흐름
   (아래 "슬롯 게시 흐름" 참고). TikTok은 Buffer queue로 영빈이 수동 처리.
   EN 채널은 YouTube only — R2 업로드 자체 skip (FB/IG/Threads/Buffer 안 함).
5. **자동 거절** — `pushed_at`이 7일 이상 지났는데 영빈이 ✅ 안 한 'proposed'
   모먼트 → 자동 `rejected`.
6. **Phase 8 calibration** — `calibrate()` 호출. 미드폼 retention spike 분포 +
   게시 7일+ 숏츠 YouTube Analytics → `calibration` 테이블 row. 다음 분석부터
   `latest_calibration()` fetch로 자동 적용.

### 미드폼 수동 trigger (영빈이 cron 외부에서 호출)
영빈이 숏츠화할 미드폼 youtube_id를 주면:
- 단건: `uv run scripts/run_pipeline.py --video-id <yt_id>`
- 다건 배치: `scripts/run_backlog.py` 패턴 (BACKLOG 리스트 inline)
- 처리 흐름: ingest → transcribe → analyze (Gemini + retention) → 노션 'proposed' push
- 다음 cron에서 영빈 ✅한 모먼트만 자동 후처리.

### 상태 머신 (`shorts.status`)
`proposed` → (영빈 ✅) → `approved` → (ffmpeg+메타) → `generated` →
(slot 할당) → (publish_ready) → `scheduled` → (슬롯 시각 publish_socials) → `published`.
거절 경로: `proposed` → `rejected` (영빈 ❌ 또는 7일 무응답 자동).
`error`는 처리 실패 표시.

### 슬롯 게시 흐름 (Cloudflare Worker → GitHub Actions)
YouTube는 `publish_ready`가 publishAt으로 예약하면 시각 도래 시 YouTube 자체가 게시.
FB/IG/Threads는 외부 cron trigger가 필요:

1. **Cloudflare Worker cron** ([infra/cloudflare-worker/](infra/cloudflare-worker/)) — 매 슬롯 시각
   (07/11/17/20 KST) GitHub workflow_dispatch API 호출. GitHub schedule cron은 정각
   큐잉 지연/누락 잦아서 Cloudflare로 대체 (2026-05-14 결정).
2. **GitHub Actions** ([publish_slot.yml](.github/workflows/publish_slot.yml)) — workflow_dispatch만,
   schedule 제거됨. `publish_socials_from_notion.py` 실행.
3. **publish_socials_from_notion.py** — 노션 'scheduled' 페이지 fetch → 현재 시각 ±15분
   필터 → R2 URL fetch → FB/IG/Threads 게시 → 노션 status='게시' 전환 →
   **R2 mp4 자동 삭제** (게시 끝나면 R2 fetch source 불필요 → 스토리지 정리).
4. **TikTok**: Buffer queue로 영빈 PC에서 publish_ready 직후 수동 추가 (publish_socials에서 제거됨).
5. **R2 catch-up cleanup**: `scripts/r2_cleanup_published.py` — hook 누락된 published mp4
   (또는 명시 `--keys`)를 일괄 삭제. `--channel ko` 노션 published 기준, `--channel en`
   SQLite 기준.

**catch-up trigger** (cron 지연 시):
```powershell
$env:TARGET_INTERNAL_IDS = "26-P002-S04"  # 콤마 구분 여러 개 가능
.venv/Scripts/python.exe scripts/publish_socials_from_notion.py
```
`DRY_RUN=true` env로 실제 게시 X (통신 검증용).

### 게시 전 메타 override 우선순위 (publish.py `_resolve_meta`)
1. 노션 Title/Description (영빈 수정값) → 2. SQLite `publish_meta_json` (Gemini
캐시) → 3. Gemini 즉시 재생성. 영빈 수정값이 항상 최상위.

### 모먼트 개수 알고리즘 (analyze.py `_dynamic_max_moments`)
영상 종류와 길이로 모먼트 상한 N 결정 후 Gemini에 "top N개" 요청:
- **B 시리즈** (`26-B0XX`, 영빈 narration): **5 고정** — narration 깊이 한정적
- **P 시리즈** (`26-P0XX`, 프로 레슨): **`max(3, duration // 120)`** — 2분당 1개,
  최소 3개. 예: 6분→3, 10분→5, 15분→7, 22분→10
- 영빈이 요청한 분기. 미구현된 PRD F1 (percentile 70 score threshold + calibration
  연동)은 Phase 8 학습 루프에서 채울 부분 — 지금은 양적 cap만 작동, 질적 threshold X.
- NMS: 모먼트 간 시작점 최소 30초 간격 (`gemini_min_gap_sec=30`).
- 안전장치: moment duration 45~80초 (45 미만 권장 X, 80 초과 reject), 목표 60~75초.
  80초 cap은 2026-05-21 calibration 분석: 게시 7일+ 26개 sample에서 80초 초과 시
  평균 views 절반 이하로 급락 (~3,820 vs ~8,300) → analyze prompt + MagicMoment
  validator 모두 80s로 강화.

### Gemini start_sec 보정 (analyze.py `_snap_start_sec`)
Gemini가 transcript의 `[start-end]` 라벨에서 `end`값을 다음 hook 시작으로 잡는
경향이 있음 (그 시점은 발화 silence). 후처리로 그 이후 첫 발화 word.start로 snap →
영상 cut + opening_line 둘 다 실제 발화 시작과 일치.

### 시그니처 카피 렌더링 (template.py / edit.py)
- **ASS subtitles (libass)** 사용 — drawtext per-glyph 폐기 (자간/% glyph silent skip 등 문제).
  `write_signature_ass`가 ASS 파일 생성, `signature_filter_segment_ass`가 ffmpeg
  `subtitles=` filter chain. ASS 파일은 mp4 옆에 임시 저장 후 ffmpeg 성공 시 정리.
- **copy1/copy2 독립 fit fontsize** — `_ass_fit_single_line`이 PIL `ImageFont.getlength`
  (advance sum, ASS libass와 동일 metric)로 측정. 짧은 줄은 더 크게, 긴 줄은 max에 맞춰.
- **Pretendard-Black.otf** — fontsdir로 명시. ASS Style Fontname=`Pretendard Black`.
- **Audio mono-mix** — `pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1` 모든 영상에 적용
  (P006 R-only stereo 원본 정규화 + 정상 stereo도 양쪽 mix). dynamic은 filter_complex
  내 chain, 기타 strategy는 `-af` 옵션.

### dynamic scene (face_centered_dynamic)
- segments 4-tuple `(start, end, cx, face_count)`. face_count는 `_build_segments`가
  YOLOv8-face로 측정한 frame별 face_count의 segment 최빈값.
- segment 별 처리: face_count==1 → cover scale crop (1명 close-up), face_count>=2 또는
  ==0 또는 face_area<1% → wide letterbox 6:4 (시연/multi-person, padding 검정).
- segment 사이 `XFADE_DURATION=0.3s` cross-fade + 끝 연장으로 어미 안 잘림.
- approve.py `process_approved`가 3-tuple legacy segments 자동 감지 → rescan으로 갱신.

### Phase 8 calibration 학습 루프 (`app/pipeline/calibrate.py`)
- **C (미드폼 retention)** — 채널 미드폼 retention curve의 양수 slope 분포 →
  `spike_threshold` percentile 80. `detect_peak_regions`에 주입해 retention region
  검출이 채널 데이터 기반으로 자동 엄격화 (기본 양수 평균보다 ~45% 엄격).
- **B (게시 7일+ 숏츠)** — 게시 후 retention 안정된 숏츠의 YouTube views/watch_time →
  top 25% 모먼트의 scene_type 분포, hook 단어 빈도. 5/19+ 데이터 쌓이면 활성화.
- **A (영빈 ✅/❌ 통계)는 skip** — 영빈이 "거의 다 ✅한다"고 명시, 신호 약함.
- `calibration` 테이블에 row append (history). analyze.py `latest_calibration()`이
  최신 row fetch → `detect_peak_regions(spike_threshold=...)` 자동 주입.
- 매일 cron Step 6에서 호출 → 매일 row 1개 추가, B 데이터는 점진 누적.

## Running Tests

```powershell
uv run pytest                                      # 기본 (slow + external 제외, pyproject 설정)
uv run pytest tests/test_template.py               # 단일 파일
uv run pytest tests/test_score.py::test_calibrate  # 단일 케이스
uv run pytest -m "slow"                            # 영상 처리 등 느린 테스트
uv run pytest -m "external"                        # 실 API 호출 (네트워크 필요)
uv run ruff check                                  # Lint
uv run ruff format                                 # Format
```

마커: `slow` (영상 처리), `external` (실 API). 기본 실행은 둘 다 제외 — `pyproject.toml`
의 `addopts = "-m 'not slow and not external'"`.

## Key Commands

### CLI 엔트리
- `uv run scripts/run_daily.py` - **6단계 cron 수동 실행** (디버깅용)
- `uv run scripts/run_daily.py --dry-run` - 흐름만 표시, 실 처리 X
- `uv run scripts/run_pipeline.py --video-id <youtube_id>` - 단일 영상 end-to-end
- `uv run scripts/ingest_transcribe.py --video-id <id>` - 수집+전사만
- `uv run scripts/analyze.py --video-id <id>` - Gemini 모먼트 추출만
- `uv run scripts/sync_notion.py` - 노션 ↔ SQLite 단발 sync
- `uv run scripts/test_template.py <path>` - 시그니처 레이아웃 시각 테스트
- `uv run scripts/calibrate.py` - 채널 percentile 70 score 재계산

### Slash 명령 (.claude/commands/)
- `/run-phase <N>` - Phase N 작업 시작
- `/test-template <video-path>` - 시그니처 레이아웃 엔진 테스트
- `/new-clip <youtube-id>` - 영상 1개 end-to-end 수동 실행
- `/eval-hooks` - Gemini 후킹 추출 품질 평가

### Windows 작업 스케줄러 등록 (영빈 PC 1회 설정)
```powershell
schtasks /create /tn SwingCrewDaily `
  /tr "c:\Users\User\coding\swingcrew-auto-shorts\.venv\Scripts\python.exe c:\Users\User\coding\swingcrew-auto-shorts\scripts\run_daily.py" `
  /sc daily /st 12:20 /f
schtasks /query /tn SwingCrewDaily /v /fo LIST    # 등록 확인
schtasks /run /tn SwingCrewDaily                  # 즉시 1회 실행 (테스트)
```

## Development Notes

- Python 3.11+ required
- Package manager: uv (not pip/poetry)
- All external API calls use tenacity for retry with exponential backoff
- All secrets in `.env`, loaded via pydantic-settings
- Logs use structlog (JSON format), never print()
- Windows native (PowerShell). Hooks are .ps1 files.

## Coding Conventions

- Function-first, classes only when state is justified
- Type hints required for all public functions
- Docstrings in Korean OK for domain-specific functions
- Each pipeline step independently runnable from CLI
- Korean comments OK in domain logic (golf, video editing)

## Skills to Use

| Files | Skill |
|-------|-------|
| `app/pipeline/edit.py`, `app/pipeline/template.py` | `ffmpeg-patterns` |
| `app/pipeline/analyze.py`, `app/integrations/gemini.py` | `gemini-prompting` |
| `app/integrations/youtube.py`, `app/pipeline/retention.py` | `youtube-api` |

When spawning subagents, always pass the relevant skill into the agent's prompt.

## Subagent Delegation

When the task fits one of these domains, delegate to the matching subagent:

- **planner**: Phase 시작 시 구현 계획 수립
- **python-reviewer**: Python 코드 작성/수정 후 리뷰
- **harness-optimizer**: 하네스 자체 개선
- **video-engineer**: FFmpeg, OpenCV, MediaPipe 작업
- **ai-analyst**: Gemini 프롬프트, transcript 분석, scene 분류
- **api-integrator**: YouTube/Notion/Buffer 통합
- **test-engineer**: pytest, 검증, eval 케이스

## Never Do

- Hardcode API keys (always use `.env` via pydantic-settings)
- Commit `outputs/`, `data/state.db`, `.env`, video/audio files
- Use `print()` (use structlog)
- Add features beyond what was requested (Behavioral #2)
- Refactor unrelated code (Behavioral #3)
- Skip writing a verification step (Behavioral #4)
- **`publish_ready()` / YouTube upload / R2 upload 자동 호출 금지** — 영빈 명시
  ("예약 걸어줘"/"게시해") 후에만. `process_approved` (mp4 + 메타)까지가 자동 한계.
