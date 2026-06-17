"""
update_playlist.py  —  GitHub Actions cloud script + optional home runner.
Place this file in the ROOT of your GitHub repository.

WHAT THIS SCRIPT DOES
---------------------
- HOME MODE  (run manually on your Windows PC):
    • Pings every stream URL from playlist.m3u using your residential IP.
    • Channels that fail N consecutive checks → moved to dead_channels.txt.
    • Channels that were dead but now respond → restored to playlist.m3u.
    • Writes health_report.txt with a timestamped status for every channel.

- CLOUD MODE (GitHub Actions, any Linux runner):
    • Detects it is NOT running at home (no C:\\Users\\Administrator).
    • Completely SKIPS all network pings — datacenter IPs are blocked by
      most IPTV providers and produce false-dead results.
    • Copies the existing playlist.m3u and dead_channels.txt as-is so the
      commit step has something to stage (idempotent — no harm if nothing
      changed).
    • Writes a simple health_report.txt noting that ping checks were skipped.

ABSOLUTE GUARANTEE
------------------
This script contains ZERO code for XML reading, XML writing, or anything
that touches shrunk_epg.xml or plex_us_epg.xml.  Those files are 100% safe.

REQUIRES (for home-mode pinging)
--------
    pip install requests

USAGE
-----
    python update_playlist.py           # auto-detects mode
    python update_playlist.py --home    # force home mode  (for testing)
    python update_playlist.py --cloud   # force cloud mode (for testing)
"""

import re
import os
import sys
import time
import shutil
import datetime
import argparse

# ── Mode detection ────────────────────────────────────────────────────────────
#
# We look for a path that only exists on your home PC.
# GitHub Actions runners are always Linux, so this path is never present there.
IS_RUNNING_AT_HOME = os.path.exists(r"C:\Users\Administrator")

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_FILE = os.path.join(SCRIPT_DIR, "playlist.m3u")
DEAD_FILE     = os.path.join(SCRIPT_DIR, "dead_channels.txt")
HEALTH_FILE   = os.path.join(SCRIPT_DIR, "health_report.txt")

# Home-mode ping settings
PING_TIMEOUT_SEC = 10      # seconds to wait for each stream HEAD/GET
PING_RETRIES     = 2       # how many attempts before declaring a channel dead
PING_RETRY_WAIT  = 3       # seconds between retries

# ── Playlist I/O ──────────────────────────────────────────────────────────────

def parse_playlist(path: str) -> list[dict]:
    """
    Returns a list of channel dicts:
        {
            "extinf":       "#EXTINF:..." line (raw, includes newline),
            "url":          stream URL string,
            "tvg_id":       value of tvg-id="...",
            "tvg_name":     value of tvg-name="...",
            "display_name": text after the last comma on the #EXTINF line,
        }
    """
    channels = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        print(f"[ERROR] Playlist not found: {path}")
        raise

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        if line.startswith("#EXTINF"):
            extinf_line = line
            url_line    = ""
            # The URL should be the very next non-blank, non-comment line
            j = i + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate and not candidate.startswith("#"):
                    url_line = candidate
                    i = j      # advance outer loop past the URL line
                    break
                j += 1

            m_id   = re.search(r'tvg-id="([^"]+)"',   extinf_line, re.IGNORECASE)
            m_name = re.search(r'tvg-name="([^"]+)"', extinf_line, re.IGNORECASE)
            m_disp = re.search(r',(.+)$',              extinf_line)

            channels.append({
                "extinf":       extinf_line,
                "url":          url_line,
                "tvg_id":       m_id.group(1).strip()   if m_id   else "",
                "tvg_name":     m_name.group(1).strip() if m_name else "",
                "display_name": m_disp.group(1).strip() if m_disp else "",
            })
        i += 1

    return channels


def write_playlist(path: str, channels: list[dict]) -> None:
    """Write a clean M3U file from a list of channel dicts."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#EXTM3U\n")
        for ch in channels:
            fh.write(ch["extinf"] + "\n")
            fh.write(ch["url"]    + "\n")


def load_dead_channels(path: str) -> dict:
    """
    Returns a dict: { url -> {"tvg_id": ..., "extinf": ..., "url": ...} }
    File format (written by save_dead_channels):
        [DEAD] <display_name>
        tvg_id=<tvg_id>
        extinf=<raw #EXTINF line>
        url=<url>
        ---
    """
    dead: dict = {}
    if not os.path.exists(path):
        return dead
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        blocks = [b.strip() for b in content.split("---") if b.strip()]
        for block in blocks:
            rec: dict = {}
            for line in block.splitlines():
                if line.startswith("tvg_id="):
                    rec["tvg_id"] = line[len("tvg_id="):]
                elif line.startswith("extinf="):
                    rec["extinf"] = line[len("extinf="):]
                elif line.startswith("url="):
                    rec["url"] = line[len("url="):]
            if "url" in rec:
                dead[rec["url"]] = rec
    except Exception as exc:
        print(f"[WARN] Could not parse dead_channels.txt: {exc}")
    return dead


def save_dead_channels(path: str, dead: dict) -> None:
    """Write dead_channels.txt from a dict {url -> record}."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for url, rec in dead.items():
            name = rec.get("display_name") or rec.get("tvg_name") or rec.get("tvg_id") or url
            fh.write(f"[DEAD] {name}\n")
            fh.write(f"tvg_id={rec.get('tvg_id','')}\n")
            fh.write(f"extinf={rec.get('extinf','')}\n")
            fh.write(f"url={url}\n")
            fh.write("---\n")


# ── Network ping ──────────────────────────────────────────────────────────────

def ping_stream(url: str) -> bool:
    """
    Returns True if the stream URL responds with an HTTP 2xx or 3xx status.
    Uses a HEAD request first (cheap), falls back to a partial GET if the
    server returns 405 Method Not Allowed.
    Times out after PING_TIMEOUT_SEC seconds.
    """
    try:
        import requests
    except ImportError:
        print("[ERROR] 'requests' library not installed.  Run: pip install requests")
        sys.exit(1)

    headers = {"User-Agent": "TiviMate/4.0 (Android)"}

    for attempt in range(1, PING_RETRIES + 1):
        try:
            resp = requests.head(
                url,
                timeout=PING_TIMEOUT_SEC,
                allow_redirects=True,
                headers=headers,
            )
            if resp.status_code == 405:
                # HEAD not allowed — try a tiny GET
                resp = requests.get(
                    url,
                    timeout=PING_TIMEOUT_SEC,
                    allow_redirects=True,
                    headers=headers,
                    stream=True,
                )
                resp.close()
            if resp.status_code < 400:
                return True
        except Exception:
            pass
        if attempt < PING_RETRIES:
            time.sleep(PING_RETRY_WAIT)

    return False


# ── Home mode ─────────────────────────────────────────────────────────────────

def run_home_mode() -> None:
    print("[MODE] HOME — pinging all streams from residential IP …\n")

    channels = parse_playlist(PLAYLIST_FILE)
    dead_map  = load_dead_channels(DEAD_FILE)

    alive_channels: list[dict] = []
    newly_dead:     list[dict] = []
    recovered:      list[dict] = []
    health_lines:   list[str]  = []

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    health_lines.append(f"Health Report — {now} (Home Mode)\n")
    health_lines.append("=" * 60 + "\n")

    # Check every channel currently IN the playlist
    for ch in channels:
        url   = ch["url"]
        label = ch["display_name"] or ch["tvg_id"] or url
        sys.stdout.write(f"  Checking: {label[:55]:<55} … ")
        sys.stdout.flush()

        live = ping_stream(url)
        if live:
            print("✓ ALIVE")
            alive_channels.append(ch)
            health_lines.append(f"  [ALIVE]  {label}\n")
            # If this channel was previously in dead_channels, it has recovered
            if url in dead_map:
                recovered.append(ch)
        else:
            print("✗ DEAD")
            newly_dead.append(ch)
            dead_map[url] = {
                "tvg_id":       ch["tvg_id"],
                "tvg_name":     ch["tvg_name"],
                "display_name": ch["display_name"],
                "extinf":       ch["extinf"],
                "url":          url,
            }
            health_lines.append(f"  [DEAD ]  {label}\n")

    # Check if any previously-dead channels have recovered
    urls_in_playlist = {ch["url"] for ch in channels}
    for url, rec in list(dead_map.items()):
        if url in urls_in_playlist:
            continue  # already checked above
        label = rec.get("display_name") or rec.get("tvg_name") or rec.get("tvg_id") or url
        sys.stdout.write(f"  Recheck dead: {label[:50]:<50} … ")
        sys.stdout.flush()
        live = ping_stream(url)
        if live:
            print("✓ RECOVERED")
            ch_restored = {
                "extinf":       rec["extinf"],
                "url":          url,
                "tvg_id":       rec.get("tvg_id", ""),
                "tvg_name":     rec.get("tvg_name", ""),
                "display_name": rec.get("display_name", ""),
            }
            alive_channels.append(ch_restored)
            del dead_map[url]
            recovered.append(ch_restored)
            health_lines.append(f"  [RECOV]  {label}\n")
        else:
            print("✗ still dead")
            health_lines.append(f"  [STILL]  {label}\n")

    # Summary
    health_lines.append("\n" + "=" * 60 + "\n")
    health_lines.append(
        f"  Alive: {len(alive_channels)}  |  Dead: {len(dead_map)}  |  "
        f"Newly dead: {len(newly_dead)}  |  Recovered: {len(recovered)}\n"
    )

    # Write outputs
    write_playlist(PLAYLIST_FILE, alive_channels)
    save_dead_channels(DEAD_FILE, dead_map)
    with open(HEALTH_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(health_lines)

    print(f"\n[+] playlist.m3u   → {len(alive_channels)} alive channels")
    print(f"[+] dead_channels.txt → {len(dead_map)} dead channels")
    if recovered:
        print(f"[+] Recovered: {[r['display_name'] or r['tvg_id'] for r in recovered]}")
    print(f"[+] health_report.txt written.")


# ── Cloud mode ────────────────────────────────────────────────────────────────

def run_cloud_mode() -> None:
    print("[MODE] CLOUD — skipping all network pings (datacenter IP detected).\n")
    print("  playlist.m3u and dead_channels.txt will not be modified.")
    print("  shrunk_epg.xml is completely untouched (as always).\n")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    with open(HEALTH_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"Health Report — {now} (Cloud Mode)\n")
        fh.write("=" * 60 + "\n")
        fh.write("  Network ping checks SKIPPED in cloud/CI mode.\n")
        fh.write("  All channels assumed ALIVE to prevent false-dead removals\n")
        fh.write("  caused by datacenter IP blocks from IPTV providers.\n")
        fh.write("  Run this script on your home PC for accurate ping results.\n")
        fh.write("=" * 60 + "\n")

    # Ensure dead_channels.txt exists so the git add in the workflow doesn't fail
    if not os.path.exists(DEAD_FILE):
        with open(DEAD_FILE, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# No dead channels recorded yet.\n")

    print("[+] health_report.txt written.")
    print("[+] Cloud run complete — no files were overwritten.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 65)
    print("  update_playlist.py  —  TiviMate Stream Health Manager")
    print("=" * 65)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home",  action="store_true", help="Force home mode")
    parser.add_argument("--cloud", action="store_true", help="Force cloud mode")
    args, _ = parser.parse_known_args()

    if args.home:
        mode = "home"
    elif args.cloud:
        mode = "cloud"
    else:
        mode = "home" if IS_RUNNING_AT_HOME else "cloud"

    print(f"  Detected mode : {'HOME' if mode == 'home' else 'CLOUD (GitHub Actions)'}")
    print(f"  Playlist      : {PLAYLIST_FILE}")
    print()

    if mode == "home":
        run_home_mode()
    else:
        run_cloud_mode()

    print("\n" + "=" * 65)
    print("  Done.")
    print("=" * 65)


if __name__ == "__main__":
    main()
