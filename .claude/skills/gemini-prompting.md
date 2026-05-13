# Skill: gemini-prompting

## When to Use

- `app/pipeline/analyze.py` — 매직 모먼트 추출
- `app/integrations/gemini.py` — Gemini client wrapper
- `app/pipeline/score.py` — LLM 기반 평가
- `ai-analyst` 서브에이전트 호출 시 함께 전달

## How It Works

모든 Gemini 호출 원칙:
1. **JSON mode 강제** — `response_mime_type="application/json"`
2. **Pydantic 스키마 검증** — 응답을 즉시 모델로 파싱
3. **Few-shot from channel** — SwingCrew 기존 인기 hook을 예시로
4. **Temperature 분리** — 추출 0.2 / 평가 0.0 / 카피 0.7
5. **Token 청킹** — transcript는 5분 단위 분할 + 후처리 병합

## Pattern: Magic Moment Extraction

```python
import google.generativeai as genai
from pydantic import BaseModel, Field

class MagicMoment(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    hook_text: str = Field(max_length=24)
    structure: dict  # {hook, problem, insight, demo, result}
    score: float = Field(ge=0, le=10)
    reasoning: str

SYSTEM_INSTRUCTION = """You are SwingCrew의 콘텐츠 분석가. 골프 미드폼 영상의
transcript에서 90초 이내 숏츠 후보를 5단 구조(hook→문제→인사이트→데모→결과)로
추출한다.

Hook 규칙:
- 12자 이내 (공백 제외)
- 구체적 숫자 또는 결과 약속 포함
- 후크 패턴: "X분만에 Y", "단 N% 차이", "N개 중 하나"

JSON schema:
{
  "moments": [
    {
      "start_sec": float,
      "end_sec": float,
      "hook_text": str (≤12자),
      "structure": {"hook": str, "problem": str, "insight": str, "demo": str, "result": str},
      "score": float (0~10),
      "reasoning": str
    }
  ]
}

성공 사례 (SwingCrew 기존 인기 숏츠):
- "스윙 7도만 바꿔도"
- "프로는 이렇게 안 친다"
- "이 한 가지가 비거리 결정"
"""

def extract_moments(transcript: str) -> list[MagicMoment]:
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    )
    resp = model.generate_content(transcript)
    data = json.loads(resp.text)
    return [MagicMoment(**m) for m in data["moments"]]
```

## Pattern: Transcript Chunking (Long Videos)

```python
def chunk_transcript(words: list[dict], chunk_sec: float = 300) -> list[list[dict]]:
    """word-level transcript를 5분 단위 청크로 분할."""
    chunks: list[list[dict]] = [[]]
    chunk_start = words[0]["start"] if words else 0
    for w in words:
        if w["start"] - chunk_start > chunk_sec:
            chunks.append([])
            chunk_start = w["start"]
        chunks[-1].append(w)
    return chunks

def extract_all(transcript_words: list[dict]) -> list[MagicMoment]:
    """청크별 추출 + 경계 중복 제거 (NMS 30초 간격)."""
    moments = []
    for chunk in chunk_transcript(transcript_words):
        chunk_text = format_for_gemini(chunk)
        moments.extend(extract_moments(chunk_text))
    return non_max_suppress(moments, min_gap_sec=30)
```

## Pattern: Cost Guard

```python
def estimate_cost(transcript: str, model: str = "gemini-2.0-flash") -> float:
    """대략적 비용 사전 추정 ($)."""
    # Gemini Flash 1M tokens 입력 = ~$0.075
    tokens = len(transcript) / 4  # 거친 추정
    return tokens / 1_000_000 * 0.075

def with_cost_guard(fn, transcript: str, max_cost: float = 0.01):
    cost = estimate_cost(transcript)
    if cost > max_cost:
        raise CostExceeded(f"Estimated ${cost:.4f} > ${max_cost:.4f}")
    return fn(transcript)
```

## Pattern: Eval Workflow

`tests/fixtures/eval_cases.json`:
```json
[
  {
    "id": "swing_basics_001",
    "transcript_file": "transcripts/sample_001.json",
    "expected_moment_count": [2, 4],
    "expected_hook_patterns": [
      "숫자 포함",
      "12자 이내",
      "결과 약속 표현"
    ]
  }
]
```

평가 스크립트:
```python
def check_hook(hook: str, patterns: list[str]) -> dict:
    return {
        "12자 이내": len(hook.replace(" ", "")) <= 12,
        "숫자 포함": bool(re.search(r"\d", hook)),
        "결과 약속 표현": any(kw in hook for kw in ["만에", "차이", "결정", "바꾸면"]),
    }
```

## Common Pitfalls

| Symptom | Fix |
|---------|-----|
| JSON 파싱 실패 | `response_mime_type="application/json"` 강제 |
| Hook 길어짐 | system instruction에 max length + few-shot |
| 같은 패턴 반복 | examples 5개+ stylistic variety |
| Timestamp 부정확 | word-level transcript 입력 |
| Token 폭주 | 5분 청킹 + max_cost guard |

## Reference

- Gemini docs: https://ai.google.dev/gemini-api/docs
- Pricing: https://ai.google.dev/pricing
- 모델 선택: 추출 작업 = `gemini-2.0-flash-exp` (저렴+빠름)
