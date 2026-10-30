import { copyFile, cp, mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(scriptDir, "..");
const projectRoot = resolve(siteRoot, "..");
const webRoot = join(projectRoot, "web");
const publicRoot = join(siteRoot, "public");

await mkdir(publicRoot, { recursive: true });

for (const name of [
  "index.html",
  "workspace.html",
  "personal-workspace.html",
  "personal-case.html",
  "health-home.html",
  "health-agent.html",
  "health-agent.css",
  "health-agent-workflows.js",
  "spine-questionnaire.js",
  "spine-questionnaire.css",
  "gait-workspace.html",
  "gait-workspace.js",
  "design-preview.html",
  "workbench.css",
  "case-context.js",
  "case-history.js",
  "login.html",
  "register.html",
  "app.js",
  "personal-app.js",
  "personal-case.js",
  "spine-demo.js",
  "health-home.js",
  "health-agent.js",
  "auth.js",
  "auth.css",
  "styles.css",
  "logo.png",
  "favicon.png",
  "a656e04b461eeb84da85986309c29f53.png",
]) {
  await copyFile(join(webRoot, name), join(publicRoot, name));
}

const caseDemoAssets = [
  "gait-silhouette-sequence.png",
  "surface-to-frontal-pseudo-dr.png",
  "real-dr-lateral.jpg",
  "surface-rgb-side.png",
  "surface-to-lateral-pseudo-dr.png",
  "video-pose-overlay.png",
  "torso-point-cloud.png",
];
const caseDemoPublicRoot = join(publicRoot, "assets", "case-demo");
await mkdir(caseDemoPublicRoot, { recursive: true });
for (const name of caseDemoAssets) {
  await copyFile(join(webRoot, "assets", "case-demo", name), join(caseDemoPublicRoot, name));
}

// Runtime styles and icon fonts must remain available without CDN access.
// Preserve the complete versioned dependency directories and license files.
await cp(join(webRoot, "vendor"), join(publicRoot, "vendor"), { recursive: true });
