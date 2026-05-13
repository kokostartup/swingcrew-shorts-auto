/**
 * Cloudflare Worker — SwingCrew 게시 cron trigger.
 *
 * GitHub Actions schedule cron이 정각 큐잉으로 자주 지연/누락되는 문제 해결.
 * Cloudflare cron triggers는 edge network에서 ~1분 이내 fire 보장.
 *
 * 동작:
 *   1. Cloudflare cron trigger fire (07/11/17/20 KST = 22/02/08/11 UTC)
 *   2. GitHub workflow_dispatch API 호출 → publish_slot.yml workflow 즉시 실행
 *   3. GitHub Actions가 publish_socials_from_notion.py 실행 → 노션 'scheduled' 모먼트 게시
 *
 * 필요 환경변수 (Worker Secrets):
 *   - GITHUB_PAT: Personal Access Token (workflow scope)
 *   - GITHUB_OWNER: repo owner (예: 'davidhan-biz')
 *   - GITHUB_REPO: repo name (예: 'swingcrew-auto-shorts')
 */

const WORKFLOW_FILE = "publish_slot.yml";
const BRANCH = "main";

async function triggerGithubWorkflow(env, inputs = {}) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const body = { ref: BRANCH };
  if (Object.keys(inputs).length > 0) {
    body.inputs = inputs;
  }
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_PAT}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "swingcrew-cf-cron",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  // 204 = success (no body). 그 외는 실패.
  const text = await resp.text();
  return { status: resp.status, body: text };
}

export default {
  // Cron trigger 진입점. wrangler.toml의 [triggers].crons 배열대로 호출됨.
  async scheduled(event, env, ctx) {
    const result = await triggerGithubWorkflow(env);
    console.log(`cron=${event.cron} ts=${event.scheduledTime} github_status=${result.status}`);
    if (result.status !== 204) {
      console.error(`github dispatch failed: ${result.body}`);
    }
  },

  // 수동 trigger용 — 브라우저로 worker URL 호출하면 즉시 workflow_dispatch.
  // 디버깅 + 즉시 게시용 (영빈 PC 외부에서).
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/trigger") {
      return new Response(
        "POST /trigger to manually dispatch publish_slot.yml",
        { status: 200 }
      );
    }
    if (request.method !== "POST") {
      return new Response("Use POST", { status: 405 });
    }
    // 간단 인증 — query param token 확인.
    const token = url.searchParams.get("token");
    if (!env.MANUAL_TOKEN || token !== env.MANUAL_TOKEN) {
      return new Response("Unauthorized", { status: 401 });
    }
    // dry_run=true query면 workflow가 social API 호출 skip (통신 검증용).
    const dryRun = url.searchParams.get("dry_run") === "true";
    const skipTime = url.searchParams.get("skip_time_filter") === "true";
    const inputs = {};
    if (dryRun) inputs.dry_run = "true";
    if (skipTime) inputs.skip_time_filter = "true";
    const result = await triggerGithubWorkflow(env, inputs);
    return new Response(
      JSON.stringify({ github_status: result.status, body: result.body, inputs }),
      { status: result.status === 204 ? 200 : 500, headers: { "Content-Type": "application/json" } }
    );
  },
};
