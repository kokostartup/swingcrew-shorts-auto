---
description: Gemini hook 추출 품질 평가
---

# /eval-hooks

영빈 채널의 기존 인기 숏츠 hook 패턴과 Gemini 추출 결과를 비교 평가.

작업 순서:
1. `tests/fixtures/eval_cases.json` 로드
2. 각 케이스에 대해 Gemini hook 추출 실행 (`app/pipeline/analyze.py` 사용)
3. 예상 패턴과 매치 확인:
   - 12자 이내 (공백 제외)
   - 구체적 숫자 포함 여부
   - 결과 약속 표현 ("만에", "차이", "결정", "바꾸면" 등)
4. 케이스별 점수 + 전체 정확도 리포트
5. 정확도 80% 미만이면 프롬프트 개선 제안

`ai-analyst` 서브에이전트 + `gemini-prompting` 스킬 사용.

리포트 형식:
```
[eval_case_id] expected: X / got: Y / score: 2/3 ✓✗✓
...
Overall accuracy: 84% (21/25 patterns matched)
```
