# Cloudflare Worker — SwingCrew 게시 Cron

GitHub Actions schedule이 정각 큐잉 지연으로 자주 누락되어, Cloudflare Worker cron으로 대체.
Worker가 정해진 시각(07/11/17/20 KST)에 GitHub `workflow_dispatch` API를 호출 → `publish_slot.yml` workflow 즉시 실행.

## 비용

- Workers free plan: 100,000 requests/일, 10ms CPU/request
- 영빈 사용량: 매일 4번 cron + occasional manual trigger = 월 ~120 requests
- **총 비용: $0** (free plan 한도의 0.0001%)

## Setup (1회 — 영빈 직접 또는 가이드 받아서)

### 1. GitHub Personal Access Token 발급

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
2. Repository access: `swingcrew-auto-shorts`만 선택
3. Permissions → Repository permissions:
   - **Actions: Read and write**
   - Metadata: Read (자동)
4. Expiration: 1년 (만료 전 갱신 알람 등록)
5. Generate → token 복사 (한 번만 보임)

### 2. Cloudflare Worker 생성

**옵션 A — Dashboard (간단, 추천)**:

1. Cloudflare Dashboard → Workers & Pages → Create → Create Worker
2. 이름: `swingcrew-publish-cron`
3. Deploy (기본 코드로 일단 deploy)
4. Edit code → `src/index.js` 내용 전체 복사/붙여넣기 → Save and Deploy
5. Settings → Triggers → Cron Triggers → Add Cron Trigger 4번:
   - `0 22 * * *`
   - `0 2 * * *`
   - `0 8 * * *`
   - `0 11 * * *`
6. Settings → Variables and Secrets → Add 4개 (모두 type=Secret):
   - `GITHUB_PAT` = (1단계 토큰)
   - `GITHUB_OWNER` = `davidhan-biz` (영빈 GitHub username)
   - `GITHUB_REPO` = `swingcrew-auto-shorts`
   - `MANUAL_TOKEN` = (임의 문자열, 수동 trigger용 인증)

**옵션 B — wrangler CLI**:
```powershell
cd infra/cloudflare-worker
npm install -g wrangler
wrangler login
wrangler secret put GITHUB_PAT
wrangler secret put GITHUB_OWNER
wrangler secret put GITHUB_REPO
wrangler secret put MANUAL_TOKEN
wrangler deploy
```

### 3. 동작 검증

**자동 cron**: 다음 슬롯(예: 20:00 KST = 11:00 UTC) 까지 대기 → GitHub Actions에 schedule trigger 대신 `workflow_dispatch` event로 publish_slot 실행 기록 확인 (`gh run list --workflow=publish_slot.yml`).

**수동 trigger** (즉시 게시 필요 시):
```powershell
curl -X POST "https://swingcrew-publish-cron.<영빈-subdomain>.workers.dev/trigger?token=<MANUAL_TOKEN>"
```

응답 200 + `github_status: 204` 면 성공.

## 보안 노트

- `GITHUB_PAT`는 `Actions: Read and write` 권한만. 다른 권한 X.
- `MANUAL_TOKEN`은 외부 URL 노출되니 충분히 길게 (32+ chars random).
- PAT 1년 만료. 만료 전 갱신 필요.

## 폐기 시

Worker 삭제: Dashboard → Worker → Settings → Delete.
GitHub PAT revoke: Settings → Developer settings → Personal access tokens → Revoke.
