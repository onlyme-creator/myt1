import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

print("==============================================")
print("🚀 RUNNING AUTOMATED CLOUD STREAM HEALTH CHECK")
print("==============================================")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_PATH = os.path.join(BASE_DIR, "playlist.m3u")
DEAD_REGISTRY_PATH = os.path.join(BASE_DIR, "dead_channels.txt")

def parse_m3u_file(path):
    channels = []
    if not os.path.exists(path): return channels
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        current_extinf = None
        for line in f:
            line = line.strip()
            if line.startswith('#EXTINF:'): current_extinf = line
            elif line and not line.startswith('#') and current_extinf:
                channels.append({"extinf": current_extinf, "url": line})
                current_extinf = None
    return channels

def parse_dead_registry():
    channels = []
    if not os.path.exists(DEAD_REGISTRY_PATH): return channels
    with open(DEAD_REGISTRY_PATH, 'r', encoding='utf-8') as f:
        content = f.read().split("\n\n")
        for block in content:
            lines = block.strip().split('\n')
            if len(lines) == 2: channels.append({"extinf": lines, "url": lines})
    return channels

def check_stream(channel):
    try:
        req = urllib.request.Request(channel["url"], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.getcode() == 200: return channel, True
    except Exception: pass
    return channel, False

# Execute stream health cycles
active_pool = parse_m3u_file(PLAYLIST_PATH)
dead_pool = parse_dead_registry()
combined_channels = active_pool + dead_pool

if combined_channels:
    print(f"📡 Pinging stream connections...")
    live_list, dead_list = [], []
    with ThreadPoolExecutor(max_workers=15) as executor:
        for channel, is_alive in executor.map(check_stream, combined_channels):
            if is_alive: live_list.append(channel)
            else: dead_list.append(channel)

    # Save sorted clean streams back to your repository text files
    with open(PLAYLIST_PATH, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for ch in live_list: f.write(f"{ch['extinf']}\n{ch['url']}\n")
    with open(DEAD_REGISTRY_PATH, 'w', encoding='utf-8') as f:
        f.write("\n\n".join([f"{ch['extinf']}\n{ch['url']}" for ch in dead_list]))
        
    print(f"📊 Streams Verified: Live: {len(live_list)} | Dead: {len(dead_list)}")
    print("✅ SUCCESS! Cloud health sync completed safely.")
else:
    print("❌ No channels found to check.")
