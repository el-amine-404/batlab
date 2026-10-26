# batlab-ui

The frame shared by batlab's web apps (subfeed, videodrop, songdrop,
clipseerr), so they look and work the same: the sidebar, the frame around the
screens, and the settings' own list. The page side of
[batlab-common](../batlab-common/README.md).

| Export | What it is |
| --- | --- |
| `Shell` | The frame: the sidebar (a drawer on a phone), the phone's bar or an app's own bar (`header`), the screen, toasts, scroll restoration; `context` reaches the screens (`useOutletContext`). |
| `Sidebar` | The app's pages with their counts, one Settings entry, the docs and API links, who is signed in, the version, and whether the page reaches the app. |
| `SettingsLayout` | The settings' list beside their page: a column on a computer, tabs that scroll on a phone. A route element with the settings as its children. |
| `useConnection` | Whether the page reaches its server (`/api/v1/health` every 30 s), for the sidebar's dot. |
| `cn` | Tailwind classes joined, the later winning. |

## Only what an app uses

Everything here is a piece an app picks, not a framework it must fill in:

- An app imports only the exports it needs; the build leaves the rest out.
- What differs is a prop, and left out it is simply not there: no `settings`,
  no Settings entry; no `links`, no docs block; no `live`, no dot (videodrop
  passes its stream's state, the others `useConnection()`); no `header`, only
  the phone's bar. An app with no settings at all doesn't use `SettingsLayout`.
- The content is the app's: its pages, counts and permissions are worked out
  in the app and handed in (`items`, `entries`), never read from here.
- A piece goes here once two apps use it the same way. What one app does on
  its own stays in that app; when an app needs something a piece can't do,
  the piece gets a prop for it rather than the app a copy of it.

## How the apps get it

Source only, nothing to install or build. Each app's Vite resolves
`@batlab/ui` to `lib/batlab-ui/src` and its own `node_modules` for React, the
router, lucide-react, sonner, clsx and tailwind-merge (`resolve.dedupe`);
TypeScript the same through `paths`, and Tailwind scans it with `@source`.
compose hands the folder to the image build as the named context `ui`, copied
to the same place relative to the app as in the repo, so the paths hold in
both.

It expects the app's Tailwind theme to have `background`, `foreground`,
`border`, `accent`, `muted-foreground`, `primary`, `destructive`, `success`
and `warning`, and a `.backdrop` class (the app's coloured background).
