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
});
