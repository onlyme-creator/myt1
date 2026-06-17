import os
import re
import glob
import gzip
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# Force accurate paths matching your precise folder image
BASE_DIR = r"C:\Users\Administrator\Documents\TV"
PLAYLIST_PATH = os.path.join(BASE_DIR, "playlist.m3u")
DEAD_REGISTRY_PATH = os.path.join(BASE_DIR, "dead_channels.txt")
OUTPUT_EPG_PATH = os.path.join(BASE_DIR, "shrunk_epg.xml")

FALLBACK_EPG_URL = "https://github.io"
DOWNLOADED_GZ_PATH = os.path.join(BASE_DIR, "downloaded_guide.xml.gz")

EPG_MAPPING = {
    "USA Network": "USANetwork.us", 
    "USA Network HD": "USA.us",
    "Charge TV": "Charge.us",
    "Charge!": "ChargeTV.us",
}

def clean_string(s):
    if not s: return ""
    return re.sub(r'[^a-z0-9]', '', s.lower())

def get_best_epg_source():
    # Look for your exact files shown in your image layout
    search_patterns = [
        os.path.join(BASE_DIR, "epg_ripper_US2*"),
        os.path.join(BASE_DIR, "plex_us_epg*")
    ]
    matching_files = []
    for pattern in search_patterns:
        matching_files.extend(glob.glob(pattern))
        
    if matching_files:
        best_file = max(matching_files, key=os.path.getmtime)
        print(f"📦 [LOG] Found local source EPG file: {os.path.basename(best_file)}")
        return best_file

    print(f"🌐 [LOG] Local files not found. Fetching backup data from internet source...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(FALLBACK_EPG_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response, open(DOWNLOADED_GZ_PATH, 'wb') as out_file:
            out_file.write(response.read())
        
        extracted_xml_path = os.path.join(BASE_DIR, "downloaded_guide.xml")
        with gzip.open(DOWNLOADED_GZ_PATH, 'rb') as f_in, open(extracted_xml_path, 'wb') as f_out:
            f_out.write(f_in.read())
        return extracted_xml_path
    except Exception as e:
        raise FileNotFoundError(f"Failed to extract local files or download web source: {e}")

def parse_m3u_file(path):
    channels = []
    print(f"📖 [LOG] Checking for playlist file at: {path}")
    if not os.path.exists(path):
        print(f"⚠️ [WARNING] Playlist file path does not exist!")
        return channels
    
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        current_extinf = None
        for line in f:
            line = line.strip()
            if line.startswith('#EXTINF:'): 
                current_extinf = line
            elif line and not line.startswith('#'):
                if current_extinf:
                    channels.append({"extinf": current_extinf, "url": line})
                    current_extinf = None
    print(f"📖 [LOG] Extracted {len(channels)} stream channels from your file.")
    return channels

def parse_dead_registry():
    channels = []
    if not os.path.exists(DEAD_REGISTRY_PATH): return channels
    with open(DEAD_REGISTRY_PATH, 'r', encoding='utf-8') as f:
        content = f.read().split("\n\n")
        for block in content:
            lines = block.strip().split('\n')
            if len(lines) == 2:
                channels.append({"extinf": lines[0], "url": lines[1]})
    return channels

def check_stream(channel):
    try:
        req = urllib.request.Request(channel["url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.getcode() == 200: return channel, True
    except Exception: pass
    return channel, False

def fix_timezone_string(time_str):
    if not time_str: return time_str
    match = re.match(r'^(\d{14})', time_str.strip())
    if match: return f"{match.group(1)} -0400"
    return time_str

def run_pipeline():
    print("==============================================")
    print("🚀 STARTING IPTV UPDATE SCRIPT")
    print("==============================================")
    
    active_pool = parse_m3u_file(PLAYLIST_PATH)
    dead_pool = parse_dead_registry()
    combined_channels = active_pool + dead_pool
    
    if not combined_channels:
        print("❌ [STOP] No channels extracted! Your playlist file is empty or formatted incorrectly.")
        return

    print(f"📡 [LOG] Verifying stream status for {len(combined_channels)} channels...")
    live_list, dead_list = [], []
    with ThreadPoolExecutor(max_workers=15) as executor:
        for channel, is_alive in executor.map(check_stream, combined_channels):
            if is_alive: live_list.append(channel)
            else: dead_list.append(channel)

    print(f"📊 [STATUS] Live: {len(live_list)} | Dead: {len(dead_list)}")

    with open(PLAYLIST_PATH, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for ch in live_list: f.write(f"{ch['extinf']}\n{ch['url']}\n")
        
    with open(DEAD_REGISTRY_PATH, 'w', encoding='utf-8') as f:
        f.write("\n\n".join([f"{ch['extinf']}\n{ch['url']}" for ch in dead_list]))

    m3u_targets = {}
    id_pattern = re.compile(r'tvg-id="([^"]+)"')
    name_pattern = re.compile(r'tvg-name="([^"]+)"')
    
    for ch in live_list:
        extinf = ch['extinf']
        tvg_id = id_pattern.search(extinf)
        tvg_name = name_pattern.search(extinf)
        display_name = extinf.split(',')[-1].strip()
        
        primary_key = tvg_id.group(1).strip() if tvg_id else (tvg_name.group(1).strip() if tvg_name else display_name)
        m3u_targets[primary_key] = {
            "clean_name": clean_string(display_name),
            "clean_id": clean_string(primary_key)
        }

    try:
        epg_source = get_best_epg_source()
        context = ET.iterparse(epg_source, events=('start', 'end'))
        new_root = ET.Element("tv")
        
        try:
            _, first_el = next(context)
            if first_el.tag == 'tv': new_root.attrib = first_el.attrib
        except StopIteration: pass

        ripper_to_m3u_map = {}

        print("🔍 [LOG] Building guide entries...")
        for event, elem in context:
            if event == 'end' and elem.tag == 'channel':
                r_id = elem.get('id')
                r_name_el = elem.find('display-name')
                r_name = r_name_el.text if r_name_el is not None else ""
                
                clean_r_id = clean_string(r_id)
                clean_r_name = clean_string(r_name)
                
                found_match = None
                if r_id in EPG_MAPPING: found_match = r_id
                elif r_id in m3u_targets: found_match = r_id
                else:
                    for m3u_key, meta in m3u_targets.items():
                        if clean_r_id == meta["clean_id"] or clean_r_name == meta["clean_name"]:
                            found_match = m3u_key
                            break
                
                if found_match:
                    elem.set('id', found_match)
                    new_root.append(elem)
                    ripper_to_m3u_map[r_id] = found_match
                else:
                    elem.clear()

        context_prog = ET.iterparse(epg_source, events=('start', 'end'))
        for event, elem in context_prog:
            if event == 'end' and elem.tag == 'programme':
                p_channel = elem.get('channel')
                target_m3u_id = ripper_to_m3u_map.get(p_channel) or (p_channel if p_channel in m3u_targets else None)
                
                if target_m3u_id:
                    elem.set('channel', target_m3u_id)
                    elem.set('start', fix_timezone_string(elem.get('start')))
                    elem.set('stop', fix_timezone_string(elem.get('stop')))
                    new_root.append(elem)
                else:
                    elem.clear()

        tree = ET.ElementTree(new_root)
        tree.write(OUTPUT_EPG_PATH, encoding='utf-8', xml_declaration=True)
        print(f"✅ SUCCESS! Final compressed guide saved at: {OUTPUT_EPG_PATH}")
        
    except Exception as e:
        print(f"❌ [ERROR] Guide structure failure: {e}")

if __name__ == "__main__":
    run_pipeline()