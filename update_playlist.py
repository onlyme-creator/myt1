import os
import re
import xml.etree.ElementTree as ET

print("==============================================")
print("🚀 RUNNING SECURE EPG REBUILD ENGINE")
print("==============================================")

# Dynamic path handles local testing and cloud execution automatically
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYLIST_PATH = os.path.join(BASE_DIR, "playlist.m3u")
OUTPUT_EPG_PATH = os.path.join(BASE_DIR, "shrunk_epg.xml")

# 1. Automatically locate your local epg_ripper source file
source_file = None
for f in os.listdir(BASE_DIR):
    if f.startswith("epg_ripper_US2") and f.endswith(".xml"):
        source_file = os.path.join(BASE_DIR, f)
        break

if not source_file:
    print("❌ ERROR: Could not find any epg_ripper_US2 XML file in your folder!")
    exit()

print(f"📦 Found source guide file: {os.path.basename(source_file)}")

# 2. Extract active channel IDs from your playlist
target_ids = set()
if os.path.exists(PLAYLIST_PATH):
    print(f"📖 Reading your active playlist: {PLAYLIST_PATH}")
    id_pattern = re.compile(r'tvg-id="([^"]+)"')
    name_pattern = re.compile(r'tvg-name="([^"]+)"')
    
    with open(PLAYLIST_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if line.startswith('#EXTINF:'):
                id_match = id_pattern.search(line)
                name_match = name_pattern.search(line)
                if id_match:
                    target_ids.add(id_match.group(1).strip())
                elif name_match:
                    target_ids.add(name_match.group(1).strip())
else:
    print("❌ ERROR: playlist.m3u not found!")
    exit()

# Force hardcoded fallback bridges for USA and Charge tags
target_ids.add("USA.us")
target_ids.add("USANetwork.us")
target_ids.add("Charge.us")

print(f"🎯 Matching criteria enabled for {len(target_ids)} channels.")

# 3. Stream through XML elements, extract data, and enforce Eastern Time zone
try:
    context = ET.iterparse(source_file, events=('start', 'end'))
    new_root = ET.Element("tv")
    
    try:
        _, first_el = next(context)
        if first_el.tag == 'tv':
            new_root.attrib = first_el.attrib
    except StopIteration:
        pass

    channel_count = 0
    prog_count = 0

    print("⚡ Syncing schedules, embedding logos, and forcing Eastern Time...")
    for event, elem in context:
        if event == 'end':
            if elem.tag == 'channel':
                ch_id = elem.get('id')
                if ch_id in target_ids:
                    new_root.append(elem)
                    channel_count += 1
                else:
                    elem.clear()
                    
            elif elem.tag == 'programme':
                p_id = elem.get('channel')
                if p_id in target_ids:
                    start_time = elem.get('start')
                    stop_time = elem.get('stop')
                    
                    # Clean punctuation tails cleanly as strings, avoiding list splitting bugs
                    if start_time:
                        clean_start = re.match(r'^(\d{14})', start_time.strip())
                        if clean_start:
                            elem.set('start', f"{clean_start.group(1)} -0400")
                            
                    if stop_time:
                        clean_stop = re.match(r'^(\d{14})', stop_time.strip())
                        if clean_stop:
                            elem.set('stop', f"{clean_stop.group(1)} -0400")
                    
                    new_root.append(elem)
                    prog_count += 1
                else:
                    elem.clear()

    print(f"📊 Processed {channel_count} channels containing {prog_count} show blocks.")
    
    # Compile files back to directory target
    tree = ET.ElementTree(new_root)
    tree.write(OUTPUT_EPG_PATH, encoding='utf-8', xml_declaration=True)
    print(f"✅ SUCCESS! Guide generated smoothly at: {OUTPUT_EPG_PATH}")

except Exception as e:
    print(f"❌ CRITICAL LOG EXTRACTION FAULT: {e}")