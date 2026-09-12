# plex-podcast-fetch

Downloads podcasts into a Plex **Music** library (used as your "Podcasts"
library) so they play properly in Plexamp — correct artist/album/track tags,
artwork, and episodes ordered by publish date — and asks Plex to scan only
the show folder that changed. No full library rescans, ever.

Why not Plex's own podcast support? Plex removed it. This works around that
by treating each show as an "artist" and each episode as a "track" in an
ordinary Music library, which Plexamp already handles well.

## Quick start

```bash
git clone https://github.com/claude-on-call/plex-podcast-fetch
cd plex-podcast-fetch
cp .env.example .env               # add your Plex token
cp feeds.yaml.example feeds.yaml   # pick your shows
cp docker-compose.yml.example docker-compose.yml
```

Edit `docker-compose.yml`: set `PLEX_URL`, `PLEX_SECTION` (your Podcasts
library's section id — it's in the library's URL in Plex Web,
`.../library/sections/<id>/...`), and point the `./library` volume at your
actual Plex library folder. Then:

```bash
mkdir -p library state
docker compose up -d
```

The container fetches once immediately, then on `interval_hours` (default 2,
set in `feeds.yaml`).

## Day to day

| Want to… | Do |
|---|---|
| Add a show | Add `- url: <feed>` to `feeds.yaml` (set `name:` now if you want one), then `docker compose exec fetch python /app/fetch.py run` |
| Fetch now | `docker compose exec fetch python /app/fetch.py run` |
| See status | `docker compose exec fetch python /app/fetch.py list` |
| Mark listened | `docker compose exec fetch python /app/fetch.py played "Show Name" --before 2026-09-01` (or `--all`) |
| Mark unlistened | same with `unplayed` |
| Logs | `docker compose logs -f fetch` |
| Remove a show | Delete it from `feeds.yaml`. Its files stay until you delete the show's folder from your library and scan the library root in Plex. |
| Change the code | Edit `fetch.py`, then `docker compose up -d --build` |

Checks use a conditional GET, so a feed with nothing new costs one 304
response.

## How it works

1. Poll each feed. Episodes are identified by `<guid>`, and anything seen
   before is skipped. State is in `state/podcasts.sqlite`.
2. Download to `<library>/.staging/` and check the file. The feed's own
   `<enclosure length>` isn't trusted (some publishers' is fake — the BBC's
   is identical on every episode), so the download is checked against the
   HTTP `Content-Length` and mutagen reads the duration.
3. Replace the publisher's tags: artist = album artist = album = show name,
   title = episode, track # = a running number per show (stays stable when
   old episodes are deleted), date, genre `Podcast`, description as the
   comment, show artwork embedded. The folder also gets `cover.jpg` and
   `artist.jpg`.
4. Rename into `<library>/<Show>/YYYY-MM-DD - Title.mp3`. It's one
   filesystem, so the rename is atomic.
5. Delete **listened** episodes that are `grace_days` past their play, or
   that have `keep` newer episodes. Play state is read from Plex each run
   (whichever account `PLEX_TOKEN` belongs to). If Plex can't be reached,
   nothing is deleted that run.
6. Send Plex one scan per changed show: a `refresh` with `path=` set to that
   show's folder only. If Plex is down, the scan is retried next run.
7. On the next run, once Plex has the episode, set its Plex **"date added"
   to its publish date** (locked). Plex keeps no per-track publish date, and
   otherwise "date added" would be download order, not publish order.

"Listened" is Plex's play count (`viewCount`). Plexamp adds one when an
episode finishes. Turn on the library's **Store Track Progress** setting too,
so it also remembers where you stopped partway through an episode.

### An "unplayed, oldest first" queue

Create a smart playlist on the Podcasts library: `track.viewCount = 0`,
sort by `addedAt` ascending. Press play and it starts with the oldest
episode you haven't finished, across every show, in publish order (thanks to
step 7 above) — new episodes join at the bottom, and a finished one drops out
by itself. A half-finished episode stays in.

## Configuration

Environment variables (set in `docker-compose.yml`):

| Variable | Required | Description |
|---|---|---|
| `PLEX_TOKEN` | Yes | Plex token — [finding yours](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). Whichever account this belongs to is whose play state is used for deletion. |
| `PLEX_URL` | No | Plex server URL. Default `http://plex:32400` (a Docker service named `plex`). |
| `PLEX_SECTION` | **Yes** | Your Podcasts library's section id. No safe default — every server's is different, and guessing wrong could touch the wrong library. |
| `PLEX_PATH` | No | The library folder's path *as Plex itself sees it*, if different from where this container mounts it (default `/data/podcasts`). |
| `PUSH_URL` | No | Push-monitor URL (Uptime Kuma, healthchecks.io), pinged after a run that fetched successfully. |
| `LIB_DIR` | No | Where this container mounts the library (default `/podcasts`). |
| `TZ` | No | Timezone, for episode dates. |

`feeds.yaml` — shows and their per-show/default retention settings — is
documented in `feeds.yaml.example`.

### Finding a show's feed URL

Most podcast apps have a "Share" or "Copy RSS link" option on a show's page
— check there first. If not, the iTunes Search API returns a `feedUrl` for
each match, no account needed:

```
https://itunes.apple.com/search?media=podcast&entity=podcast&term=<show name>
```

A show's own website often links its feed directly too, usually behind an
RSS icon or an "Subscribe" / "For other apps" link.

### Monitoring

Set `PUSH_URL` and, after any run that fetched at least one show
successfully, this pings it with a plain `GET` (`?status=up&msg=<N>/<total>
feeds ok`). That's a generic push-monitor ping, so it works with:

- **[Uptime Kuma](https://github.com/louislam/uptime-kuma):** add a **Push**
  monitor, copy the URL it gives you, set that as `PUSH_URL`.
- **[healthchecks.io](https://healthchecks.io):** create a check, copy its
  ping URL, set that as `PUSH_URL`.
- **Anything else that accepts a plain GET ping** — cron-monitoring services,
  a webhook relay, or an endpoint of your own — point `PUSH_URL` at it. The
  query parameters are extra information for services that use them
  (Kuma does; most others just ignore them) and are harmless either way.

Leave `PUSH_URL` unset to skip this entirely.

## Known Issues and Design Notes

- **Staging must be inside the library mount.** A separate bind mount makes
  `rename()` fail with `EXDEV` even on the same disk. A `.plexignore` in the
  library root (`.staging/*`, `*.part`) keeps Plex from scanning it.
- **If a library was created while its folder was empty**, Plex's location
  row for it can end up with `available=NULL`, and every scan returns
  instantly with "Refreshing 0 IDs" — no scan actually happens. Fixed by
  re-saving the library's location through Plex Web's "Edit library" (or the
  equivalent API call). If a library does nothing when scanned, check this
  first.
- **Turn OFF "Scan my library automatically"** on the Podcasts library. It's
  a global Plex setting that watches every library for filesystem changes,
  which fights with this doing its own targeted, one-folder-at-a-time scans.
- **Bind-mount the feeds file's directory, not the file itself.** A
  single-file bind pins the file's inode; an editor that saves by writing a
  new file (many do) leaves the container reading the OLD version. Mount the
  directory containing `feeds.yaml` and set `FEEDS_FILE` to the path inside
  it, or use a read-only bind of the file and always edit in place (`>>`,
  never save-as-new-file).
- **Adding shows right after `docker compose up -d`:** the loop runs once
  immediately on start, so an `exec … run` straight after start may say
  "another run is in progress; skipping" — harmless, the loop's own run
  picks up the new show. Watch `docker compose logs -f fetch`.
- **`PLEX_SECTION` has no default on purpose.** Every Plex server's library
  section ids are different; a default that happens to be right on one
  install is very likely wrong on the next one.

## Not done yet

OPML import. Delete-after-played retention as an alternative policy.
`yt-dlp` support for YouTube-only shows.

## Contributing, issues and security

- Bugs, ideas and pull requests are all welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
- Security problems: please report privately first, see [SECURITY.md](SECURITY.md).

This repository is maintained with AI assistance (Claude), and a human reviews
every change before it is published.

MIT licensed — see [LICENSE](LICENSE).
