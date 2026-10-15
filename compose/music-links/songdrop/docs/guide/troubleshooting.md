# When something fails

| Message | What to do |
| --- | --- |
| The site refused the download (403) | Usually temporary; the later tries handle it. If every download fails, yt-dlp needs an update. |
| YouTube asked to confirm this isn't a bot | Wait, or add [YouTube cookies](settings#youtube). |
| This video is age-restricted | Add [YouTube cookies](settings#youtube), then press **Retry now** on the Missing screen. |
| No matching song was found | The song isn't on YouTube under its name. Try another link for it. |
| This video has no chapters, so it was kept as one song | The video has no chapters to split at. |
| Downloaded, but it couldn't be copied to the output folder | Download it from History; check the output folder in [Settings, General](settings#general). |
| A connection's card says its last message failed | Open it and press **Test**: the message says what the other side answered. |

**System, Status** shows the versions of yt-dlp, spotDL and ffmpeg, and the
space left in the output folder.
