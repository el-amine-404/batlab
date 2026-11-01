// Each SVG here to a 256-pixel PNG: `node render.mjs` with playwright-core and Chromium at hand.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
const here = dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium-browser", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 256, height: 256 } });
for (const file of readdirSync(here).filter((name) => name.endsWith(".svg"))) {
  await page.setContent(`<!doctype html><style>html,body{margin:0;background:transparent}svg{display:block;width:256px;height:256px}</style>${readFileSync(join(here, file), "utf8")}`);
  await page.screenshot({ path: join(here, file.replace(".svg", ".png")), omitBackground: true });
}
await browser.close();
