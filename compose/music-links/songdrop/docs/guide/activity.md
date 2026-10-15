# Queue, History, Missing

The **Activity** screens show what songdrop is doing and has done. The sidebar
counts what is in the Queue and how many songs are missing.

## Queue

Downloads running now, with their progress and the song being downloaded, and
the ones waiting for their turn ([Links at once](settings#downloads)).
**Cancel** stops one; the songs it already finished are kept.

## History

Downloads that ended: **Complete**, with **Songs missing**, **Failed** or
**Cancelled**. Open one to see its songs, download them one by one or all at
once (**Download all**, a zip), and read its notes. **Remove** takes a download
off the list; copies already in the output folder stay. **Clear complete**
removes every complete or cancelled download at once.

A download leaves History on its own after the time set in
[Settings, Downloads](settings#downloads), unless one of its songs is still
waiting for a try.

## Missing

Every song a download couldn't get, across all downloads, with the reason and
its state:

| State | Meaning |
| --- | --- |
| Next try in … | Waiting for its next try ([Retries](settings#retries)). |
| Trying again now | In a try. |
| No more tries on its own | Its last try failed. **Retry now** still works. |
| (no button) | It has no link of its own, so it can't be tried alone. |

**Retry now** tries a song at once, whatever its state. A song that comes back
moves to its download's songs and goes to the output folder.
