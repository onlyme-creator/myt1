import re
import os
import glob
from lxml import etree

# --- Configuration ---
SCRIPT_DIR = r"C:\Users\Administrator\Documents\TV"
PLAYLIST_FILE = os.path.join(SCRIPT_DIR, "playlist.m3u")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "shrunk_epg.xml")
EPG_PATTERN = os.path.join(SCRIPT_DIR, "epg_ripper_US2*.xml")
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

def find_epg_source(pattern):
    """Find the EPG source file matching the glob pattern."""
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No EPG source file found matching: {pattern}")
    if len(matches) > 1:
        print(f"[!] Multiple EPG files found, using: {matches[0]}")
    print(f"[+] Using EPG source: {matches[0]}")
    return matches[0]

def clean_timestamp(raw_ts):
    """
    Safely extract the first 14 digits from a timestamp string and
    append the Eastern timezone offset. Returns a plain string with
    NO brackets or quotes, e.g. '20260617120000 -0400'.
    """
    if not raw_ts:
        return ""
    # Strip whitespace and extract only the first 14 digit characters
    ts_str = str(raw_ts).strip()
    match = re.match(r'(\d{14})', ts_str)
    if match:
        return f"{match.group(1)} {TIMEZONE_OFFSET}"
    # Fallback: grab any run of digits up to 14
    digits = re.sub(r'\D', '', ts_str)[:14]
    if digits:
        return f"{digits} {TIMEZONE_OFFSET}"
    return ts_str  # Return as-is if nothing matched

def build_shrunk_epg(epg_source, channel_ids, output_path):
    """
    Stream-parse the large EPG XML file, keep only matching channels
    and their programmes, fix timestamps, and write clean output XML.
    """
    matched_channels = {}   # id -> lxml Element
    matched_programmes = [] # list of lxml Elements

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
                # Deep copy so we can clear the tree safely
                matched_channels[ch_id] = elem
            else:
                elem.clear()

        elif tag == "programme":
            ch_id = (elem.get("channel") or "").strip()
            if ch_id in channel_ids:
                # Fix the start timestamp
                raw_start = elem.get("start", "")
                clean_start = clean_timestamp(raw_start)
                elem.set("start", clean_start)

                # Fix the stop timestamp
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

    # --- Build output XML tree ---
    print(f"[+] Building output XML...")
    tv_root = etree.Element("tv")
    tv_root.set("generator-info-name", "shrunk_epg_builder")

    # Write matched channel elements first
    for ch_id in channel_ids:
        if ch_id in matched_channels:
            tv_root.append(matched_channels[ch_id])

    # Write matched programme elements
    for prog in matched_programmes:
        tv_root.append(prog)

    # --- Serialize to file ---
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

    # Step 1: Extract channel IDs from playlist
    channel_ids = extract_channel_ids(PLAYLIST_FILE)
    if not channel_ids:
        print("ERROR: No channel IDs found in playlist. Aborting.")
        return

    # Step 2: Locate EPG source file
    epg_source = find_epg_source(EPG_PATTERN)

    # Step 3 & 4: Parse EPG, fix timestamps, build output
    build_shrunk_epg(epg_source, channel_ids, OUTPUT_FILE)

    print("=" * 60)
    print("  All done! Load shrunk_epg.xml into TiviMate.")
    print("=" * 60)

if __name__ == "__main__":
    main()