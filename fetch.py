#!/usr/bin/env python3
"""Podcast fetcher for a Plex music library used as "Podcasts".

Each run: poll the feeds in feeds.yaml, download new episodes into a hidden
staging dir inside the library mount, tag them for a Plex Music library
(artist = album = show name), rename them into <library>/<Show>/, prune old
ones, then ask Plex to scan ONLY the show folders that changed. No full
library rescans, ever.

See README.md for setup and design notes.
"""
import argparse
import calendar
import datetime as dt
import fcntl
import hashlib
import html
import logging
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse

import feedparser
import mutagen
import requests
import yaml
from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

LIB = os.environ.get("LIB_DIR", "/podcasts")
# Staging lives INSIDE the library mount: rename() across two bind mounts
# fails with EXDEV even on the same disk. .plexignore keeps Plex out of it.
STAGING = os.path.join(LIB, ".staging")
STATE_DB = os.environ.get("STATE_DB", "/state/podcasts.sqlite")
LOCK_FILE = os.environ.get("LOCK_FILE", "/state/.lock")
FEEDS_FILE = os.environ.get("FEEDS_FILE", "/config/feeds.yaml")
PLEX_URL = os.environ.get("PLEX_URL", "http://plex:32400").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
PLEX_SECTION = os.environ.get("PLEX_SECTION", "")
PLEX_PATH = os.environ.get("PLEX_PATH", "/data/podcasts")  # LIB as the Plex container sees it
PUSH_URL = os.environ.get("PUSH_URL", "")  # optional Uptime Kuma / healthchecks.io push monitor
MAX_NEW_PER_RUN = 10  # per show; guards against a feed re-issuing its whole back catalogue
MIN_SECONDS = 30      # anything shorter isn't an episode (error jingle, empty file)

log = logging.getLogger("podcasts")
http = requests.Session()
http.headers["User-Agent"] = "plex-podcast-fetch/1.0 (self-hosted, personal use)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
  url TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  etag TEXT,
  modified TEXT,
  image_url TEXT,
  next_track INTEGER NOT NULL DEFAULT 1,
  pending_scan INTEGER NOT NULL DEFAULT 0,
  last_checked TEXT,
  last_error TEXT
);
-- status: downloaded | skipped (older than the backfill on add, or over the per-run cap) | pruned
CREATE TABLE IF NOT EXISTS episodes (
  show_url TEXT NOT NULL,
  guid TEXT NOT NULL,
  title TEXT,
  published TEXT,
  track INTEGER,
  file TEXT,
  status TEXT NOT NULL,
  added TEXT,
  PRIMARY KEY (show_url, guid)
);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def clean_name(s, limit=150):
    """Filesystem-safe, still readable: 'Tech Life: AI?' -> 'Tech Life - AI-'."""
    s = (s or "").replace(":", " -")
    s = re.sub(r'[\x00-\x1f/\\*?"<>|]', "-", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip(".")
    return s[:limit].rstrip(". ") or "untitled"


def strip_html(s, limit=1000):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def mime_of(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def open_db():
    db = sqlite3.connect(STATE_DB)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if "plex_dated" not in {r[1] for r in db.execute("PRAGMA table_info(episodes)")}:
        db.execute("ALTER TABLE episodes ADD COLUMN plex_dated INTEGER NOT NULL DEFAULT 0")
    return db


def load_cfg():
    with open(FEEDS_FILE) as f:
        cfg = yaml.safe_load(f) or {}
    cfg["defaults"] = cfg.get("defaults") or {}
    cfg["shows"] = [s for s in (cfg.get("shows") or []) if s and s.get("url")]
    return cfg


def plex(endpoint, method="GET", **params):
    params["X-Plex-Token"] = PLEX_TOKEN
    # quote_via=quote: spaces as %20, not '+', so folder paths reach Plex intact
    url = f"{PLEX_URL}{endpoint}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
    r = http.request(method, url, headers={"Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json() if "json" in r.headers.get("Content-Type", "") else None


def date_episodes(db):
    """Set each episode's Plex "date added" to its publish date (locked).

    Plex keeps no per-track publish date, so an "unplayed, oldest first"
    smart playlist would otherwise follow download order, which is wrong
    whenever several episodes arrive in one run. An episode is picked up
    here on the run AFTER its download, once Plex's scan has added it;
    until then its date added is the download time, which is close anyway.
    """
    todo = db.execute("SELECT show_url, guid, file, published FROM episodes "
                      "WHERE status='downloaded' AND plex_dated=0").fetchall()
    if not todo:
        return
    tracks = (plex(f"/library/sections/{PLEX_SECTION}/all", type="10") or {}).get("MediaContainer", {}).get("Metadata", [])
    keys = {p.get("file"): t["ratingKey"] for t in tracks for m in t.get("Media", []) for p in m.get("Part", [])}
    done = 0
    for ep in todo:
        key = keys.get(f"{PLEX_PATH}/{ep['file']}")
        if not key:
            continue  # not scanned in yet; next run
        added = int(dt.datetime.fromisoformat(ep["published"]).timestamp())
        plex(f"/library/sections/{PLEX_SECTION}/all", "PUT", type="10", id=key,
             **{"addedAt.value": added, "addedAt.locked": 1})
        db.execute("UPDATE episodes SET plex_dated=1 WHERE show_url=? AND guid=?", (ep["show_url"], ep["guid"]))
        done += 1
    db.commit()
    if done:
        log.info("set Plex 'date added' = publish date on %d episode(s)", done)


def episodes_from(feed):
    """Audio episodes in the feed, oldest first."""
    out = []
    for e in feed.entries:
        enc = next((l for l in e.get("enclosures", [])
                    if l.get("href") and ((l.get("type") or "").startswith("audio")
                                          or re.search(r"\.(mp3|m4a|ogg|opus)(\?|$)", l["href"], re.I))),
                   None)
        if not enc:
            continue
        tp = e.get("published_parsed") or e.get("updated_parsed")
        date = dt.datetime.fromtimestamp(calendar.timegm(tp), dt.timezone.utc) if tp else dt.datetime.now(dt.timezone.utc)
        out.append({
            "guid": e.get("id") or enc["href"],
            "url": enc["href"],
            "title": (e.get("title") or "Untitled").strip(),
            "date": date,
            "desc": strip_html(e.get("summary") or e.get("description")),
        })
    out.sort(key=lambda x: x["date"])
    return out


def download(url, dest):
    with http.get(url, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        expected = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                got += len(chunk)
    # Feeds' own <enclosure length> is often fake (BBC: identical on every
    # episode), so check against what the server actually sent instead.
    if expected and not r.headers.get("Content-Encoding") and got != expected:
        raise IOError(f"short download: {got} of {expected} bytes")


def ensure_cover(show_dir, image_url, known_url):
    """Show artwork as cover.* (album poster) + artist.* (artist poster).
    Returns (bytes, mime) to embed, or None."""
    existing = [f for f in os.listdir(show_dir) if f.split(".")[0] in ("cover", "artist")]
    cover = next((f for f in existing if f.startswith("cover.")), None)
    if cover and known_url == image_url:
        with open(os.path.join(show_dir, cover), "rb") as f:
            data = f.read()
        return data, mime_of(data)
    r = http.get(image_url, timeout=(15, 60))
    r.raise_for_status()
    mime = mime_of(r.content)
    if not mime:
        raise ValueError(f"artwork at {image_url} is not a JPEG or PNG")
    for f in existing:
        os.remove(os.path.join(show_dir, f))
    ext = ".png" if mime == "image/png" else ".jpg"
    for stem in ("cover", "artist"):
        with open(os.path.join(show_dir, stem + ext), "wb") as f:
            f.write(r.content)
    return r.content, mime


def tag(path, audio, show, ep, track, cover):
    """Replace whatever tags the publisher shipped with ours. Returns the extension."""
    date = ep["date"].astimezone().strftime("%Y-%m-%d")
    if isinstance(audio, MP3):
        try:
            ID3(path).delete()
        except ID3NoHeaderError:
            pass
        t = ID3()
        t.add(TIT2(encoding=3, text=ep["title"]))
        t.add(TPE1(encoding=3, text=show))
        t.add(TPE2(encoding=3, text=show))
        t.add(TALB(encoding=3, text=show))
        t.add(TRCK(encoding=3, text=str(track)))
        t.add(TDRC(encoding=3, text=date))
        t.add(TCON(encoding=3, text="Podcast"))
        t.add(COMM(encoding=3, lang="eng", desc="", text=ep["desc"]))
        if cover:
            t.add(APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0]))
        t.save(path, v1=0, v2_version=3)
        return ".mp3"
    if isinstance(audio, MP4):
        if audio.tags is None:
            audio.add_tags()
        audio.tags.clear()
        audio.tags.update({"\xa9nam": [ep["title"]], "\xa9ART": [show], "aART": [show], "\xa9alb": [show],
                           "trkn": [(track, 0)], "\xa9day": [date], "\xa9gen": ["Podcast"], "\xa9cmt": [ep["desc"]]})
        if cover:
            fmt = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
            audio.tags["covr"] = [MP4Cover(cover[0], imageformat=fmt)]
        audio.save()
        return ".m4a"
    if isinstance(audio, (OggOpus, OggVorbis)):
        # no embedded art for Ogg; Plex uses the folder's cover.*
        audio.tags.clear()
        for k, v in {"title": ep["title"], "artist": show, "albumartist": show, "album": show,
                     "tracknumber": str(track), "date": date, "genre": "Podcast", "comment": ep["desc"]}.items():
            audio.tags[k] = [v]
        audio.save()
        return ".opus" if isinstance(audio, OggOpus) else ".ogg"
    raise ValueError(f"unsupported audio format: {type(audio).__name__}")


def save_episode(ep, show_dir, show, track, cover):
    """Download -> verify -> tag in staging, then one atomic rename into the show folder."""
    os.makedirs(STAGING, exist_ok=True)
    part = os.path.join(STAGING, hashlib.sha1(ep["guid"].encode()).hexdigest() + ".part")
    try:
        download(ep["url"], part)
        audio = mutagen.File(part)
        if audio is None or audio.info.length < MIN_SECONDS:
            raise ValueError(f"not audio, or shorter than {MIN_SECONDS}s")
        ext = tag(part, audio, show, ep, track, cover)
        base = clean_name(f"{ep['date'].astimezone():%Y-%m-%d} - {ep['title']}")
        dest, n = os.path.join(show_dir, base + ext), 2
        while os.path.exists(dest):
            dest, n = os.path.join(show_dir, f"{base} ({n}){ext}"), n + 1
        os.rename(part, dest)  # same mount: atomic, Plex never sees a partial file
        return os.path.basename(dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def listened():
    """{Plex file path: when it was last finished} for played podcast episodes.

    Play state is per Plex account — whichever account PLEX_TOKEN belongs to.
    """
    tracks = (plex(f"/library/sections/{PLEX_SECTION}/all", type="10") or {}).get("MediaContainer", {}).get("Metadata", [])
    return {p.get("file"): t.get("lastViewedAt") or time.time()
            for t in tracks if t.get("viewCount") for m in t.get("Media", []) for p in m.get("Part", [])}


def prune(db, url, name, keep, grace_days, plays):
    """Only ever delete episodes you've listened to. A listened episode goes
    once `keep` newer episodes of the show have landed, or `grace_days` after
    you listened, whichever comes first. Unlistened and half-played episodes
    are never deleted. plays=None (Plex unreachable) -> delete nothing."""
    if plays is None:
        return
    landed = [r[0] for r in db.execute("SELECT published FROM episodes WHERE show_url=? "
                                       "AND status IN ('downloaded', 'pruned')", (url,))]
    for ep in db.execute("SELECT guid, file, published FROM episodes WHERE show_url=? AND status='downloaded'",
                         (url,)).fetchall():
        heard_at = plays.get(f"{PLEX_PATH}/{ep['file']}")
        if heard_at is None:
            continue
        newer = sum(1 for p in landed if p > ep["published"])
        days = (time.time() - heard_at) / 86400
        if newer < keep and days < grace_days:
            continue
        path = os.path.join(LIB, ep["file"])
        if os.path.exists(path):
            os.remove(path)
        db.execute("UPDATE episodes SET status='pruned' WHERE show_url=? AND guid=?", (url, ep["guid"]))
        db.execute("UPDATE shows SET pending_scan=1 WHERE url=?", (url,))
        log.info("%s: - %s (listened %d days ago, %d newer episodes)", name, ep["file"], days, newer)


def process_show(db, cfg, defaults, plays):
    url = cfg["url"]
    keep = int(cfg.get("keep", defaults.get("keep", 20)))
    grace_days = float(cfg.get("grace_days", defaults.get("grace_days", 30)))
    backfill = int(cfg.get("backfill", defaults.get("backfill", 3)))
    row = db.execute("SELECT * FROM shows WHERE url=?", (url,)).fetchone()

    headers = {}
    if row and row["etag"]:
        headers["If-None-Match"] = row["etag"]
    if row and row["modified"]:
        headers["If-Modified-Since"] = row["modified"]
    r = http.get(url, headers=headers, timeout=(15, 60))
    if r.status_code == 304:
        log.info("%s: feed unchanged", row["name"])
        db.execute("UPDATE shows SET last_checked=?, last_error=NULL WHERE url=?", (now(), url))
        db.commit()
        return
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise ValueError(f"no entries in feed ({feed.get('bozo_exception') or 'empty'})")

    name = cfg.get("name") or (row["name"] if row else None) or feed.feed.get("title") or url
    first_add = row is None
    if first_add:
        db.execute("INSERT INTO shows (url, name) VALUES (?, ?)", (url, name))
    else:
        db.execute("UPDATE shows SET name=? WHERE url=?", (name, url))
    row = db.execute("SELECT * FROM shows WHERE url=?", (url,)).fetchone()

    known = {g for (g,) in db.execute("SELECT guid FROM episodes WHERE show_url=?", (url,))}
    new = [e for e in episodes_from(feed) if e["guid"] not in known]
    cap = backfill if first_add else MAX_NEW_PER_RUN
    skip, new = (new[:-cap], new[-cap:]) if cap > 0 else (new, [])
    if skip and not first_add:
        log.warning("%s: %d new episodes, over the per-run cap; skipping the %d oldest", name, len(skip) + len(new), len(skip))
    db.executemany("INSERT INTO episodes (show_url, guid, title, published, status, added) VALUES (?,?,?,?,'skipped',?)",
                   [(url, e["guid"], e["title"], e["date"].isoformat(), now()) for e in skip])
    if first_add:
        log.info("%s: new show, %d episodes in feed; fetching the latest %d", name, len(skip) + len(new), len(new))

    folder = clean_name(name)
    show_dir = os.path.join(LIB, folder)
    os.makedirs(show_dir, exist_ok=True)
    cover = None
    image_url = (feed.feed.get("image") or {}).get("href")
    if image_url:
        try:
            cover = ensure_cover(show_dir, image_url, row["image_url"])
            db.execute("UPDATE shows SET image_url=? WHERE url=?", (image_url, url))
        except Exception as ex:
            log.warning("%s: artwork failed, continuing without it: %s", name, ex)

    failed = False
    track = row["next_track"]
    for ep in new:  # oldest first, so track numbers follow publish order
        try:
            fname = save_episode(ep, show_dir, name, track, cover)
        except Exception as ex:
            # stop here so a retry next run keeps episodes in publish order
            log.error("%s: '%s' failed, will retry next run: %s", name, ep["title"], ex)
            failed = True
            break
        db.execute("INSERT INTO episodes (show_url, guid, title, published, track, file, status, added) "
                   "VALUES (?,?,?,?,?,?,'downloaded',?)",
                   (url, ep["guid"], ep["title"], ep["date"].isoformat(), track, f"{folder}/{fname}", now()))
        track += 1
        db.execute("UPDATE shows SET next_track=?, pending_scan=1 WHERE url=?", (track, url))
        db.commit()
        log.info("%s: + %s", name, fname)

    prune(db, url, name, keep, grace_days, plays)

    # Only trust the feed's ETag once everything in it is on disk; after a
    # failure, refetch in full next run so the failed episode is retried.
    db.execute("UPDATE shows SET etag=?, modified=?, last_checked=?, last_error=? WHERE url=?",
               (None if failed else r.headers.get("ETag"), None if failed else r.headers.get("Last-Modified"),
                now(), "episode download failed" if failed else None, url))
    db.commit()


def scan_pending(db):
    """One partial scan per changed show folder -- never the whole section."""
    for row in db.execute("SELECT url, name FROM shows WHERE pending_scan=1").fetchall():
        path = f"{PLEX_PATH}/{clean_name(row['name'])}"
        try:
            plex(f"/library/sections/{PLEX_SECTION}/refresh", path=path)
        except requests.RequestException as ex:
            log.error("Plex scan of %s failed, will retry next run: %s", path, ex)
            continue
        db.execute("UPDATE shows SET pending_scan=0 WHERE url=?", (row["url"],))
        db.commit()
        log.info("asked Plex to scan %s only", path)


def run():
    cfg = load_cfg()
    db = open_db()
    try:
        date_episodes(db)
    except requests.RequestException as ex:
        log.warning("couldn't set publish dates in Plex, will retry next run: %s", ex)
    try:
        plays = listened()
    except requests.RequestException as ex:
        plays = None
        log.warning("couldn't read play state from Plex, deleting nothing this run: %s", ex)
    ok = 0
    for show in cfg["shows"]:
        try:
            process_show(db, show, cfg["defaults"], plays)
            ok += 1
        except Exception as ex:
            log.error("%s: %s", show["url"], ex)
            db.execute("UPDATE shows SET last_checked=?, last_error=? WHERE url=?", (now(), str(ex)[:500], show["url"]))
            db.commit()
    scan_pending(db)
    if PUSH_URL and ok:
        try:
            http.get(PUSH_URL, params={"status": "up", "msg": f"{ok}/{len(cfg['shows'])} feeds ok"}, timeout=15)
        except requests.RequestException as ex:
            log.warning("push monitor ping failed: %s", ex)


def locked(fn):
    """`docker compose exec fetch ... run` can overlap the loop; only one run at a time."""
    with open(LOCK_FILE, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another run is in progress; skipping")
            return
        fn()


def cmd_loop():
    while True:
        locked(run)
        hours = float(load_cfg()["defaults"].get("interval_hours", 2))
        time.sleep(hours * 3600 + random.uniform(-600, 600))


def cmd_list():
    db = open_db()
    for s in db.execute("SELECT * FROM shows ORDER BY name"):
        n, newest = db.execute("SELECT count(*), max(published) FROM episodes WHERE show_url=? AND status='downloaded'",
                               (s["url"],)).fetchone()
        line = f"{s['name']}: {n} on disk, newest {(newest or '-')[:10]}, checked {s['last_checked'] or 'never'}"
        if s["pending_scan"]:
            line += " [Plex scan pending]"
        if s["last_error"]:
            line += f"\n    ERROR: {s['last_error']}"
        print(line)


def cmd_mark(show, played, before):
    """Mark episodes listened/unlistened in Plex (/:/scrobble, /:/unscrobble)."""
    db = open_db()
    s = db.execute("SELECT * FROM shows WHERE lower(name)=lower(?) OR url=?", (show, show)).fetchone()
    if not s:
        sys.exit(f"no show called {show!r} -- see `fetch.py list`")
    q, args = "SELECT file FROM episodes WHERE show_url=? AND status='downloaded'", [s["url"]]
    if before:
        q += " AND published < ?"
        args.append(before)
    files = {f"{PLEX_PATH}/{f}" for (f,) in db.execute(q, args)}
    tracks = (plex(f"/library/sections/{PLEX_SECTION}/all", type="10") or {}).get("MediaContainer", {}).get("Metadata", [])
    hits = [t for t in tracks if any(p.get("file") in files for m in t.get("Media", []) for p in m.get("Part", []))]
    for t in hits:
        plex("/:/scrobble" if played else "/:/unscrobble", key=t["ratingKey"], identifier="com.plexapp.plugins.library")
    print(f"{s['name']}: marked {len(hits)} episode(s) as {'played' if played else 'unplayed'}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="one fetch run now")
    sub.add_parser("loop", help="fetch every interval_hours (the container's default)")
    sub.add_parser("list", help="shows, episodes on disk, last check, errors")
    for name in ("played", "unplayed"):
        p = sub.add_parser(name, help=f"mark a show's episodes {name} in Plex")
        p.add_argument("show", help="show name (as in `list`) or feed URL")
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--all", action="store_true", help="every episode on disk")
        g.add_argument("--before", metavar="YYYY-MM-DD", help="episodes published before this date")
    a = ap.parse_args()

    if a.cmd in ("run", "loop", "played", "unplayed"):
        if not PLEX_TOKEN:
            sys.exit("PLEX_TOKEN is not set")
        if not PLEX_SECTION:
            sys.exit("PLEX_SECTION is not set -- it's your Podcasts library's section id, "
                     "visible in its URL in Plex Web (…/library/sections/<id>/…)")
    if a.cmd == "run":
        locked(run)
    elif a.cmd == "loop":
        cmd_loop()
    elif a.cmd == "list":
        cmd_list()
    else:
        cmd_mark(a.show, a.cmd == "played", a.before)


if __name__ == "__main__":
    main()
