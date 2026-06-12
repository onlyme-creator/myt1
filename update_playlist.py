#!/usr/bin/env python3
"""
update_playlist.py
==================
Compiles a structured .m3u IPTV playlist with full EPG metadata (tvg-id,
tvg-name, tvg-logo, group-title) from a curated list of channel sources.

Stream types:
  - STATIC  : URL is permanent; used directly as-is.
  - DYNAMIC : URL is volatile / expires; a scraper function extracts a
              fresh .m3u8 at runtime.  Add a custom extractor below in the
              DYNAMIC EXTRACTORS section for each such channel.

Run locally : python update_playlist.py
GitHub Actions runs this on a cron schedule and commits the result.
"""

import re
import sys
import logging
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OUTPUT_FILE     = "playlist.m3u"
EPG_FILE        = "epg.xml.gz"
EPG_URL         = "https://iptv-org.github.io/epg/guides/us/tvtv.us.epg.xml.gz"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel Registry
# ---------------------------------------------------------------------------
# Each entry is a dict with:
#   display_name  – shown in the IPTV player channel list
#   tvg_id        – iptv-org/database standard ID  (ChannelName.us)
#   tvg_name      – canonical channel name for EPG matching
#   tvg_logo      – publicly hosted PNG/JPG logo URL
#   group_title   – playlist category / group
#   url           – stream URL *or* source page URL (for dynamic streams)
#   dynamic       – False = use url directly; True = call scraper at runtime
# ---------------------------------------------------------------------------
CHANNELS = [

    # ── Local News ──────────────────────────────────────────────────────────
    {
        "display_name": "12 On Your Side Richmond",
        "tvg_id":       "WWBT.us",
        "tvg_name":     "WWBT NBC12",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/NBC_2013_current_logo.svg/320px-NBC_2013_current_logo.svg.png",
        "group_title":  "Local News",
        "url":          "https://amg00312-amg00312c35-amgplt0022.playout.now3.amagi.tv/ts-us-e2-n2/playlist/amg00312-amg00312c35-amgplt0022/playlist.m3u8",
        "dynamic":      False,
    },

    # ── News ────────────────────────────────────────────────────────────────
    {
        "display_name": "CBS News",
        "tvg_id":       "CBSNews.us",
        "tvg_name":     "CBS News",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/CBS_News.svg/320px-CBS_News.svg.png",
        "group_title":  "News",
        "url":          "https://cbsn-us.cbsnstream.cbsnews.com/out/v1/55a8648e8f134e82a470f83d562deeca/master_24.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "CNN",
        "tvg_id":       "CNN.us",
        "tvg_name":     "CNN",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b1/CNN.svg/320px-CNN.svg.png",
        "group_title":  "News",
        "url":          "https://turnerlive.warnermediacdn.com/hls/live/586495/cnngo/cnn_slate/VIDEO_0_3564000.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "MSNBC",
        "tvg_id":       "MSNBC.us",
        "tvg_name":     "MSNBC",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/MSNBC_logo.svg/320px-MSNBC_logo.svg.png",
        "group_title":  "News",
        "url":          "http://41.205.93.154/MSNBC/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    {
        "display_name": "HLN",
        "tvg_id":       "HLN.us",
        "tvg_name":     "HLN",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/HLN_logo_2022.svg/320px-HLN_logo_2022.svg.png",
        "group_title":  "News",
        "url":          "http://23.237.104.106:8080/USA_HLN/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Broadcast ───────────────────────────────────────────────────────────
    {
        "display_name": "ABC",
        "tvg_id":       "ABC.us",
        "tvg_name":     "ABC",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a4/ABC_Entertainment_logo_2021.svg/320px-ABC_Entertainment_logo_2021.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1809/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "CBS",
        "tvg_id":       "CBS.us",
        "tvg_name":     "CBS",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/CBS_logo.svg/320px-CBS_logo.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1810/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "NBC 1",
        "tvg_id":       "NBC.us",
        "tvg_name":     "NBC",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/NBC_2013_current_logo.svg/320px-NBC_2013_current_logo.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1812/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "NBC 2",
        "tvg_id":       "NBC.us",
        "tvg_name":     "NBC",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f9/NBC_2013_current_logo.svg/320px-NBC_2013_current_logo.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1820/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    {
        "display_name": "Fox 1",
        "tvg_id":       "FOX.us",
        "tvg_name":     "Fox",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/FOX_Broadcasting_Company_logo.svg/320px-FOX_Broadcasting_Company_logo.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1833/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Fox 2",
        "tvg_id":       "FOX.us",
        "tvg_name":     "Fox",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/FOX_Broadcasting_Company_logo.svg/320px-FOX_Broadcasting_Company_logo.svg.png",
        "group_title":  "Broadcast",
        "url":          "http://stream.cammonitorplus.net/1752/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Sports ──────────────────────────────────────────────────────────────
    {
        "display_name": "ESPN",
        "tvg_id":       "ESPN.us",
        "tvg_name":     "ESPN",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/ESPN_wordmark.svg/320px-ESPN_wordmark.svg.png",
        "group_title":  "Sports",
        "url":          "http://41.205.93.154/ESPN/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "ESPN2",
        "tvg_id":       "ESPN2.us",
        "tvg_name":     "ESPN2",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/ESPN2_logo.svg/320px-ESPN2_logo.svg.png",
        "group_title":  "Sports",
        "url":          "http://41.223.30.230/ESPN2/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "ESPNU",
        "tvg_id":       "ESPNU.us",
        "tvg_name":     "ESPNU",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/ESPNU_logo.svg/320px-ESPNU_logo.svg.png",
        "group_title":  "Sports",
        "url":          "http://23.237.104.106:8080/USA_ESPNU/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "NBA TV",
        "tvg_id":       "NBATV.us",
        "tvg_name":     "NBA TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/7/7c/NBA_TV.svg/320px-NBA_TV.svg.png",
        "group_title":  "Sports",
        "url":          "http://23.237.104.106:8080/USA_NBA/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "NFL Network",
        "tvg_id":       "NFLNetwork.us",
        "tvg_name":     "NFL Network",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/d/d3/NFL_Network_logo.svg/320px-NFL_Network_logo.svg.png",
        "group_title":  "Sports",
        "url":          "http://23.237.104.106:8080/USA_NFL_NETWORK/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "FS1",
        "tvg_id":       "FS1.us",
        "tvg_name":     "FS1",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Fox_Sports_1_logo.svg/320px-Fox_Sports_1_logo.svg.png",
        "group_title":  "Sports",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/2501.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "FS2",
        "tvg_id":       "FS2.us",
        "tvg_name":     "FS2",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8b/Fox_Sports_2_logo.svg/320px-Fox_Sports_2_logo.svg.png",
        "group_title":  "Sports",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/2500.m3u8",
        "dynamic":      False,
    },

    # ── Entertainment ───────────────────────────────────────────────────────
    {
        "display_name": "A&E",
        "tvg_id":       "AandE.us",
        "tvg_name":     "A&E",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/A%26E_Network_logo.svg/320px-A%26E_Network_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.239.31.26:8989/aande/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "AMC",
        "tvg_id":       "AMC.us",
        "tvg_name":     "AMC",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/AMC_2019_logo.svg/320px-AMC_2019_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.239.31.26:8989/amc/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "BET",
        "tvg_id":       "BET.us",
        "tvg_name":     "BET",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/29/BET_logo_2012.svg/320px-BET_logo_2012.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/647385.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Bravo",
        "tvg_id":       "Bravo.us",
        "tvg_name":     "Bravo",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a3/Bravo_logo_2017.svg/320px-Bravo_logo_2017.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://41.205.93.154/BRAVO/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Comedy Central",
        "tvg_id":       "ComedyCentral.us",
        "tvg_name":     "Comedy Central",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d1/Comedy_Central_logo.svg/320px-Comedy_Central_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_COMEDY_CENTRAL/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "E! Entertainment",
        "tvg_id":       "E!.us",
        "tvg_name":     "E!",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/5/fifty/E%21_logo.svg/320px-E%21_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_E/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "FX",
        "tvg_id":       "FX.us",
        "tvg_name":     "FX",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/FX_International_logo.svg/320px-FX_International_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_FX/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "FXX",
        "tvg_id":       "FXX.us",
        "tvg_name":     "FXX",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/FXX_logo.svg/320px-FXX_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_FXX/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "MTV",
        "tvg_id":       "MTV.us",
        "tvg_name":     "MTV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/eight/MTV_2021_logo.svg/320px-MTV_2021_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_MTV/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Paramount Network",
        "tvg_id":       "ParamountNetwork.us",
        "tvg_name":     "Paramount Network",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Paramount_Network_logo.svg/320px-Paramount_Network_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_PARAMOUNT_NETWORK/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Reelz",
        "tvg_id":       "Reelz.us",
        "tvg_name":     "Reelz",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/Reelz_logo.svg/320px-Reelz_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_REELZ/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Syfy",
        "tvg_id":       "Syfy.us",
        "tvg_name":     "Syfy",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c6/Syfy_logo_2017.svg/320px-Syfy_logo_2017.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_SYFY/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "TBS",
        "tvg_id":       "TBSEast.us",
        "tvg_name":     "TBS East",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bf/TBS_logo_2016.svg/320px-TBS_logo_2016.svg.png",
        "group_title":  "Entertainment",
        "url":          "https://turnerlive.warnermediacdn.com/hls/live/2023172/tbseast/slate/VIDEO_0_3564000.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "TV Land",
        "tvg_id":       "TVLand.us",
        "tvg_name":     "TV Land",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8b/TV_Land_logo_2015.svg/320px-TV_Land_logo_2015.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/614330.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "TNT",
        "tvg_id":       "TNTEast.us",
        "tvg_name":     "TNT East",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/TNT_US_2016.svg/320px-TNT_US_2016.svg.png",
        "group_title":  "Entertainment",
        "url":          "https://turnerlive.warnermediacdn.com/hls/live/2023168/tnteast/slate/VIDEO_0_3564000.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "truTV",
        "tvg_id":       "TruTVEast.us",
        "tvg_name":     "truTV East",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/TruTV_logo_2014.svg/320px-TruTV_logo_2014.svg.png",
        "group_title":  "Entertainment",
        "url":          "https://turnerlive.warnermediacdn.com/hls/live/2023176/trueast/slate/VIDEO_0_3564000.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Vice TV",
        "tvg_id":       "ViceTV.us",
        "tvg_name":     "Vice TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Vice_TV_logo.svg/320px-Vice_TV_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_VICETV/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "VH1",
        "tvg_id":       "VH1.us",
        "tvg_name":     "VH1",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/86/VH1_logo.svg/320px-VH1_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_VH1/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Revolt TV",
        "tvg_id":       "RevoltTV.us",
        "tvg_name":     "Revolt TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Revolt_TV_logo.svg/320px-Revolt_TV_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_REVOLT/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "WE tv",
        "tvg_id":       "WEtv.us",
        "tvg_name":     "WE tv",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/WE_tv_logo.svg/320px-WE_tv_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_WE_TV/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Movies & Premium ────────────────────────────────────────────────────
    {
        "display_name": "Cinemax",
        "tvg_id":       "Cinemax.us",
        "tvg_name":     "Cinemax",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/7/seventyfive/Cinemax_2014.svg/320px-Cinemax_2014.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_CINEMAX/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "HBO",
        "tvg_id":       "HBO.us",
        "tvg_name":     "HBO",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/HBO_logo.svg/320px-HBO_logo.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_HBO/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "HBO2",
        "tvg_id":       "HBO2.us",
        "tvg_name":     "HBO2",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/HBO_logo.svg/320px-HBO_logo.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_HBO2/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "HBO Comedy",
        "tvg_id":       "HBOComedy.us",
        "tvg_name":     "HBO Comedy",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/HBO_logo.svg/320px-HBO_logo.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_HBO_COMEDY/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Showtime",
        "tvg_id":       "Showtime.us",
        "tvg_name":     "Showtime",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e2/Showtime.svg/320px-Showtime.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_SHOWTIME/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Starz",
        "tvg_id":       "Starz.us",
        "tvg_name":     "Starz",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Starz_2016.svg/320px-Starz_2016.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "http://23.237.104.106:8080/USA_STARZ/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "TCM",
        "tvg_id":       "TCMEast.us",
        "tvg_name":     "TCM East",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c0/TCM_Logo_2020.svg/320px-TCM_Logo_2020.svg.png",
        "group_title":  "Movies & Premium",
        "url":          "https://turnerlive.warnermediacdn.com/hls/live/2023186/tcmeast/noslate/VIDEO_1_5128000.m3u8",
        "dynamic":      False,
    },

    # ── Lifestyle & Science ─────────────────────────────────────────────────
    {
        "display_name": "Discovery Channel",
        "tvg_id":       "DiscoveryChannel.us",
        "tvg_name":     "Discovery Channel",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2e/Discovery_Channel_logo.svg/320px-Discovery_Channel_logo.svg.png",
        "group_title":  "Lifestyle & Science",
        "url":          "http://23.237.104.106:8080/USA_DISCOVERY/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "LMN",
        "tvg_id":       "LifetimeMovieNetwork.us",
        "tvg_name":     "Lifetime Movie Network",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Lifetime_Movie_Network_logo.svg/320px-Lifetime_Movie_Network_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_LMN/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "National Geographic",
        "tvg_id":       "NationalGeographic.us",
        "tvg_name":     "National Geographic",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fc/Natgeologo.svg/320px-Natgeologo.svg.png",
        "group_title":  "Lifestyle & Science",
        "url":          "http://23.237.104.106:8080/USA_NAT_GEO/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Entertainment ───────────────────────────────────────────────────────
    {
        "display_name": "Fuse",
        "tvg_id":       "Fuse.us",
        "tvg_name":     "Fuse",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Fuse_TV_logo.svg/320px-Fuse_TV_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_FUSE/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "FXM",
        "tvg_id":       "FXM.us",
        "tvg_name":     "FXM",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/FXM_logo.svg/320px-FXM_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/3736.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Freeform",
        "tvg_id":       "Freeform.us",
        "tvg_name":     "Freeform",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/Freeform_2016_logo.svg/320px-Freeform_2016_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/2502.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Hallmark Channel",
        "tvg_id":       "HallmarkChannel.us",
        "tvg_name":     "Hallmark Channel",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/5/fifty/Hallmark_Channel_logo.svg/320px-Hallmark_Channel_logo.svg.png",
        "group_title":  "Entertainment",
        "url":          "http://23.237.104.106:8080/USA_HALLMARK/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Lifestyle & Science ─────────────────────────────────────────────────
    {
        "display_name": "National Geographic Wild",
        "tvg_id":       "NatGeoWild.us",
        "tvg_name":     "National Geographic Wild",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0e/Nat_Geo_Wild_logo.svg/320px-Nat_Geo_Wild_logo.svg.png",
        "group_title":  "Lifestyle & Science",
        "url":          "http://23.237.104.106:8080/USA_NAT_GEO_WILD/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Animal Planet",
        "tvg_id":       "AnimalPlanet.us",
        "tvg_name":     "Animal Planet",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/twenty/Animal_Planet_logo_2018.svg/320px-Animal_Planet_logo_2018.svg.png",
        "group_title":  "Lifestyle & Science",
        "url":          "http://23.237.104.106:8080/USA_ANIMAL_PLANET/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Crime & Investigation ───────────────────────────────────────────────
    {
        "display_name": "Court TV",
        "tvg_id":       "CourtTV.us",
        "tvg_name":     "Court TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b9/Court_TV_logo_2019.svg/320px-Court_TV_logo_2019.svg.png",
        "group_title":  "Crime & Investigation",
        "url":          "https://cdn-uw2-prod.tsv2.amagi.tv/linear/amg01438-ewscrippscompan-courttv-tablo/playlist.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Charge! TV",
        "tvg_id":       "ChargeTV.us",
        "tvg_name":     "Charge! TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/3/thirty/Charge_TV_logo.svg/320px-Charge_TV_logo.svg.png",
        "group_title":  "Crime & Investigation",
        "url":          "http://kytv.xyz/live/20022002/20022002/1407188.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Investigation Discovery",
        "tvg_id":       "InvestigationDiscovery.us",
        "tvg_name":     "Investigation Discovery",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/forty/Investigation_Discovery_logo.svg/320px-Investigation_Discovery_logo.svg.png",
        "group_title":  "Crime & Investigation",
        "url":          "http://kytv.xyz/live/20022002/20022002/175883.m3u8",
        "dynamic":      False,
    },

    # ── Kids ────────────────────────────────────────────────────────────────
    {
        "display_name": "Boomerang",
        "tvg_id":       "Boomerang.us",
        "tvg_name":     "Boomerang",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Boomerang_2015_logo.svg/320px-Boomerang_2015_logo.svg.png",
        "group_title":  "Kids",
        "url":          "http://23.237.104.106:8080/USA_BOOMERANG/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Disney Junior",
        "tvg_id":       "DisneyJunior.us",
        "tvg_name":     "Disney Junior",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/eight/Disney_Junior_logo.svg/320px-Disney_Junior_logo.svg.png",
        "group_title":  "Kids",
        "url":          "https://hlsr-app.vercel.app/api/proxy?url=http://23.237.104.106:8080/USA_DISNEY_JUNIOR/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Disney XD",
        "tvg_id":       "DisneyXD.us",
        "tvg_name":     "Disney XD",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/8/eight/Disney_XD_2015_logo.svg/320px-Disney_XD_2015_logo.svg.png",
        "group_title":  "Kids",
        "url":          "http://23.237.104.106:8080/USA_DISNEY_XD/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },

    # ── Free & Broadcast ────────────────────────────────────────────────────
    {
        "display_name": "Bounce TV",
        "tvg_id":       "BounceTV.us",
        "tvg_name":     "Bounce TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/e/ea/Bounce_TV_logo.svg/320px-Bounce_TV_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "http://23.237.104.106:8080/USA_BOUNCE/tracks-v1a1/mono.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "BUZZR",
        "tvg_id":       "BUZZR.us",
        "tvg_name":     "BUZZR",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/c/ca/Buzzr_logo.svg/320px-Buzzr_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "https://buzzrota-web.amagi.tv/1080p-vtt/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Comet TV",
        "tvg_id":       "CometTV.us",
        "tvg_name":     "Comet TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/b/b1/Comet_%28TV_network%29_logo.svg/320px-Comet_%28TV_network%29_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "https://fast-channels.sinclairstoryline.com/COMET/index_1.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Cozi TV",
        "tvg_id":       "CoziTV.us",
        "tvg_name":     "Cozi TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/en/thumb/3/thirty/Cozi_TV_logo.svg/320px-Cozi_TV_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "http://173.225.32.123/Cozi-2358/tracks-v1a1/mono.ts.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Ebony TV",
        "tvg_id":       "EbonyTV.us",
        "tvg_name":     "Ebony TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/4/forty/Ebony_magazine_logo.svg/320px-Ebony_magazine_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "https://amg00353-amg00353c34-lg-us-3767.playouts.now.amagi.tv/ts-us-e2-n2/playlist/amg00353-lionsgatetvfast-ebonytv-lgus/cb573b1d6573618984cb376fcef44382847d3dd50e2fd63471a9448f1298410ddafbb2d3932fff695c811fc060036d555879cc1bd77d0ad7b923f6573077073c9c9cab6ba8d95266c0814b747ff0390157fa1e738567107bd6e7555ad473e6344d41b9473d1862c4f02ef43258c780088e7e2988acc0303a2c5e5b97095fe3f24cb7ade25e40d039a8e2014e967d44fa83439fd501e9d6805d8316847fa3459a95a48c8515d7e42f52937f8ab5a2c54007fbb54ad1a468b8d38e6c3ddc423c470a50f5d1eb942c12a7378ed8659db1d24bf001da8165a06d32a9099b4efe5fe28727cac5932692a4b8d74f4e37a0862809972278c2f8990312c70ecdabfbda7a7290f75e1f2dac3f8b70796dc2b4a371f584d57f66a224964964c4935391cc9d8123af7ce900ec04c7c2e6913e43014d0115b964ad8866e1d7bc12a7e51c25550216fcd40684ceee573e664091931efa275be88bc5d3915f629a344e14bf32800a5e2cfb3d622ab176b9826a38c0662b952502d951218e47efab63a3f39bd30329a6d7c84cb03dbefb599fbe50f8236f1752fabf0b5651162f80bc335c602a9208e580cbb5235104bea1cf8c80395230301956039cf1a721cbfcb2072956900368303a414ec65083278852105154a05cb4880c678e122e3decc5660eba0d43234f30e1351096dba82a1a182718dba409ca7c50/93/1920x1080_8048040/index.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Game Show Central",
        "tvg_id":       "GameShowNetwork.us",
        "tvg_name":     "Game Show Network",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/3/thirty/Game_Show_Network_logo.svg/320px-Game_Show_Network_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "https://jmp2.uk/plu-5e54187aae660e00093561d6.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "Grit TV",
        "tvg_id":       "GritTV.us",
        "tvg_name":     "Grit TV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Grit_%28TV_network%29_logo.svg/320px-Grit_%28TV_network%29_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/40583.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "getTV",
        "tvg_id":       "getTV.us",
        "tvg_name":     "getTV",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/GetTV_logo.svg/320px-GetTV_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "http://s.rocketdns.info:8080/live/monstercable/Dq6jjknxCr/30928.m3u8",
        "dynamic":      False,
    },
    {
        "display_name": "ION Television",
        "tvg_id":       "ION.us",
        "tvg_name":     "ION Television",
        "tvg_logo":     "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Ion_Television_2020_logo.svg/320px-Ion_Television_2020_logo.svg.png",
        "group_title":  "Free & Broadcast",
        "url":          "https://pb-xhb3ic8b61whm.akamaized.net/Ion_US_6.m3u8",
        "dynamic":      False,
    },

]

# ---------------------------------------------------------------------------
# DYNAMIC EXTRACTORS
# ---------------------------------------------------------------------------
# If a channel has dynamic=True, add a function here named after its tvg_id
# (dots replaced by underscores).  It must return a valid .m3u8 URL string,
# or None on failure.
#
# Example skeleton:
#
# def extract_ExampleChannel_us() -> str | None:
#     headers = {"User-Agent": USER_AGENT}
#     try:
#         r = requests.get("https://example.com/live",
#                          headers=headers, timeout=REQUEST_TIMEOUT)
#         r.raise_for_status()
#         match = re.search(r'(https?://[^"\']+\.m3u8)', r.text)
#         return match.group(1) if match else None
#     except Exception as exc:
#         log.error("Extractor failed: %s", exc)
#         return None
#
# Then register it:
# EXTRACTORS = {"ExampleChannel_us": extract_ExampleChannel_us}
# ---------------------------------------------------------------------------

EXTRACTORS: dict = {}

# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------
DEAD_FILE   = "dead_channels.txt"   # committed to repo each run
REPORT_FILE = "health_report.txt"   # committed to repo each run

# ---------------------------------------------------------------------------
# Core logic  (do not edit below unless you know what you're doing)
# ---------------------------------------------------------------------------

def resolve_url(channel: dict) -> str | None:
    """Return the stream URL for a channel, running a scraper if dynamic."""
    if not channel["dynamic"]:
        return channel["url"]
    key = channel["tvg_id"].replace(".", "_")
    extractor = EXTRACTORS.get(key)
    if extractor is None:
        log.warning("No extractor for dynamic channel '%s'. Skipping.", channel["display_name"])
        return None
    log.info("Running extractor for '%s' ...", channel["display_name"])
    url = extractor()
    if url:
        log.info("  → %s", url)
    else:
        log.warning("  → Extractor returned no URL for '%s'.", channel["display_name"])
    return url


def check_url(url: str, name: str) -> bool:
    """
    Return True if the URL responds with a success status code.
    Tries HEAD first (fast), falls back to GET if HEAD is rejected.
    A channel is considered ALIVE if the server responds at all with
    a non-5xx code — 401/403 still means the server is up.
    """
    headers = {"User-Agent": USER_AGENT}
    for method in ("HEAD", "GET"):
        try:
            r = requests.request(
                method, url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=(method == "GET"),
            )
            alive = r.status_code < 500
            log.info("  [%s] %s → HTTP %d (%s)",
                     method, name, r.status_code, "✅ alive" if alive else "❌ dead")
            return alive
        except requests.exceptions.Timeout:
            log.warning("  [%s] %s → timeout", method, name)
        except requests.exceptions.ConnectionError:
            log.warning("  [%s] %s → connection error", method, name)
        except Exception as exc:
            log.warning("  [%s] %s → %s", method, name, exc)
    return False


def build_extinf(channel: dict) -> str:
    return (
        f'#EXTINF:-1 '
        f'tvg-id="{channel["tvg_id"]}" '
        f'tvg-name="{channel["tvg_name"]}" '
        f'tvg-logo="{channel["tvg_logo"]}" '
        f'group-title="{channel["group_title"]}",'
        f'{channel["display_name"]}'
    )


def compile_playlist(channels: list) -> tuple[str, list, list]:
    """
    Build the playlist string.
    Returns (playlist_content, alive_channels, dead_channels).
    Dead channels are excluded from the playlist but their definitions
    remain untouched in CHANNELS so they auto-recover on the next run.
    """
    lines   = ["#EXTM3U url-tvg=\"epg.xml.gz\" x-tvg-url=\"epg.xml.gz\""]
    alive   = []
    dead    = []

    for ch in channels:
        url = resolve_url(ch)
        if not url:
            dead.append(ch)
            continue

        log.info("Checking '%s' ...", ch["display_name"])
        if check_url(url, ch["display_name"]):
            lines.append("")
            lines.append(build_extinf(ch))
            lines.append(url)
            alive.append(ch)
        else:
            log.warning("  ⚠️  '%s' is DEAD — excluded from playlist.", ch["display_name"])
            dead.append(ch)

    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("✅  Alive : %d channels", len(alive))
    log.info("❌  Dead  : %d channels", len(dead))
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines) + "\n", alive, dead


def write_playlist(content: str, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    log.info("Playlist written → %s", path)


def write_dead_channels(dead: list, path: str) -> None:
    """Write dead_channels.txt — empty file if all channels are alive."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Dead channels as of {now}\n")
        fh.write(f"# These are automatically excluded from playlist.m3u\n")
        fh.write(f"# They will reappear automatically if they come back online\n\n")
        if dead:
            for ch in dead:
                fh.write(f"[{ch['group_title']}] {ch['display_name']}\n")
                fh.write(f"  url: {ch.get('url', 'dynamic')}\n\n")
        else:
            fh.write("All channels are alive! 🎉\n")
    log.info("Dead channels log written → %s", path)


def write_health_report(alive: list, dead: list, path: str) -> None:
    """Write a human-readable health_report.txt summary."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(alive) + len(dead)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"IPTV Playlist Health Report\n")
        fh.write(f"Generated : {now}\n")
        fh.write(f"{'━' * 40}\n\n")
        fh.write(f"Total channels : {total}\n")
        fh.write(f"✅ Alive        : {len(alive)}\n")
        fh.write(f"❌ Dead         : {len(dead)}\n\n")

        if dead:
            fh.write(f"{'━' * 40}\n")
            fh.write(f"DEAD CHANNELS ({len(dead)}):\n\n")
            for ch in dead:
                fh.write(f"  ❌ [{ch['group_title']}] {ch['display_name']}\n")
                fh.write(f"     {ch.get('url', 'dynamic scraper')}\n\n")

        fh.write(f"{'━' * 40}\n")
        fh.write(f"ALIVE CHANNELS ({len(alive)}):\n\n")
        for ch in alive:
            fh.write(f"  ✅ [{ch['group_title']}] {ch['display_name']}\n")

    log.info("Health report written → %s", path)


def download_epg(url: str, path: str) -> bool:
    """
    Download the public US EPG (XMLTV) file and save it as epg.xml.gz
    in the repository root.  Returns True on success, False on failure.
    The playlist will still work without it — EPG is best-effort.
    """
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("📡  Downloading EPG from:")
    log.info("    %s", url)
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=60,          # EPG files can be large — give it time
            stream=True,
        )
        r.raise_for_status()
        total = 0
        with open(path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
        log.info("✅  EPG saved → %s  (%.1f KB)", path, total / 1024)
        return True
    except requests.exceptions.Timeout:
        log.warning("❌  EPG download timed out — skipping.")
    except requests.exceptions.ConnectionError as exc:
        log.warning("❌  EPG connection error: %s — skipping.", exc)
    except requests.exceptions.HTTPError as exc:
        log.warning("❌  EPG HTTP error: %s — skipping.", exc)
    except Exception as exc:
        log.warning("❌  EPG download failed: %s — skipping.", exc)
    return False


if __name__ == "__main__":
    playlist, alive, dead = compile_playlist(CHANNELS)
    write_playlist(playlist, OUTPUT_FILE)
    write_dead_channels(dead, DEAD_FILE)
    write_health_report(alive, dead, REPORT_FILE)
    download_epg(EPG_URL, EPG_FILE)
    # Exit code 0 always — dead channels are expected and handled gracefully
    sys.exit(0)
