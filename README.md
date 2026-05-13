# SwingCrew Shorts Automation

YouTube 미드폼을 자동으로 숏츠로 변환하고 멀티 플랫폼에 게시.

## 빠른 시작

```powershell
# 1. 의존성 설치
uv sync

# 2. 환경 변수 설정
copy .env.example .env
# .env 파일에 API 키 입력

# 3. 단일 영상 처리
uv run scripts/run_pipeline.py --video-id <youtube_id>

# 4. 테스트
uv run pytest
```

## 진행 상황

- [x] Phase 0: 프로젝트 셋업 + 하네스 구축
- [x] Phase 1: 시그니처 레이아웃 엔진
- [x] Phase 2: 영상 수집 + 전사
- [x] Phase 3: Gemini 후보 추출
- [x] Phase 4: 잔존율 통합 + Scene 자동 분류
- [x] Phase 5: 노션 승인 워크플로우
- [ ] Phase 6: 멀티 플랫폼 게시
- [ ] Phase 7: GitHub Actions 자동화
- [ ] Phase 8: 캘리브레이션 + 학습 루프

## 문서

- [PRD.md](PRD.md) - 제품 요구사항
- [CLAUDE.md](CLAUDE.md) - Claude Code 컨텍스트 + 코딩 규범
- [.claude/rules/](.claude/rules/) - 항상 따르는 규칙

## 환경 요구

- Windows 11 (PowerShell)
- Python 3.11+ (uv가 자동 관리)
- FFmpeg (PATH)
- yt-dlp (PATH)
- Git
