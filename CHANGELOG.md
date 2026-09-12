# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-12

First release, extracted from a working self-hosted setup and generalised:
every path, library section, and account-specific comment became a
documented setting or example.

### Added

- `fetch.py` — polls podcast feeds, downloads and tags new episodes for a
  Plex Music library used as "Podcasts", prunes episodes once they're
  listened to and past their retention window, and asks Plex to scan only
  the show folders that changed (never a full library rescan).
- Handles MP3, M4A/AAC, Ogg Vorbis and Opus episodes; embeds show artwork;
  sets each episode's Plex "date added" to its actual publish date so an
  "unplayed, oldest first" smart playlist plays episodes in the right order.
- `list`, `played`/`unplayed` subcommands for status and marking episodes
  listened without touching the file.
- Docker image with pinned dependencies; `docker-compose.yml.example` and
  `feeds.yaml.example` to copy and edit.
- `PLEX_SECTION` has no default — deliberately, since a wrong guess could
  touch the wrong library.
- Project documentation: README, contributing guide, security policy, issue
  and pull request templates, and a dependency-install + syntax/`--help` CI
  check.

[Unreleased]: https://github.com/claude-on-call/plex-podcast-fetch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/claude-on-call/plex-podcast-fetch/releases/tag/v0.1.0
