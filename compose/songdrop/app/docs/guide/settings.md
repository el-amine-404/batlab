# Settings

Each settings screen has **Save** at the top; nothing changes until you save.
Leaving a screen with unsaved changes asks first. Saved settings apply to the
next download and are kept when songdrop restarts.

## General

| Setting | Default | What it does |
| --- | --- | --- |
| Send songs to the folder | on | Off: songs are only downloadable from History. |
| Output folder | `music/songdrop` | Where songs are copied, inside the folder mounted at `/output` (`/mnt/storage/data/inbox` in this stack). |
| Page address | `http://songdrop.homelab.lan` | Messages sent through [Connect](connections) link back to the page with it. |

The output folder can be typed, with suggestions from the folders that exist,
or chosen with the folder button, which browses the server's folders. Under the
field, songdrop says whether songs can be written there, and how much space is
free. A folder that doesn't exist yet is fine: it is made with the first song.
Save waits while the folder can't be used.

## Downloads

| Setting | Default | What it does |
| --- | --- | --- |
| Links at once | 1 | How many links download at the same time (at most 4). |
| Spotify songs at once | 3 | Songs of one Spotify link downloaded at the same time. More is faster, and more likely to be refused by YouTube. |
| Most songs per link | 200 | A longer playlist is refused. |
| Time limit per link | 180 minutes | A download still running then is stopped. |
| Keep downloads for | 24 hours | How long a download and its files stay in History. |

## Retries

When a failed song is tried again: 10 minutes, 1 hour and 6 hours after each
failure unless changed, at most 6 tries. With none, songs are tried again only
with **Retry now**.

## Spotify and Other sites

Where each download option starts, per engine ([Download options](options)).
**Defaults** puts back songdrop's own.

## YouTube

Some videos are shown only to a signed-in viewer (age-restricted ones). To
download them, give songdrop the cookies of a signed-in browser:

1. Open a private browser window and sign in to YouTube. A spare account is
   safer: YouTube may sign out cookies used for downloading.
2. Export youtube.com's cookies as `cookies.txt` (Netscape format), for example
   with the "Get cookies.txt LOCALLY" extension, then close the window.
3. In **Settings, YouTube**, upload the file.

Every download uses them, Spotify ones too. songdrop never shows them again:
the screen says only when they were saved and when they run out.

## Connect

Where songdrop's messages go: [Connect](connections).
