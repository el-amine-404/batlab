import { defineConfig } from "vitepress";

// The docs site, published on GitHub Pages by .github/workflows/docs.yml:
// https://el-amine-404.github.io/batlab/songdrop/. Its API reference (/api/) is
// Scalar on a copy of the API's description, public/api/openapi.json (npm run
// openapi); a running songdrop shows its own at /docs.
export default defineConfig({
  base: "/batlab/songdrop/",
  title: "songdrop",
  description: "Paste a link, get the songs: Spotify, YouTube and more, checked and sent to your library.",
  cleanUrls: true,
  lastUpdated: false,
  head: [["link", { rel: "icon", href: "/batlab/songdrop/favicon.svg" }]],
  themeConfig: {
    logo: "/favicon.svg",
    nav: [
      { text: "Guide", link: "/guide/", activeMatch: "/guide/" },
      { text: "API", link: "/guide/api", activeMatch: "/guide/api" },
      { text: "API reference", link: "/api/", target: "_self" },
      { text: "Contributing", link: "/contributing" },
    ],
    socialLinks: [{ icon: "github", link: "https://github.com/el-amine-404/batlab/tree/main/compose/songdrop/app" }],
    editLink: {
      pattern: "https://github.com/el-amine-404/batlab/edit/main/compose/songdrop/app/docs/:path",
      text: "Change this page on GitHub",
    },
    sidebar: [
      {
        text: "Using songdrop",
        items: [
          { text: "Getting started", link: "/guide/" },
          { text: "Download options", link: "/guide/options" },
          { text: "Queue, History, Missing", link: "/guide/activity" },
          { text: "Settings", link: "/guide/settings" },
          { text: "Connect", link: "/guide/connections" },
          { text: "When something fails", link: "/guide/troubleshooting" },
        ],
      },
      {
        text: "For apps",
        items: [
          { text: "Using the API", link: "/guide/api" },
          { text: "API reference", link: "/api/", target: "_self" },
        ],
      },
      { text: "Contributing", items: [{ text: "How songdrop is built", link: "/contributing" }] },
    ],
    search: { provider: "local" },
    outline: { level: [2, 3] },
    footer: { message: "songdrop, part of batlab." },
  },
});
