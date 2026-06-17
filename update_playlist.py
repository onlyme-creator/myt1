import re
import os
import glob
import sys
from lxml import etree

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_FILE = os.path.join(SCRIPT_DIR, "playlist.m3u")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "shrunk_epg.xml")
TIMEZONE_OFFSET = "-0400"

def extract_channel_ids(playlist_path):
    """Parse playlist.m3u and extract all tvg-id values."""
    channel_ids = set()
    try:
        with open(playlist_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    match = re.search(r'tvg-id="([^"]+)"', line, re.IGNORECASE)
                    if match:
                        tvg_id = match.group(1).strip()
                        if tvg_id:
                            channel_ids.add(tvg_id)
    except FileNotFoundError:
        print(f"ERROR: Playlist file not found: {playlist_path}")
        raise
    print(f"[+] Found {len(channel_ids)} unique channel IDs in playlist.")
    return channel_ids

def find_epg_source():
    """
    Search for the EPG source file across all likely locations.
    Priority order:
      1. EPG_FILE env var (set this in your Actions workflow for a guaranteed path)
      2. Same directory as the script
      3. Current working directory
      4. Workspace root (common in GitHub Actions: /home/runner/work/REPO/REPO)
      5. Full recursive search from the repo root downward
    """
    pattern = "epg_ripper_US2*.xml"

    # 1. Explicit env var override — most reliable in CI
    env_path = os.environ.get("EPG_FILE")
    if env_path:
        if os.path.isfile(env_path):
            print(f"[+] EPG source from EPG_FILE env var: {env_path}")
            return env_path
        else:
            print(f"[!] EPG_FILE env var set to '{env_path}' but file not found there.")

    # 2. Build a list of candidate directories to search
    search_dirs = []

    # Script's own directory
    search_dirs.append(SCRIPT_DIR)

    # Current working directory (may differ from script dir in CI)
    cwd = os.getcwd()
    if cwd not in search_dirs:
        search_dirs.append(cwd)

    # GitHub Actions workspace root: /home/runner/work/REPO/REPO
    # Walk up from script dir to find the workspace root
    candidate = SCRIPT_DIR
    for _ in range(6):
        candidate = os.path.dirname(candidate)
        if candidate and candidate not in search_dirs:
            search_dirs.append(candidate)
        if candidate in ("/", ""):
            break

    # Also check GITHUB_WORKSPACE env var if present
    gh_workspace = os.environ.get("GITHUB_WORKSPACE")
    if gh_workspace and gh_workspace not in search_dirs:
        search_dirs.append(gh_workspace)

    # 3. Check each candidate dir (non-recursive first — fast)
    print(f"[+] Searching for '{pattern}' in candidate directories...")
    for d in search_dirs:
        matches = glob.glob(os.path.join(d, pattern))
        if matches:
            print(f"[+] Found EPG source: {matches[0]}")
            return matches[0]

    # 4. Recursive fallback — search entire repo tree from workspace root
    root = gh_workspace or SCRIPT_DIR
    # Walk up to a likely repo root (stop at filesystem root or after 4 levels)
    for _ in range(4):
        parent = os.path.dirname(root)
        if parent == root:
            break
        root = parent

    print(f"[+] Falling back to recursive search under: {root}")
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and common noise
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git')]
        for fname in filenames:
            if re.match(r'epg_ripper_US2.*\.xml$', fname, re.IGNORECASE):
                found = os.path.join(dirpath, fname)
                print(f"[+] Found EPG source (recursive): {found}")
                return found

    # 5. Nothing found — print a helpful diagnostic before raising
    print("\n[!] Could not locate the EPG source file. Diagnostic info:")
    print(f"    Script dir : {SCRIPT_DIR}")
    print(f"    CWD        : {cwd}")
    print(f"    Searched   : {search_dirs}")
    print(f"    Tip: Set the EPG_FILE environment variable in your workflow, e.g.:")
    print(f"         EPG_FILE: /home/runner/work/myt1/myt1/epg_ripper_US2_something.xml")
    raise FileNotFoundError(f"No file matching '{pattern}' found anywhere under {root}")

def clean_timestamp(raw_ts):
    """
    Safely extract the first 14 digits from a timestamp string and
    append the Eastern timezone offset. Returns a plain string with
    NO brackets or quotes, e.g. '20260617120000 -0400'.
    """
    if not raw_ts:
        return ""
    ts_str = str(raw_ts).strip()
    match = re.match(r'(\d{14})', ts_str)
    if match:
        return f"{match.group(1)} {TIMEZONE_OFFSET}"
    digits = re.sub(r'\D', '', ts_str)[:14]
    if digits:
        return f"{digits} {TIMEZONE_OFFSET}"
    return ts_str

def build_shrunk_epg(epg_source, channel_ids, output_path):
    """
    Stream-parse the large EPG XML file, keep only matching channels
    and their programmes, fix timestamps, and write clean output XML.
    """
    matched_channels = {}
    matched_programmes = []

    print(f"[+] Stream-parsing EPG source (this may take a moment)...")

    context = etree.iterparse(
        epg_source,
        events=("end",),
        tag=("channel", "programme"),
        recover=True,
        encoding="utf-8"
    )

    processed = 0
    for event, elem in context:
        tag = elem.tag

        if tag == "channel":
            ch_id = (elem.get("id") or "").strip()
            if ch_id in channel_ids:
                matched_channels[ch_id] = elem
            else:
                elem.clear()

        elif tag == "programme":
            ch_id = (elem.get("channel") or "").strip()
            if ch_id in channel_ids:
                raw_start = elem.get("start", "")
                clean_start = clean_timestamp(raw_start)
                elem.set("start", clean_start)

                raw_stop = elem.get("stop", "")
                clean_stop = clean_timestamp(raw_stop)
                elem.set("stop", clean_stop)

                matched_programmes.append(elem)
            else:
                elem.clear()

        processed += 1
        if processed % 50000 == 0:
            print(f"    ...processed {processed:,} elements so far")

    print(f"[+] Done parsing. Matched {len(matched_channels)} channels, "
          f"{len(matched_programmes)} programmes.")

    print(f"[+] Building output XML...")
    tv_root = etree.Element("tv")
    tv_root.set("generator-info-name", "shrunk_epg_builder")

    for ch_id in channel_ids:
        if ch_id in matched_channels:
            tv_root.append(matched_channels[ch_id])

    for prog in matched_programmes:
        tv_root.append(prog)

    print(f"[+] Writing output to: {output_path}")
    tree = etree.ElementTree(tv_root)
    tree.write(
        output_path,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True
    )
    print(f"[+] Successfully wrote: {output_path}")

def main():
    print("=" * 60)
    print("  shrunk_epg builder — TiviMate EPG Filter Script")
    print("=" * 60)

    channel_ids = extract_channel_ids(PLAYLIST_FILE)
    if not channel_ids:
        print("ERROR: No channel IDs found in playlist. Aborting.")
        return

    epg_source = find_epg_source()
    build_shrunk_epg(epg_source, channel_ids, OUTPUT_FILE)

    print("=" * 60)
    print("  All done! Load shrunk_epg.xml into TiviMate.")
    print("=" * 60)

if __name__ == "__main__":
    main()