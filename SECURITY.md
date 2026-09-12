# Security Policy

## Reporting a vulnerability

**Please report privately first — don't open a public issue.**

Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/claude-on-call/plex-podcast-fetch/security/advisories/new)
(the Security tab of this repository). It creates a private advisory visible
only to you and the maintainers.

If that isn't available to you, open a public issue that says only "I'd like
to report a security issue privately" with no details, and you'll get a
private channel to use.

Please include:

- what version (commit) is affected,
- what an attacker could do,
- steps to reproduce, ideally with a minimal example (a crafted feed, say),
- anything that limits the impact.

Please **don't include your real Plex token or personal data** in your report.

## What to expect

- **Acknowledgement within 7 days.** This is a spare-time project, not a
  staffed security team, so please allow for that.
- An assessment and a fix plan, or an explanation of why it isn't a
  vulnerability, within 30 days.
- Credit in the release notes and the advisory when a report leads to a fix,
  unless you'd rather stay anonymous.
- Please give us a reasonable chance to ship a fix before disclosing publicly.
  90 days is a fair default, less if the issue is being actively exploited.

## Scope

Things genuinely in scope:

- a malicious or malformed podcast feed causing more than a failed fetch of
  that one show — arbitrary file write outside the library folder, code
  execution, or a crash that takes down the whole `loop`,
- the Plex token being written or logged anywhere it shouldn't be,
- command or SQL injection through a feed's own content (titles, episode
  names) or through `feeds.yaml`.

Out of scope: whatever your own Plex server does, and anything that needs an
attacker to already control the machine this runs on or your Plex server
itself. Malicious *podcast publishers* deliberately attacking their own
subscribers is a real-world threat model this project does take seriously —
report it.

## Supported versions

Only the latest commit on `main` is supported. There are no backports.
