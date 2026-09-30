#!/usr/bin/env python3
"""
RD CineHub CLI — Version Terminal Interactive avec Streaming MPV & Auto-Fallback anti-451
"""

import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app import (
    DEFAULT_DOWNLOAD_DIR,
    HEADERS,
    find_mpv,
    format_size,
    launch_mpv,
    load_config,
    rd_check_torrent,
    rd_debrid_magnet,
    rd_get_user,
    save_config,
    search_apibay,
    search_cinemeta,
    search_torrentio,
)


def download_cli(url: str, dest_dir: Path, filename: str):
    dest_dir.mkdir(parents=True, exist_ok=True)
    filepath = dest_dir / filename
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        start_time = time.time()
        print(f"\n📥 Téléchargement dans : {filepath}")
        with open(filepath, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.time() - start_time, 0.1)
                speed = downloaded / elapsed
                if total:
                    pct = downloaded * 100 / total
                    sys.stdout.write(
                        f"\r   [{pct:5.1f}%] {format_size(downloaded)} / {format_size(total)} ({format_size(speed)}/s)   "
                    )
                else:
                    sys.stdout.write(f"\r   {format_size(downloaded)} ({format_size(speed)}/s)   ")
                sys.stdout.flush()
    print("\n✅ Téléchargement terminé !")


def ensure_token():
    cfg = load_config()
    token = cfg.get("rd_token", "").strip()
    if not token:
        print("\n🔑 Configuration initiale Real-Debrid")
        print("👉 Récupérez votre Token API sur : https://real-debrid.com/apitoken")
        token = input("Collez votre Token API Real-Debrid : ").strip()
        if not token:
            sys.exit("❌ Token obligatoire.")
        save_config({"rd_token": token})
    return token


def torrent_matches_words(t, words):
    hay = " ".join([
        t.get("source", ""),
        t.get("title", ""),
        t.get("meta", ""),
        " ".join(t.get("qualities", [])),
        " ".join(t.get("langs", [])),
    ]).lower()
    return all(w in hay for w in words)


def pick_torrents_with_fallback(torrents):
    if not torrents:
        print("❌ Aucun torrent trouvé.")
        return []

    flt = input(
        f"\n🔎 {len(torrents)} torrents trouvés. Filtrer (ex: 'multi 4k', 'fr 1080p' ou Entrée pour tout voir) : "
    ).strip().lower()
    if flt:
        words = [w for w in flt.split() if w]
        filtered = [t for t in torrents if torrent_matches_words(t, words)]
        if filtered:
            torrents = filtered
            print(f"✅ {len(torrents)} torrents correspondent au filtre '{flt}'.")
        else:
            print("⚠️ Aucun résultat pour ce filtre, affichage complet.")

    shown = torrents[:35]
    print("\n🧲 Liste des torrents :")
    for i, t in enumerate(shown, 1):
        tags = " ".join(t.get("qualities", []) + t.get("langs", []))
        tag_str = f"[{tags}] " if tags else ""
        print(f"  {i:2d}. [{t['source']}] {tag_str}{t['title']}")
        if t.get("meta"):
            print(f"      └─ {t['meta']}")

    sel = input(
        f"\nChoisissez le torrent [1-{len(shown)}, défaut=1] (bascule auto si bloqué DMCA 451) : "
    ).strip() or "1"
    if not sel.isdigit() or not (1 <= int(sel) <= len(shown)):
        return []
    start_idx = int(sel) - 1
    return shown[start_idx:]


def debrid_with_auto_fallback(token, candidate_torrents, season=None, episode=None):
    for idx, t in enumerate(candidate_torrents, 1):
        title = t.get("title", "Magnet")
        print(f"\n⏳ Essai #{idx} sur Real-Debrid : {title[:75]}...")
        try:
            res = rd_debrid_magnet(
                token,
                t["magnet"],
                season=season,
                episode=episode,
                resolve_url=t.get("resolve_url"),
            )
            return res
        except Exception as e:
            err_str = str(e)
            if "451" in err_str or "infringing_file" in err_str:
                print("   ⚠️ Bloqué par DMCA sur Real-Debrid (Erreur 451: infringing_file) -> passage automatique au suivant !")
                continue
            else:
                print(f"   ⚠️ Échec sur ce torrent ({err_str}) -> passage automatique au suivant !")
                continue
    return None


def main():
    token = ensure_token()
    cfg = load_config()
    download_dir = Path(cfg.get("download_dir", str(DEFAULT_DOWNLOAD_DIR)))
    mpv_bin = find_mpv()

    try:
        user = rd_get_user(token)
        days = int(user.get("premium", 0)) // 86400
        user_str = f"{user.get('username')} ({days}j Premium)"
    except Exception:
        user_str = "Token non vérifié"

    print("=" * 66)
    print(f"⚡ RD CineHub CLI — Connecté : {user_str}")
    print(f"📂 Dossier d'installation : {download_dir}")
    print(f"🎬 Lecteur MPV : {mpv_bin or 'Non trouvé'}")
    print("=" * 66)
    print("  1. 🎬 Chercher un Film")
    print("  2. 📺 Chercher une Série")
    print("  3. 🔍 Recherche libre par mots-clés")
    print("  4. 🧲 Coller directement un lien Magnet")
    print("  5. ⚙️  Modifier le Token ou le Dossier de téléchargement")
    print("=" * 66)

    choice = input("Votre choix [1-5] : ").strip()

    if choice == "5":
        new_tok = input("Nouveau Token RD (Entrée pour ne pas changer) : ").strip()
        new_dir = input(f"Nouveau dossier [{download_dir}] : ").strip()
        updates = {}
        if new_tok:
            updates["rd_token"] = new_tok
        if new_dir:
            updates["download_dir"] = new_dir
        save_config(updates)
        print("✅ Configuration sauvegardée.")
        return

    season, episode = None, None
    if choice == "4":
        magnet = input("\nCollez le lien magnet : ").strip()
        candidates = [{"title": "Magnet manuel", "magnet": magnet}]
    elif choice == "3":
        q = input("\nRecherche libre (ex: Dune 2024 MULTI 1080p) : ").strip()
        candidates = pick_torrents_with_fallback(search_apibay(q))
    else:
        mtype = "series" if choice == "2" else "movie"
        q = input(f"\nTitre {'de la série' if mtype == 'series' else 'du film'} : ").strip()
        if not q:
            return
        metas = search_cinemeta(q, mtype)
        if not metas:
            print("⚠️ Aucun résultat catalogue, recherche libre...")
            candidates = pick_torrents_with_fallback(search_apibay(q))
        else:
            print("\n📋 Catalogue :")
            for idx, m in enumerate(metas[:10], 1):
                print(f"  {idx:2d}. {m.get('name')} ({m.get('releaseInfo', '?')})")
            idx_sel = int(input("\nNuméro [1] : ").strip() or "1") - 1
            media = metas[idx_sel]
            if mtype == "series":
                season = input("Saison [1] : ").strip() or "1"
                episode = input("Épisode [1] : ").strip() or "1"
            print(f"\n⏳ Recherche des torrents pour {media.get('name')}...")
            torrents = search_torrentio(media["id"], mtype, season or 1, episode or 1)
            if not torrents:
                torrents = search_apibay(media.get("name", q))
            candidates = pick_torrents_with_fallback(torrents)

    if not candidates:
        return

    res = debrid_with_auto_fallback(token, candidates, season=season, episode=episode)
    if not res:
        print("\n❌ Tous les torrents testés ont été refusés par Real-Debrid.")
        return

    while not res.get("ready"):
        sys.stdout.write(
            f"\r🔄 Real-Debrid met en cache le torrent : {res.get('progress')}% ({res.get('speed')})..."
        )
        sys.stdout.flush()
        time.sleep(2.5)
        res = rd_check_torrent(token, res["torrent_id"], season=season, episode=episode)

    files = res.get("files", [])
    print(f"\n\n✅ {len(files)} fichier(s) débridé(s) sur Real-Debrid :")
    for i, f in enumerate(files, 1):
        star = "🎯 [Épisode demandé] " if f.get("is_target_ep") else ""
        print(f"  {i:2d}. {star}{f['filename']} ({f['filesize']})")
        print(f"      🔗 {f['download']}")

    target_file = files[0]
    if len(files) > 1:
        f_idx = input(f"\nQuel fichier lancer/installer ? [1-{len(files)}, défaut=1] : ").strip() or "1"
        if f_idx.isdigit():
            target_file = files[int(f_idx) - 1]

    print("\nAction :")
    print("  [1] ▶️  Streamer en direct dans MPV (4K HDR / Multi-Audio)")
    print("  [2] 💾 Télécharger / Installer dans mon dossier PC")
    print("  [3] 👋 Quitter")
    act = input("Choix [1] : ").strip() or "1"

    if act == "1":
        try:
            used_mpv = launch_mpv(target_file["download"], target_file["filename"])
            print(f"\n🎬 Lecture lancée dans MPV ({used_mpv}) !")
            print("💡 Raccourcis MPV utiles : [#] Changer piste Audio (VF/VO) | [j] Sous-titres | [f] Plein écran")
        except Exception as e:
            print(f"⚠️ {e}\nLien direct : {target_file['download']}")
    elif act == "2":
        download_cli(target_file["download"], download_dir, target_file["filename"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Annulé.")
