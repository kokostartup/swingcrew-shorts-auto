/**
 * Cloudflare Worker — SwingCrew 게시 cron trigger + health-check.
 *
 * GitHub Actions schedule cron이 정각 큐잉으로 자주 지연/누락되는 문제 해결.
 * Cloudflare cron triggers는 edge network에서 ~1분 이내 fire 보장.
 *
 * 동작:
 *   1. 정시 cron (07/11/17/20 KST = 22/02/08/11 UTC) → workflow_dispatch
 *   2. 5분 후 cron (XX:05) → 직전 dispatch run health-check
 *      - 여전히 queued/pending이면 cancel + 재dispatch (runner 적체 catch-up)
 *      - in_progress/completed면 정상 진행 — no-op
 *
 * 필요 환경변수 (Worker Secrets):
 *   - GITHUB_PAT: Personal Access Token (workflow scope)
 *   - GITHUB_OWNER: repo owner
 *   - GITHUB_REPO: repo name
 *   - MANUAL_TOKEN: /trigger endpoint 인증
 */

const WORKFLOW_FILE = "publish_slot.yml";
const BRANCH = "main";
// health-check가 직전 5분 내 dispatched run 조회 — 6분 lookback 여유.
const HEALTH_CHECK_LOOKBACK_MIN = 6;

function ghHeaders(env) {
  return {
    "Authorization": `Bearer ${env.GITHUB_PAT}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "swingcrew-cf-cron",
  };
}

async function triggerGithubWorkflow(env, inputs = {}) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const body = { ref: BRANCH };
  if (Object.keys(inputs).length > 0) body.inputs = inputs;
  const resp = await fetch(url, {
    method: "POST",
    headers: { ...ghHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text };
}

async function listRecentRuns(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=5`;
  const resp = await fetch(url, { headers: ghHeaders(env) });
  if (!resp.ok) return null;
  return await resp.json();
}

async function cancelRun(env, runId) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/runs/${runId}/cancel`;
  const resp = await fetch(url, { method: "POST", headers: ghHeaders(env) });
  return { status: resp.status, body: await resp.text() };
}

// 5분 cron — 직전 5분 내 dispatched run이 still queued면 cancel + 재dispatch.
async function healthCheck(env, scheduledTimeIso) {
  const data = await listRecentRuns(env);
  if (!data?.workflow_runs?.length) {
    console.error("health: no runs found");
    return;
  }
  const cutoff = new Date(Date.now() - HEALTH_CHECK_LOOKBACK_MIN * 60 * 1000);
  const stuck = data.workflow_runs.find(r => {
    const created = new Date(r.created_at);
    if (created < cutoff) return false;
    return r.status === "queued" || r.status === "pending" || r.status === "waiting";
  });
  if (!stuck) {
    const latest = data.workflow_runs[0];
    console.log(`health: ok — latest run ${latest.id} status=${latest.status}`);
    return;
  }
  console.warn(`health: run ${stuck.id} stuck in ${stuck.status} — cancelling + retrying`);
  const cancel = await cancelRun(env, stuck.id);
  console.log(`health: cancel status=${cancel.status}`);
  const retry = await triggerGithubWorkflow(env);
  console.log(`health: retry dispatch status=${retry.status}`);
  if (retry.status !== 204) console.error(`health: retry failed: ${retry.body}`);
}

export default {
  // wrangler.toml의 [triggers].crons 배열대로 호출. event.cron으로 분기.
  async scheduled(event, env, ctx) {
    const isHealthCheck = event.cron.startsWith("5 ");
    if (isHealthCheck) {
      console.log(`health-check cron=${event.cron} ts=${event.scheduledTime}`);
      ctx.waitUntil(healthCheck(env, event.scheduledTime));
      return;
    }
    const result = await triggerGithubWorkflow(env);
    console.log(`cron=${event.cron} ts=${event.scheduledTime} github_status=${result.status}`);
    if (result.status !== 204) console.error(`github dispatch failed: ${result.body}`);
  },

  // 수동 trigger — POST /trigger?token=...
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/trigger") {
      return new Response("POST /trigger to manually dispatch publish_slot.yml", { status: 200 });
    }
    if (request.method !== "POST") return new Response("Use POST", { status: 405 });
    const token = url.searchParams.get("token");
    if (!env.MANUAL_TOKEN || token !== env.MANUAL_TOKEN) {
      return new Response("Unauthorized", { status: 401 });
    }
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
