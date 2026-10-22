# Contributing

Issues and pull requests are welcome.

- **Backend**: Python 3.12+, FastAPI, SQLite. `pytest backend/tests` must pass.
- **Interface**: React, TypeScript, Tailwind and shadcn/ui. `npm run build`
  must pass (it type-checks). Keep the look consistent: shadcn components, one
  font, no extra colors for the same meaning.
- **Worker**: changes to the pipeline should come with a measurement on songs a
  person has synced (see `docs/worker.md`), not only an impression.
- Lyrics are copyrighted: never commit lyrics text or audio in tests or docs.
  Tests generate tones with ffmpeg and use made-up lines.

Commits follow [Conventional Commits](https://www.conventionalcommits.org).
