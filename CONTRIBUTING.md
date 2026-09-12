# Contributing

Contributions are welcome, all of them: bug reports, fixes, new features,
documentation, typo corrections, or just telling us it didn't work on your
setup. You do not need to ask permission first.

## What this project is

A small self-hosted service, not a single standalone script: `fetch.py` (the
fetcher), a `Dockerfile`, and `docker-compose.yml.example` / `feeds.yaml.example`
that together define how it's deployed and configured. A change to any one of
these should keep the other two consistent — a new environment variable, for
instance, needs a line in `fetch.py`, a place in `docker-compose.yml.example`,
and a row in the README's configuration table.

## Reporting a bug or asking for a feature

Open an issue: [bug report](https://github.com/claude-on-call/plex-podcast-fetch/issues/new?template=bug_report.yml)
or [feature request](https://github.com/claude-on-call/plex-podcast-fetch/issues/new?template=feature_request.yml).

Please **redact your Plex token and any personal paths or URLs** before
pasting logs or config — this tool never prints your token, but a paste of
`docker compose logs` or `feeds.yaml` might reveal more than you intend.

Found a security problem? Don't open an issue — see [SECURITY.md](SECURITY.md).

## Setting up to test a change

You need a Plex server with a Music library you're happy to use as a test
"Podcasts" library — real or throwaway, but not one that matters, since
this project deletes files.

```bash
git clone https://github.com/claude-on-call/plex-podcast-fetch
cd plex-podcast-fetch
cp .env.example .env                       # your test server's token
cp feeds.yaml.example feeds.yaml           # a show or two is enough
cp docker-compose.yml.example docker-compose.yml
# edit docker-compose.yml: PLEX_URL, PLEX_SECTION, the library volume path
mkdir -p library state
docker compose up -d --build
docker compose exec fetch python /app/fetch.py run
docker compose exec fetch python /app/fetch.py list
```

For a quick syntax check without Docker:

```bash
pip install -r requirements.txt
python3 -m compileall -q .
python3 fetch.py --help
```

That catches typos, but it can't tell you whether tags come out right in
Plex or whether a feed parses the way you expect — for anything touching
`process_show()`, `tag()`, or `prune()`, please run it against a real
library before opening the PR.

## Pull requests

1. Fork the repository and branch off `main`.
2. Make the change, keeping `fetch.py` / `docker-compose.yml.example` /
   `feeds.yaml.example` / the README in sync (see "What this project is").
3. Run the syntax check above.
4. Test against a real (or test) Plex library where the change touches
   fetching, tagging, or `prune()` — see above.
5. Open the PR and describe what changed and how you tested it. What you saw
   happen in Plex (folder structure, tags, a deletion) is more convincing
   than "it runs".

The syntax/`--help` check runs automatically on every PR; the Docker-based
testing above doesn't, so it's on you and on review.

## House style

- **Small and readable.** `fetch.py` is one file on purpose — before adding a
  dependency or splitting into modules, open an issue first and we'll talk
  through whether it's worth the added surface.
- **Never delete anything you haven't verified was listened to.** `prune()`
  is the part of this tool with the most to lose if it's wrong; changes there
  deserve extra care and a written-out explanation of the new behaviour in
  the PR.
- **No dangerous defaults.** A setting whose wrong value could touch the
  wrong Plex library or delete something you didn't mean to should have no
  default at all — see `PLEX_SECTION`.
- **Docker-compose changes stay portable.** Don't reintroduce assumptions
  about one particular network setup (see the git history around
  `docker-compose.yml.example` for why) — `PLEX_URL` is how this reaches
  Plex, for anyone's setup.
- Comments should explain *why*, particularly around Plex's own quirks —
  several of the README's "Known Issues and Design Notes" exist only
  because of one.
- Python 3.9+, PEP 8, a soft line limit around 120 characters (matches the
  existing file).

## Review and merging

This repository is maintained in spare time, with AI assistance (Claude), and
a human reviews everything before it is published. Expect a first response
within a week or two. If something is urgent, say so in the issue.

By contributing, you agree that your contribution is licensed under the
repository's [MIT licence](LICENSE).
