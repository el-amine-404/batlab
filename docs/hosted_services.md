# Hosted Services

This document provides a summary of the self-hosted services configured in this homelab, along with links to their official documentation.

| Service          | Explanation                                                                                              | Official Documentation                                                   |
| :--------------- | :------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------- |
| **Caddy**        | A powerful, enterprise-ready open-source web server with automatic HTTPS certificates.                   | [caddyserver.com](https://caddyserver.com)                               |
| **AdGuard Home** | A network-wide DNS server for blocking ads/tracking and managing local domain resolution.                | [adguard.com/adguard-home.html](https://adguard.com/adguard-home.html)   |
| **Unbound**      | A validating, recursive, caching DNS resolver designed for high privacy and security.                    | [nlnetlabs.nl/projects/unbound/](https://nlnetlabs.nl/projects/unbound/) |
| **Homepage**     | A highly customizable, modern application dashboard with real-time docker container metrics.             | [gethomepage.dev](https://gethomepage.dev)                               |
| **Jellyfin**     | An open-source media system that lets you stream your movies, shows, and music to any device.            | [jellyfin.org](https://jellyfin.org)                                     |
| **Dispatcharr**  | An IPTV stream manager that feeds the iptv-org channels to Jellyfin Live TV, through the VPN.            | [dispatcharr.github.io](https://dispatcharr.github.io/Dispatcharr-Docs/) |
| **NodeCast TV**  | A web player for live TV on PCs and phones, fed by Dispatcharr's Xtream Codes API.                       | [github.com/technomancer702/nodecast-tv](https://github.com/technomancer702/nodecast-tv) |
| **qBittorrent**  | A lightweight, open-source BitTorrent download client with a clean web interface.                        | [qbittorrent.org](https://www.qbittorrent.org)                           |
| **Sonarr**       | A TV show collection manager that automates downloading and organizing Usenet and BitTorrent files.      | [sonarr.tv](https://sonarr.tv)                                           |
| **Radarr**       | A movie collection manager that automates downloading and organizing Usenet and BitTorrent files.        | [radarr.video](https://radarr.video)                                     |
| **Lidarr**       | A music collection manager that automates downloading and organizing Usenet and BitTorrent files.        | [lidarr.audio](https://lidarr.audio)                                     |
| **Aurral**       | Music requests and discovery, the Seerr of the music stack; also imports Spotify and YouTube Music playlists. | [docs.aurral.org](https://docs.aurral.org)                               |
| **Feishin**      | A better player for Navidrome in the browser (synced lyrics, queue, playlists); logs in with each user's Navidrome account. | [github.com/jeffvli/feishin](https://github.com/jeffvli/feishin)          |
| **Stanzarr**     | Lyrics for the music libraries: tracks every song's lyrics, fetches them from LRCLIB, AI sync on a GPU worker, lyrics editor. | private project (`~/stanzarr`) |
| **slskd**        | A Soulseek client with a web UI, run inside the VPN, for albums torrent sites do not carry.                | [github.com/slskd/slskd](https://github.com/slskd/slskd)                 |
| **Soularr**      | Hands Lidarr's missing albums to slskd and has Lidarr import what it finds.                                | [github.com/mrusse/soularr](https://github.com/mrusse/soularr)           |
| **songdrop**     | One page for pasted links: Spotify through spotDL, anything else yt-dlp knows; songs go to the music inbox.  | `compose/music-links/songdrop` (built here)                                |
| **clipseerr**    | The newest music videos to browse and watch; ask for the song (Aurral, else songdrop) or the video (metube-videos, into Jellyfin). | `compose/clipseerr` (built here)                                         |
| **MeTube**       | A web UI for yt-dlp: paste a YouTube link, the song lands in the music inbox.                              | [github.com/alexta69/metube](https://github.com/alexta69/metube)         |
| **musicguard**   | (custom) Checks every music file, keeps only audio and clean tags, adds lyrics, quarantines the rest.     | [docs/music.md](music.md)                                                |
| **Readarr**      | An ebook and audiobook manager that automates downloading and organizing Usenet and BitTorrent files.    | [readarr.com](https://readarr.com)                                       |
| **Prowlarr**     | An indexer manager and proxy that syncs torrent track and Usenet indexers with the Arr stack.            | [prowlarr.com](https://prowlarr.com)                                     |
| **Bazarr**       | A companion application that automatically searches, downloads, and manages subtitles for Sonarr/Radarr. | [bazarr.media](https://www.bazarr.media)                                 |
| **Recyclarr**    | A command-line tool that syncs custom quality profiles and formats from TRaSH Guides to Sonarr/Radarr.   | [recyclarr.dev](https://recyclarr.dev)                                   |
| **Frigate**      | A camera NVR in front of the Hikvision DVR: live view of the home cameras in the browser ([cameras](cameras.md)). | [frigate.video](https://frigate.video)                                   |
| **Uptime Kuma**  | A self-hosted status page and uptime monitoring tool with multi-channel alerting.                        | [uptime.kuma.pet](https://uptime.kuma.pet)                               |
| **Portainer**    | A lightweight management console for creating, updating, and viewing Docker containers.                  | [portainer.io](https://www.portainer.io)                                 |
| **Netdata**      | A real-time system monitoring agent for tracking CPU, memory, and container health metrics.              | [netdata.cloud](https://www.netdata.cloud)                               |
| **Gluetun**      | A lightweight, containerized VPN client for routing other container traffic securely through a VPN.      | [github.com/qdm12/gluetun](https://github.com/qdm12/gluetun)             |
| **Dozzle**       | A simple, real-time web-based log viewer for tracking active Docker container logs.                      | [dozzle.dev](https://dozzle.dev)                                         |
| **Gotenberg**    | A developer-friendly API for converting HTML, Markdown, and Office documents into PDFs.                  | [gotenberg.dev](https://gotenberg.dev)                                   |
| **File Browser** | A lightweight utility providing a web-based file management interface for your directories.              | [filebrowser.org](https://filebrowser.org)                               |
| **Ollama**       | A local model runtime used by private AI services without sending prompts to a hosted model provider.     | [ollama.com](https://ollama.com)                                         |
| **Open WebUI**   | A private, multi-user chat interface connected to the local Ollama runtime.                               | [docs.openwebui.com](https://docs.openwebui.com)                         |
