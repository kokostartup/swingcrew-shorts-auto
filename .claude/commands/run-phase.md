---
description: 지정한 Phase 작업을 시작. PRD.md와 해당 Phase 스펙 로드.
---

# /run-phase <N>

Phase $1을 시작합니다.

작업 순서:
1. `PRD.md`에서 Phase $1 섹션 읽기 (Phase 로드맵)
2. `.claude/rules/behavioral-guidelines.md` 4원칙 재확인
3. `planner` 서브에이전트 호출 → Phase $1 구현 계획 수립
4. 계획 검토 + 영빈 승인 대기
5. 단계별 실행. 각 단계마다 검증 기준 정의 (Behavioral #4)
6. Phase 완료 후 `README.md` 진행상황 체크박스 업데이트

도메인에 맞는 서브에이전트 자동 위임:
- 영상 처리 → `video-engineer` + skill `ffmpeg-patterns`
- AI/프롬프트 → `ai-analyst` + skill `gemini-prompting`
- API 통합 → `api-integrator` + skill `youtube-api`
- 테스트 → `test-engineer`

Python 코드 작성 후엔 항상 `python-reviewer` 호출.
