"""
Nyaa.si Scraper pour KINO
Recherche de releases anime en VOSTFR / MULTI sur Nyaa.si via flux RSS standard.
"""

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def search(query: str, media_type: str = "series", year=None, season=None, episode=None):
    results = []
    # Nettoyer et construire la requête
    clean_q = re.sub(r"[^a-zA-Z0-9\s]", " ", query).strip()
    if not clean_q:
        return []

    # Format de recherche anime spécifique si épisode/saison fourni
    search_term = clean_q
    if season and episode:
        search_term = f"{clean_q} E{int(episode):02d}"
    elif episode:
        search_term = f"{clean_q} {int(episode):02d}"

    url = f"https://nyaa.si/?page=rss&q={urllib.parse.quote_plus(search_term)}&c=1_0&f=0"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KINO/1.0"
    })

    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            channel = root.find("channel")
            if channel is None:
                return []

            # Namespace Nyaa pour seeders, infohash, size
            ns = {"nyaa": "https://nyaa.si/xmlns/nyaa"}

            for item in channel.findall("item"):
                title_elem = item.find("title")
                title = title_elem.text if title_elem is not None else ""

                seeders = 0
                s_elem = item.find("nyaa:seeders", ns)
                if s_elem is not None and s_elem.text:
                    try:
                        seeders = int(s_elem.text)
                    except Exception:
                        pass

                size_str = ""
                sz_elem = item.find("nyaa:size", ns)
                if sz_elem is not None and sz_elem.text:
                    size_str = sz_elem.text

                infohash = ""
                ih_elem = item.find("nyaa:infoHash", ns)
                if ih_elem is not None and ih_elem.text:
                    infohash = ih_elem.text.lower()

                magnet = ""
                if infohash:
                    magnet = f"magnet:?xt=urn:btih:{infohash}&dn={urllib.parse.quote(title)}"

                if title and (magnet or infohash):
                    results.append({
                        "name": title,
                        "title": title,
                        "magnet": magnet,
                        "info_hash": infohash,
                        "seeds": seeders,
                        "size": size_str,
                        "source": "Nyaa Anime",
                    })

    except Exception:
        pass

    return results
