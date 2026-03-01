# Community Posts — Audiobook-Manager Launch

Post these in order, staggered 3-5 days apart. Respond to every comment in the first 2 hours.
Include a screenshot of the web UI wherever the platform supports it.

---

## 1. r/selfhosted (Post First — Tuesday/Wednesday morning ET)

**Flair:** Show-Off

**Title:** Just brought my audiobook manager out of private beta — converts your Audible library to Opus and streams it from your own server

**Body:**

Hey folks! I've been quietly building this for myself for a while now, and it finally feels ready to share with the world.

**Audiobook-Manager** is a self-hosted tool that does two things: converts your Audible purchases (AAXC files) to Opus format, and gives you a web-based library to browse and stream them from your own hardware.

I started it because I have ~800 audiobooks on Audible and it drove me nuts that I could only listen through their app. Now they live on my homelab and I can listen from any browser on any device.

Here's what it looks like day-to-day:

- Drop your Audible files in a folder — the converter picks them up automatically, preserves chapters, cover art, and metadata
- Browse your library through a cozy vintage-themed web UI with search, collections, and sorting
- Hit play and it remembers where you stopped, even across devices
- Family members can have their own accounts with separate listening progress (TOTP and Passkey/FIDO2 auth)
- PDF supplements for those Audible courses that come with companion materials

**Getting started is pretty straightforward:**

```
docker pull ghcr.io/theboscoclub/audiobook-manager:latest
```

Runs on amd64 and arm64 (Raspberry Pi 4/5, Apple Silicon, etc.). Also does bare-metal with systemd if Docker isn't your thing.

This isn't trying to replace Audiobookshelf — different focus. Audiobookshelf is great for organizing an existing library. This is more about the Audible-to-open-format pipeline and then living with your converted collection.

I've been my own only user for a long time, so I'd genuinely love to hear how it works (or doesn't!) for someone else's setup. Fresh eyes catch things you stop noticing after 60+ releases.

MIT licensed, actively maintained: https://github.com/TheBoscoClub/Audiobook-Manager

Happy to answer any questions!

---

## 2. Hacker News (Post 3 days after r/selfhosted — Thursday ~10am ET)

**Title:** Show HN: Audiobook-Manager – Self-hosted Audible converter and streaming library

**Body:**

Hi HN,

I just brought my audiobook manager out of private beta and wanted to share it here. It's an open-source tool that converts Audible AAXC files to Opus and serves them through a web-based library with a streaming player.

I built this because I have ~800 audiobooks I've paid for and I wanted to actually own them. Opus turned out to be a great fit for spoken word — excellent quality at low bitrates, open codec, no patents.

The conversion pipeline preserves chapters, cover art, and metadata. The web UI has search, collections, and position sync across devices. Auth supports TOTP and Passkey/FIDO2 with an encrypted (SQLCipher) database.

Stack: Python/Flask, SQLite, ffmpeg, mutagen. Deploys via Docker (amd64/arm64) or bare-metal with systemd services.

This has been a solo project for a while and I'm excited (and a little nervous) to see how it holds up with other people's libraries and setups. Feedback very welcome — especially on the conversion pipeline and web player.

MIT licensed: https://github.com/TheBoscoClub/Audiobook-Manager

---

## 3. r/audiobooks (Post 3-4 days after HN — Monday/Tuesday)

**Title:** I built a free tool to listen to your Audible books on your own terms — just opened it up after a long private beta

**Body:**

Hey everyone! Fellow audiobook listener here. I've spent the last year+ building something I've wanted for a long time, and I finally feel good enough about it to share.

**The short version:** It's a free, open-source app that converts your Audible purchases into regular audio files (Opus format) and lets you listen to them through a web browser — on your phone, tablet, laptop, wherever.

**Why I built it:** I have around 800 books on Audible and I love the content, but I wanted more flexibility in how I listen. Being tied to one app on one platform felt limiting for books I've paid good money for. So I built my own listener.

**What it's like to use:**

- Your chapters, cover art, and all the book info carry over during conversion
- The player remembers exactly where you stopped — switch from your phone to your laptop and it picks right up
- Browse by author, narrator, genre, or collections. Search works across everything
- If you have family who listens too, everyone gets their own account with separate progress tracking
- Those PDF course companions from the Great Courses? Those come along for the ride too

It does need to run on a computer you own — a Linux server, a Raspberry Pi 4 or 5, a NAS, whatever you have. There's a Docker image that makes setup pretty easy.

I've been the only person using this for a while, and I'm genuinely curious what other listeners think. What's important to you in a listening experience? What would make something like this useful (or not useful) for your workflow?

Totally free, no catch: https://github.com/TheBoscoClub/Audiobook-Manager

---

## 4. r/homelab (Post 3-4 days after r/audiobooks — Thursday/Friday)

**Title:** Just opened up my audiobook manager after a long private beta — converts Audible files and streams them from your homelab

**Body:**

Hey homelabbers! Sharing a project that just came out of private beta — thought this crowd might appreciate it.

**Audiobook-Manager** converts your Audible purchases (AAXC files) to Opus format and serves a web library with streaming playback. I've been running it on my own setup for ~800 books and it's been my daily driver for over a year.

**The stack:**

- Python/Flask + SQLite (SQLCipher for the auth DB)
- ffmpeg for audio processing, mutagen for Opus metadata embedding
- 5 systemd services: converter, mover, downloader, API server, web server
- Docker multi-arch (amd64/arm64) or bare-metal install

**Quick start:**

```
docker pull ghcr.io/theboscoclub/audiobook-manager:latest
```

**Things that might interest this crowd:**

- Automated pipeline: AAXC files go into a watched folder, Opus files come out in your library
- Position sync across devices (SQLite-backed, no cloud dependency)
- TOTP + Passkey/FIDO2 auth with admin approval for new users
- Smart duplicate detection catches re-purchases by content hash
- Genre/narrator sync from Audible's library export (250+ categories)
- HTTPS with self-signed or your own certs

It's been a solo project and I'm excited to see it run on someone else's hardware for the first time. If you're an Audible listener with a homelab, I'd love to know how it goes.

MIT licensed: https://github.com/TheBoscoClub/Audiobook-Manager

---

## 5. r/datahoarder (Post 3-5 days after r/homelab)

**Title:** Just opened up my Audible preservation tool — converts your library to Opus and manages it from your own server

**Body:**

Hey data hoarders! Just brought a project out of private beta that I think speaks this community's language: if you paid for it, you should own it.

**Audiobook-Manager** converts Audible AAXC files to Opus (open, patent-free codec — excellent quality for spoken word at low bitrates) and serves them from your own hardware.

**What it preserves:**

- Full chapter markers
- Cover art (embedded in each Opus file)
- All metadata: title, author, narrator, series info, publish dates
- PDF supplements (course companions, maps, reference materials)

**What it adds on top:**

- Automated pipeline — drop AAXC files in a directory and walk away
- Web library with streaming player and position sync across devices
- Smart duplicate detection (content hash + metadata matching)
- Genre/narrator sync from your Audible library export
- Multi-user auth (TOTP, Passkey/FIDO2) with encrypted database

I've been running it for ~800 books on my own server. Everything is stored in open formats on my own disks. No cloud dependency, no phone-home, no subscription.

Just opened this up after a long private beta and I'm genuinely curious how other people's libraries compare to mine. Different collections probably exercise the conversion pipeline in ways I haven't seen yet.

Free and open source (MIT): https://github.com/TheBoscoClub/Audiobook-Manager

---

## Posting Schedule

| # | Platform | Suggested Day | Best Time |
|---|----------|---------------|-----------|
| 1 | r/selfhosted | Tue or Wed | Morning ET |
| 2 | Hacker News | Thu (3 days later) | 10am-12pm ET |
| 3 | r/audiobooks | Mon/Tue (next week) | Morning ET |
| 4 | r/homelab | Thu/Fri (same week) | Morning ET |
| 5 | r/datahoarder | Tue (following week) | Morning ET |

**Tips:**
- Respond to every comment in the first 2 hours — engagement is everything
- Include a screenshot of the web UI wherever the platform supports images
- If any post gets real traction, pause the others for a few days to ride the wave
- Don't cross-post — each post is custom-written for that audience
- Be genuine in replies. You're not selling anything. You're sharing something you made.
