---
layout: home

hero:
  name: songdrop
  text: Paste a link, get the songs.
  tagline: Spotify, YouTube, SoundCloud, Bandcamp and the other sites yt-dlp knows. Each song checked, missing ones tried again, all of it sent to your library.
  image:
    src: /favicon.svg
    alt: songdrop
  actions:
    - theme: brand
      text: Get started
      link: /guide/
    - theme: alt
      text: Use the API
      link: /guide/api
    - theme: alt
      text: API reference
      link: /api/
      target: _self

features:
  - icon: 🎯
    title: The right song
    details: A Spotify song found on YouTube is compared with Spotify's own preview. A wrong upload is dropped and another one tried.
    link: /guide/#right-song-every-song
  - icon: 🔁
    title: Every song
    details: A song that fails is tried again later, on its own. The Missing screen shows what is still missing and when it is tried next.
    link: /guide/activity
  - icon: 📂
    title: Into your library
    details: Finished songs go to the output folder you choose. In this stack, musicguard takes them from there into Navidrome.
    link: /guide/settings#general
  - icon: 🔔
    title: Tells you
    details: Discord today, any app through a webhook. Choose which events each connection gets.
    link: /guide/connections
  - icon: 🧩
    title: An API first
    details: The page is a client of a versioned API, described in OpenAPI. Any app can start downloads and be called back.
    link: /guide/api
  - icon: 🛠️
    title: Easy to extend
    details: A new connection type is one backend file; a new screen is one page and one line.
    link: /contributing
---
