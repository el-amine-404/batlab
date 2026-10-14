// Builds the docs (/docs) from ../docs/*.md into dist/docs/<name>.html with
// markdown-it: one page per file, a sidebar of the pages and of the open page's
// sections. Each file starts with front matter: title and order.
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import MarkdownIt from "markdown-it";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, process.env.DOCS_DIR ?? "../../docs");
const target = resolve(here, "../dist/docs");

const escape = (text) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const slug = (text) =>
  text
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s+/g, "-");

function page(file) {
  const raw = readFileSync(join(source, file), "utf8");
  const match = raw.match(/^---\n([\s\S]*?)\n---\n/);
  const meta = Object.fromEntries(
    (match?.[1] ?? "").split("\n").map((line) => line.split(/:\s*/, 2)).filter((pair) => pair.length === 2),
  );
  const name = file.replace(/\.md$/, "");
  return { name, title: meta.title ?? name, order: Number(meta.order ?? 99), body: raw.slice(match?.[0].length ?? 0) };
}

function render(markdown) {
  const md = new MarkdownIt({ html: false, linkify: true, typographer: true });
  const sections = [];
  const used = new Set();
  md.core.ruler.push("anchors", (state) => {
    state.tokens.forEach((token, index) => {
      if (token.type !== "heading_open") return;
      const text = state.tokens[index + 1].content;
      let id = slug(text);
      for (let n = 2; used.has(id); n++) id = `${slug(text)}-${n}`;
      used.add(id);
      token.attrSet("id", id);
      if (token.tag === "h2" || token.tag === "h3") sections.push({ id, text, level: token.tag });
    });
  });
  // Tables scroll on their own on a phone instead of widening the page.
  md.renderer.rules.table_open = () => '<div class="table"><table>';
  md.renderer.rules.table_close = () => "</table></div>";
  return { html: md.render(markdown), sections };
}

const pages = readdirSync(source)
  .filter((file) => file.endsWith(".md"))
  .map(page)
  .sort((a, b) => a.order - b.order);
const href = (name) => (name === "index" ? "/docs/" : `/docs/${name}`);

mkdirSync(target, { recursive: true });
for (const current of pages) {
  const { html, sections } = render(current.body);
  const nav = pages
    .map((item) => {
      const active = item.name === current.name;
      const toc = active
        ? `<ul class="toc">${sections
            .map((s) => `<li class="${s.level}"><a href="#${s.id}">${escape(s.text)}</a></li>`)
            .join("")}</ul>`
        : "";
      return `<li><a class="page${active ? " active" : ""}" href="${href(item.name)}">${escape(item.title)}</a>${toc}</li>`;
    })
    .join("");
  const document = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escape(current.title)} · songdrop docs</title>
<link rel="icon" href="/favicon.svg">
<link rel="stylesheet" href="/docs/docs.css">
</head>
<body>
<header class="bar">
  <a class="brand" href="/docs/"><img src="/favicon.svg" alt="" width="24" height="24">songdrop <span>docs</span></a>
  <a class="back" href="/">Open songdrop →</a>
</header>
<div class="layout">
  <nav class="side" aria-label="Docs"><ul>${nav}</ul></nav>
  <main class="doc">${html}</main>
</div>
</body>
</html>
`;
  writeFileSync(join(target, `${current.name}.html`), document);
}
writeFileSync(join(target, "docs.css"), readFileSync(join(here, "docs.css")));
console.log(`docs: ${pages.map((p) => p.name).join(", ")} → ${target}`);
