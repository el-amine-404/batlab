import { defineConfig } from "vitepress";

// The docs site, served by songdrop at /docs/. The API reference (/docs/api/)
// is Scalar on the running API's OpenAPI description: public/api/index.html.
export default defineConfig({
  base: "/docs/",
  title: "songdrop",
  description: "Paste a link, get the songs: Spotify, YouTube and more, checked and sent to your library.",
  cleanUrls: true,
  lastUpdated: false,
  head: [["link", { rel: "icon", href: "/docs/favicon.svg" }]],
  themeConfig: {
    logo: "/favicon.svg",
    nav: [
      { text: "Guide", link: "/guide/", activeMatch: "/guide/" },
      { text: "API", link: "/guide/api", activeMatch: "/guide/api" },
      { text: "API reference", link: "/api/", target: "_self" },
      { text: "Contributing", link: "/contributing" },
      { text: "Open songdrop", link: "/../", target: "_self" },
    ],
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
