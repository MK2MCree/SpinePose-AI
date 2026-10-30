import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  return (await import(workerUrl.href)).default;
}

function runtimeEnv() {
  return {
    ASSETS: {
      async fetch(request) {
        const pathname = new URL(request.url).pathname;
        if (pathname !== "/index.html") {
          return new Response("Not found", { status: 404 });
        }
        const html = await readFile(
          new URL("../public/index.html", import.meta.url),
          "utf8",
        );
        return new Response(html, {
          status: 200,
          headers: { "content-type": "text/html" },
        });
      },
    },
  };
}

const ctx = {
  waitUntil() {},
  passThroughOnException() {},
};

test("ships the real product homepage with social metadata", async () => {
  const html = await readFile(
    new URL("../public/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /SpineInsight/);
  assert.match(html, /property=["']og:image["']/);
  assert.match(html, /name=["']twitter:image["']/);
  assert.match(html, /\/og\.png/);
});

test("keeps legacy entry points compatible", async () => {
  const worker = await loadWorker();

  const homepage = await worker.fetch(
    new Request("https://spineinsight.example/index1.html"),
    runtimeEnv(),
    ctx,
  );
  assert.equal(homepage.status, 308);
  assert.equal(homepage.headers.get("location"), "https://spineinsight.example/");

  const workspace = await worker.fetch(
    new Request("https://spineinsight.example/workspace1.html"),
    runtimeEnv(),
    ctx,
  );
  assert.equal(workspace.status, 308);
  assert.equal(
    workspace.headers.get("location"),
    "https://spineinsight.example/workspace.html",
  );
});

test("ships the browser API contract used by the clinical workspace", async () => {
  const [html, script] = await Promise.all([
    readFile(new URL("../public/workspace.html", import.meta.url), "utf8"),
    readFile(new URL("../public/app.js", import.meta.url), "utf8"),
  ]);

  for (const id of [
    "xray-batch-result-list",
    "rgb-batch-result-list",
    "btn-run-xray-batch-left",
    "btn-run-rgb-batch-pipeline",
  ]) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
    assert.match(script, new RegExp(id));
  }

  assert.match(script, /credentials:\s*["']same-origin["']/);
  assert.match(script, /\/api\/xray["']/);
  assert.match(script, /\/api\/rgb["']/);
  assert.match(script, /\/api\/pseudoct\/single/);
  assert.match(html, /auth\.js/);
});

test("ships the local account pages and common account client", async () => {
  const [login, register, authClient] = await Promise.all([
    readFile(new URL("../public/login.html", import.meta.url), "utf8"),
    readFile(new URL("../public/register.html", import.meta.url), "utf8"),
    readFile(new URL("../public/auth.js", import.meta.url), "utf8"),
  ]);

  assert.match(login, /id="login-form"/);
  assert.match(register, /id="register-avatar"/);
  assert.match(authClient, /\/api\/auth\/login/);
  assert.match(authClient, /\/api\/auth\/register/);
});

test("ships the protected personal screening workspace", async () => {
  const [html, script] = await Promise.all([
    readFile(new URL("../public/personal-workspace.html", import.meta.url), "utf8"),
    readFile(new URL("../public/personal-app.js", import.meta.url), "utf8"),
  ]);

  for (const label of ["RGB检测", "DR检测", "步态评估"]) {
    assert.match(html, new RegExp(label));
  }
  for (const endpoint of [
    "/api/upload",
    "/api/rgb",
    "/api/crop",
    "/api/pseudoct/single",
    "/api/pseudoct/loop_to_xray",
    "/api/xray",
  ]) {
    assert.match(script, new RegExp(endpoint.replaceAll("/", "\\/")));
  }
  assert.match(html, /id="personal-video-summary"/);
  assert.match(script, /gait-workspace\.html/);
  assert.doesNotMatch(html, /侧弯初筛/);
  assert.doesNotMatch(html, /模型准备中/);
  assert.doesNotMatch(script, /updateVideoScreening/);
  assert.match(script, /MindSpineAuth\?\.requireUser/);
  assert.match(script, /saveCaseEntry/);
  assert.match(html, /personal-case\.html/);
});

test("ships the personal longitudinal case page", async () => {
  const [html, script] = await Promise.all([
    readFile(new URL("../public/personal-case.html", import.meta.url), "utf8"),
    readFile(new URL("../public/personal-case.js", import.meta.url), "utf8"),
  ]);

  for (const id of ["case-profile-form", "case-clinical-form", "case-checkin-form", "case-trend-svg", "case-entry-list", "case-questionnaire-analysis", "btn-case-view-questionnaire"]) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
  }
  assert.match(script, /\/api\/personal-case/);
  assert.match(script, /MindSpineAuth\?\.requireUser/);
  assert.match(script, /window\.print\(\)/);
  assert.match(html, /#spine-agent-workbench\{display:block\}/);
  assert.match(script, /renderQuestionnaireSummary/);
});

test("ships the separated health center and controlled agent pages", async () => {
  const [home, homeScript, agent, agentScript, demoScript] = await Promise.all([
    readFile(new URL("../public/health-home.html", import.meta.url), "utf8"),
    readFile(new URL("../public/health-home.js", import.meta.url), "utf8"),
    readFile(new URL("../public/health-agent.html", import.meta.url), "utf8"),
    readFile(new URL("../public/health-agent.js", import.meta.url), "utf8"),
    readFile(new URL("../public/spine-demo.js", import.meta.url), "utf8"),
  ]);

  for (const page of ["health-home.html", "personal-workspace.html", "personal-case.html", "health-agent.html"]) {
    assert.match(home, new RegExp(page.replaceAll(".", "\\.")));
  }
  for (const id of ["agent-triage-status", "agent-chat-form", "agent-action-list", "btn-agent-toggle-consent", "agent-questionnaire-analysis"]) {
    assert.match(agent, new RegExp(`id=["']${id}["']`));
  }
  assert.match(home, /Multimodal evidence matrix/);
  assert.match(home, /Case briefing \/ today/);
  assert.match(agent, /当前病例/);
  assert.match(agent, /agent-sidebar right/);
  assert.match(homeScript, /\/api\/cases\//);
  assert.match(homeScript, /legacy_timeline/);
  assert.match(agentScript, /\/api\/cases\//);
  assert.match(agentScript, /\/api\/agent-actions\//);
  assert.match(agentScript, /local, immutable case first/);
  assert.match(agentScript, /已完成 · \$\{assetCount\} 项/);
  assert.match(home, /hub-trend-chart/);
  assert.match(home, /hub-analysis-grid/);
  assert.match(home, /spine-demo\.js/);
  assert.match(agent, /data-demo-banner/);
  assert.match(demoScript, /演示病例 · 腰背随访/);
  assert.match(demoScript, /demo-message-agent-intake/);
  assert.match(demoScript, /demo-review-001/);
  assert.match(demoScript, /demo-action-review/);
  assert.match(demoScript, /adult-spine-triage-demo-v1/);
  assert.match(demoScript, /real-dr-lateral\.jpg/);
  assert.match(demoScript, /torso-point-cloud\.png/);
  assert.match(homeScript, /演示记录 · 非诊断/);
});
