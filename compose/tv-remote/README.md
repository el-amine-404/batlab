# tv-remote

A web remote and voice control for Samsung Tizen TVs. Served at `https://tv.homelab.lan`.

## Where things live

| File | One job |
| --- | --- |
| `app/voice.py` | Sentence to intent, in EN/FR/AR. Pure text, no I/O. |
| `app/commands.py` | What each intent does to a TV. |
| `app/tvcontrol.py` | `TvControl`, the only view of a TV that commands and the API use; `SamsungTv` implements it. |
| `app/profiles.json` | What differs per TV: inputs and their spoken names. |
| `app/samsung.py` | The TV's protocols: discovery, control socket, REST, UPnP, Wake-on-LAN. |
| `app/youtube.py`, `jellyfin.py`, `playback.py` | What plays and how to control it. |
| `app/tvstate.py` | A TV's overall state; power. |
| `app/speech.py` | Recording to words and a command; lowers the TV while listening. |
| `app/http_api.py` | Routes. Actions are listed in `ACTIONS` and `READS`. |
| `app/auth.py`, `config.py`, `store.py`, `server.py` | Login, environment, paired TVs, start-up. |
| `app/web/` | The page: `core.js` first, `main.js` last, one script per feature. |
| `speech/` | `tv-remote-speech`: Whisper on loopback. Audio stays on this machine. |

Dependencies point one way: `http_api` → `commands` → `tvcontrol` → `samsung`.

## Common changes

**Another TV.** Add an entry to `app/profiles.json`, keyed by MAC or model. Its `inputs`
are the left column of the TV's source list (Source key), and `row` counts from 0. If the
list changes, for example after plugging in a device, update `row`. Other TVs may need a
different walk: see `SamsungTv.select_input`, and check on the TV itself, because nothing
reports which input is showing.

**Another name for an input.** Add it to that input's `aliases`, lower case, without
accents or punctuation.

**Another phrase for a command.** Add it to the matching group in `_GROUPS` in `voice.py`,
with a case in `tests/test_voice.py`.

**A new kind of command.** Give it a new `type` in `voice.py`, then a function with
`@handles("type")` in `commands.py`, and add the type to `NEEDS_TV_ON` or `WAKES_THE_TV`
if it needs the TV on.

**Another make of TV.** Write a class with the `TvControl` methods and return it from
`tvcontrol.control`.

## Tests

```sh
cd compose/tv-remote
uv run --no-project --python 3.13 --with-requirements requirements.txt python -m unittest discover tests
```

`InputWalk` in `tests/test_commands.py` pins key sequences confirmed on the real TV. A
changed sequence needs checking on the TV, not just a changed test.
