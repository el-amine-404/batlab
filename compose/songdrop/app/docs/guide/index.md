# Getting started

songdrop turns a link into audio files. Paste a Spotify, YouTube, SoundCloud
or Bandcamp link (or one of any other site yt-dlp supports), and songdrop
downloads the songs, checks they are the right ones, and copies them to its
output folder. In this stack, that folder is musicguard's inbox: musicguard
tags each song and adds it to Navidrome.

## Download a link

1. Open songdrop. The first screen, **Add**, is the link box.
2. Paste the link. songdrop says which site and kind of link it is, and which
   engine will download it.
3. Optional: open **Options** to change how this link is downloaded
   ([Download options](options)).
4. Press **Download**. The download goes to the **Queue**; when it ends, it
   moves to **History** ([Queue, History, Missing](activity)).

Each song stays downloadable from History for the time set in
[Settings, Downloads](settings#downloads) (24 hours unless changed), and a copy
is already in the output folder.

## Links it takes

| Link | Engine | What you get |
| --- | --- | --- |
| Spotify track, album, playlist or artist | spotDL | Each song, found on YouTube Music, with Spotify's title, artists, album, track number, date and cover. |
| YouTube, YouTube Music, SoundCloud, Bandcamp… | yt-dlp | The audio, with the site's title and thumbnail. |
| A YouTube video inside a playlist (`watch?v=…&list=…`) | yt-dlp | The video only, unless **Whole playlist** is on. |

songdrop refuses links it can't download: Spotify podcasts, `spotify.link`
short links (open them and copy the `open.spotify.com` link), and sites yt-dlp
knows only as a generic web page. A link with more songs than the limit in
Settings (200 unless changed) is refused rather than half done.

## Right song, every song

**Spotify downloads are checked.** spotDL picks a YouTube upload by title and
length, and a re-upload can carry the right title over other audio. Each
download is compared with Spotify's own 30-second preview. Below 70% alike, the
file is dropped and other uploads of the same length are tried, up to four,
until one matches. The download's notes say when this happened.

**Failed songs are tried again.** YouTube sometimes refuses a download for a
while. A song that fails is tried again on its own after 10 minutes, 1 hour and
6 hours ([Settings, Retries](settings#retries)), then songdrop gives up. The
**Missing** screen lists every missing song, its next try, and **Retry now**.
