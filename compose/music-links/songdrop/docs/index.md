---
title: Guide
order: 1
---

# songdrop

songdrop turns a link into audio files. Paste a Spotify, YouTube, SoundCloud
or Bandcamp link (or any other site yt-dlp supports), and songdrop downloads
the songs, checks they are the right ones, and copies them to its output
folder. In this stack, that folder is musicguard's inbox: musicguard tags each
song and adds it to Navidrome.

Everything the page does is also available through the [API](api).

## Download a link

1. Paste the link in the box on the **Downloads** screen. songdrop shows which
   site and kind of link it is, and which engine will download it.
2. Optional: open **Options** to change how this link is downloaded.
3. Press **Download**. The job appears in the list below, with its progress.

When the job ends, each song can be downloaded from the page for the time set
in [Settings](#settings) (24 hours by default), and a copy is already in the
output folder.

### Links it takes

| Link | Engine | What you get |
| --- | --- | --- |
| Spotify track, album, playlist or artist | spotDL | Each song, found on YouTube Music, with Spotify's title, artists, album, track number, date and cover. |
| YouTube, YouTube Music, SoundCloud, Bandcamp… | yt-dlp | The audio, with the site's title and thumbnail. |
| A YouTube video inside a playlist (`watch?v=…&list=…`) | yt-dlp | The video only, unless **Whole playlist** is on. |

songdrop refuses links it can't download: Spotify podcasts, `spotify.link`
short links (open them and copy the `open.spotify.com` link), and sites yt-dlp
only knows as a generic web page. A link with more songs than the limit in
Settings (200 by default) is refused rather than half done.

## Right song, every song

**Spotify downloads are checked.** spotDL picks a YouTube upload by title and
length, and a re-upload can carry the right title over other audio. Each
download is compared with Spotify's own 30-second preview. Below 70% alike, the
file is dropped and other uploads of the same length are tried, up to four,
until one matches. The job's notes say when this happened.

**Failed songs are tried again.** YouTube sometimes refuses a download (403)
for a while. A song that fails is retried automatically after 10 minutes, 1 hour
and 6 hours (change this in Settings), then songdrop gives up. Each missing
song shows when its next try is, a **Retry now** button (which also works after
songdrop gave up) and a link to the song on Spotify or the site.

## Options

Options apply to one download. Their starting values are the defaults set in
Settings.

### Spotify links (spotDL)

| Option | What it does |
| --- | --- |
| Format | Opus (YouTube Music's own stream, not converted, best), M4A or MP3 (converted). |
| Bitrate | Only for M4A and MP3. A higher number can't add quality the source doesn't have. |
| Find the audio on | YouTube Music (default), YouTube, SoundCloud or Bandcamp. |
| Artist releases | For an artist link: everything, albums, singles or compilations. |
| Filter search results | Skip matches whose title or length is too far from the Spotify song. |
| Only verified results | Official uploads only. Safer, but more songs may not be found. |
| Use YouTube Music metadata | Tags from YouTube Music instead of Spotify. |
| Cut non-music parts | Remove intros and talking marked on SponsorBlock. |
| Skip explicit songs | Leave out songs Spotify marks explicit. |

### Other links (yt-dlp)

| Option | What it does |
| --- | --- |
| Format | Original (the site's audio, not converted, best), Opus, M4A or MP3. |
| Bitrate | Only when converting. |
| Whole playlist | For a video inside a playlist: download the whole playlist. |
| How much of the playlist | Only the first 10, 25, 50 or 100 items. |
| Clip from / to | Keep part of a video, e.g. one song of a live set. Times like `1:23` or `1:02:03`; leave one empty for the start or the end. |
| One song per chapter | Split a video at its chapters (a whole album uploaded as one video). Each part is named after its chapter. Not together with a clip. |
| Cut non-music parts | Remove intros and skits marked on SponsorBlock. |
| Embed cover | Put the thumbnail in the file as its cover. |

## Settings

Open **Settings** to change how songdrop works. Changes apply at once, to the
next job; they are kept when songdrop restarts.

| Setting | Default | What it does |
| --- | --- | --- |
| Send songs to the output folder | on | Off: songs are only downloadable from the page. |
| Output folder | `music/songdrop` | Where songs are copied, inside the folder mounted at `/output` (the inbox). Made if missing. |
| Keep downloads for | 24 hours | How long a finished job and its files stay on the page. |
| Most songs per link | 200 | Longer playlists are refused. |
| Jobs at once | 1 | How many links download at the same time. |
| Spotify songs at once | 3 | How many songs of one Spotify link download at the same time. More is faster, and more likely to be refused by YouTube. |
| Time limit per job | 180 minutes | A job still running then is stopped. |
| Later tries | 10 min, 1 h, 6 h | When a failed song is tried again. Empty: never on its own; **Retry now** still works. |
| Discord webhook | none | Posts missing songs, songs a later try brought back, and songs songdrop gave up on. Write-only: once saved, it is never shown again. |
| Page address | `http://songdrop.homelab.lan` | Added to Discord messages as a link. |
| Default options | built-in | The starting value of every option, per engine. |
| YouTube cookies | none | See below. |

### YouTube cookies

Some videos are only shown to a signed-in viewer (age-restricted ones). To
download them, give songdrop the cookies of a signed-in browser:

1. Open a private browser window and sign in to YouTube. A spare account is
   safer: YouTube may sign out cookies that are used for downloading.
2. Export youtube.com's cookies as `cookies.txt` (Netscape format), for example
   with the "Get cookies.txt LOCALLY" extension, then close the window.
3. In **Settings → YouTube cookies**, upload the file.

The file is used by every download, Spotify ones too. songdrop never shows it
again: the page only says when it was saved and when it runs out.

## When something fails

| Message | What to do |
| --- | --- |
| The site refused the download (403) | Usually temporary; the later tries handle it. If every download fails, yt-dlp needs an update. |
| YouTube asked to confirm this isn't a bot | Wait, or add YouTube cookies. |
| This video is age-restricted | Add YouTube cookies, then press **Retry now**. |
| No matching song was found | The song isn't on YouTube under its name. Try another link for it. |
| This video has no chapters, so it was kept as one song | The video has no chapters to split at. |
| Downloaded, but it couldn't be copied to the output folder | Download it from the page; check the output folder in Settings. |
