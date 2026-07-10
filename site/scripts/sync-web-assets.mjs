import { copyFile, mkdir } from "node:fs/promises";
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
  "app.js",
  "styles.css",
  "a656e04b461eeb84da85986309c29f53.png",
]) {
  await copyFile(join(webRoot, name), join(publicRoot, name));
}

await copyFile(join(projectRoot, "optimized_docs", "mindspine_logo.png"), join(publicRoot, "favicon.png"));

