#!/usr/bin/env python3
"""deezer_tool.py — CLI Deezer sans token, concu pour etre appele par Claude Code.

Contrat tool-call :
  * stdout = JSON pur (machine-readable), stderr = progression/erreurs
  * code retour 0 si OK, 1 si erreur fatale
  * stdlib uniquement, aucune dependance

Sous-commandes :
  profile [USER_ID] [--lastfm KEY] [--outdir DIR]
      Dump complet (CSV) + profil de gouts compact (JSON) -> stdout.
  verify --input FICHIER | --tracks "Titre - Artiste; Titre - Artiste"
      Verifie que des titres proposes (par une IA) existent sur Deezer.
      Sortie : lien, preview 30s, statut found/not_found/approx.
  enrich [USER_ID] [--max N]
      Donnees track-level des favoris : bpm, gain, isrc, date de sortie.

USER_ID est optionnel : a defaut, DEEZER_USER_ID est lu depuis l'environnement
ou un fichier .env (voir .env.example).
  trends [--genre-id N] [--limit N]
      Charts Deezer (0 = tous genres). Ex : 116 = Rap/Hip Hop.
  releases [--genre-id N] [--limit N]
      Nouvelles sorties editoriales par genre.
  search QUERY [--bpm-min N] [--bpm-max N] [--dur-min N] [--dur-max N]
             [--strict] [--order ORDER] [--limit N]
      Recherche avancee (filtres artist:/track:/album:/label: acceptes dans QUERY).

Exemples :
  python deezer_tool.py profile            # DEEZER_USER_ID depuis .env
  python deezer_tool.py verify --tracks "Exutoire - Damso; Tricheur - Nekfeu"
  python deezer_tool.py search 'artist:"damso"' --bpm-min 130 --strict
  python deezer_tool.py trends --genre-id 116 --limit 20
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

API = "https://api.deezer.com"
PAUSE = 0.12  # rate limit Deezer : 50 req / 5 s


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_dotenv():
    """Charge un .env (KEY=VALUE) sans ecraser l'environnement existant.
    Cherche dans le cwd puis dans le dossier du script."""
    candidates = [os.path.join(os.getcwd(), ".env"),
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
        return


def resolve_user_id(args):
    uid = args.user_id or os.environ.get("DEEZER_USER_ID", "")
    if not uid:
        out_json({"error": "USER_ID manquant : passer en argument ou definir "
                           "DEEZER_USER_ID (env ou fichier .env)"})
        sys.exit(1)
    return uid


def fetch(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def dz(url):
    data = fetch(url)
    time.sleep(PAUSE)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"{url}: {data['error'].get('message')}")
    return data


def paginate(url):
    while url:
        try:
            data = dz(url)
        except RuntimeError as e:
            log(f"  !! {e}")
            return
        yield from data.get("data", [])
        url = data.get("next")


def out_json(obj):
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1)
    print()


def year(ts):
    return time.strftime("%Y", time.localtime(ts)) if ts else "?"


def month(ts):
    return time.strftime("%Y-%m", time.localtime(ts)) if ts else "?"


# ---------------------------------------------------------------- profile
def cmd_profile(args):
    uid, outdir = resolve_user_id(args), args.outdir
    os.makedirs(outdir, exist_ok=True)

    log("Favoris...")
    favs = list(paginate(f"{API}/user/{uid}/tracks?limit=200"))
    log(f"  {len(favs)}")

    log("Playlists...")
    pl_tracks = {}
    for pl in paginate(f"{API}/user/{uid}/playlists?limit=100"):
        pl_tracks[pl["title"]] = list(
            paginate(f"{API}/playlist/{pl['id']}/tracks?limit=200"))
        log(f"  {pl['title']}: {len(pl_tracks[pl['title']])}")

    log("Artistes / albums suivis...")
    fav_artists = [a["name"] for a in paginate(f"{API}/user/{uid}/artists?limit=100")]
    fav_albums = [f"{a['title']} - {a['artist']['name']}"
                  for a in paginate(f"{API}/user/{uid}/albums?limit=100")]

    all_tracks = favs + [t for ts in pl_tracks.values() for t in ts]
    n = len(all_tracks)
    if not n:
        out_json({"error": "aucun titre public trouve", "user_id": uid})
        return 1

    log("Albums (genre + date de sortie)...")
    genre_names = {g["id"]: g["name"] for g in fetch(f"{API}/genre")["data"]}
    album_ids = {t["album"]["id"] for t in all_tracks if t.get("album")}
    album_info = {}
    for i, aid in enumerate(album_ids):
        try:
            alb = dz(f"{API}/album/{aid}")
            gid = alb.get("genre_id", -1)
            gname = genre_names.get(gid)
            if not gname:
                gdata = alb.get("genres", {}).get("data", [])
                gname = gdata[0]["name"] if gdata else "inconnu"
            album_info[aid] = {"genre": gname,
                               "release": alb.get("release_date", "")[:4]}
        except Exception:
            album_info[aid] = {"genre": "inconnu", "release": ""}
        if i and i % 50 == 0:
            log(f"  {i}/{len(album_ids)}")

    def genre_of(t):
        return album_info.get(t.get("album", {}).get("id"), {}).get("genre", "inconnu")

    def release_of(t):
        return album_info.get(t.get("album", {}).get("id"), {}).get("release", "")

    artist_count = Counter(t["artist"]["name"] for t in all_tracks)
    artist_ids = {t["artist"]["name"]: t["artist"]["id"] for t in all_tracks}

    log(f"Artistes ({len(artist_ids)}) : nb_fans...")
    artist_fans = {}
    for i, (name, aid) in enumerate(artist_ids.items()):
        try:
            artist_fans[name] = dz(f"{API}/artist/{aid}").get("nb_fans", 0)
        except Exception:
            artist_fans[name] = 0
        if i and i % 50 == 0:
            log(f"  {i}/{len(artist_ids)}")

    top_artists = [a for a, _ in artist_count.most_common(40)]
    log(f"Top {len(top_artists)} artistes : hits + lies...")
    liked_by_artist = defaultdict(set)
    for t in all_tracks:
        liked_by_artist[t["artist"]["name"]].add(t["id"])

    deep_cut_stats, related_counter = {}, Counter()
    library_artists = set(artist_count) | set(fav_artists)
    for name in top_artists:
        aid = artist_ids[name]
        try:
            top_ids = {t["id"] for t in dz(f"{API}/artist/{aid}/top?limit=10")["data"]}
            liked = liked_by_artist[name]
            deep_cut_stats[name] = round(1 - len(liked & top_ids) / len(liked), 2) if liked else 0
        except Exception:
            pass
        try:
            for rel in dz(f"{API}/artist/{aid}/related?limit=15")["data"]:
                if rel["name"] not in library_artists:
                    related_counter[rel["name"]] += 1
        except Exception:
            pass

    tag_cloud = Counter()
    if args.lastfm:
        log("Tags Last.fm...")
        for i, name in enumerate(artist_ids):
            q = urllib.parse.urlencode({"method": "artist.gettoptags", "artist": name,
                                        "api_key": args.lastfm, "format": "json",
                                        "autocorrect": 1})
            try:
                tags = fetch(f"https://ws.audioscrobbler.com/2.0/?{q}") \
                    .get("toptags", {}).get("tag", [])[:5]
                for tg in tags:
                    tag_cloud[tg["name"].lower()] += artist_count[name]
            except Exception:
                pass
            time.sleep(0.25)
            if i and i % 50 == 0:
                log(f"  {i}/{len(artist_ids)}")

    # signaux internes
    fav_keys = {(t["title"], t["artist"]["name"]) for t in favs}
    pl_keys = {(t["title"], t["artist"]["name"])
               for ts in pl_tracks.values() for t in ts}
    double_engagement = sorted(fav_keys & pl_keys)
    add_months = Counter(month(t.get("time_add")) for t in favs if t.get("time_add"))
    gaps = sorted(int(year(t["time_add"])) - int(release_of(t))
                  for t in favs
                  if release_of(t).isdigit() and t.get("time_add"))
    decades = Counter(f"{release_of(t)[:3]}0s" for t in all_tracks
                      if release_of(t).isdigit())

    # CSV
    csv_path = os.path.join(outdir, f"deezer_{uid}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["track_id", "titre", "artiste", "album", "genre", "annee_sortie",
                    "source", "duree_s", "ajoute_le", "popularite_rank"])
        for t in favs:
            w.writerow([t["id"], t["title"], t["artist"]["name"], t["album"]["title"],
                        genre_of(t), release_of(t), "favoris", t["duration"],
                        year(t.get("time_add")), t.get("rank", "")])
        for name, ts in pl_tracks.items():
            for t in ts:
                w.writerow([t["id"], t["title"], t["artist"]["name"], t["album"]["title"],
                            genre_of(t), release_of(t), f"playlist: {name}",
                            t["duration"], year(t.get("time_add")), t.get("rank", "")])

    genre_count = Counter(genre_of(t) for t in all_tracks)
    by_year = defaultdict(Counter)
    for t in favs:
        by_year[year(t.get("time_add"))][genre_of(t)] += 1
    ranks = sorted(t.get("rank", 0) for t in all_tracks if t.get("rank"))
    fans_sorted = sorted(v for v in artist_fans.values() if v)

    profile = {
        "resume": {
            "titres_total": n,
            "titres_uniques": len({(t['title'], t['artist']['name']) for t in all_tracks}),
            "favoris": len(favs),
            "playlists": {k: len(v) for k, v in pl_tracks.items()},
            "note_playlists": "les noms de playlists sont des labels de contexte d'ecoute",
            "duree_moyenne_s": round(sum(t["duration"] for t in all_tracks) / n),
            "part_explicit": round(sum(1 for t in all_tracks if t.get("explicit_lyrics")) / n, 2),
            "popularite_mediane_rank": ranks[len(ranks) // 2] if ranks else None,
            "nb_fans_median_artistes": fans_sorted[len(fans_sorted) // 2] if fans_sorted else None,
            "note_niche": "rank 0-1M et nb_fans eleves = mainstream ; bas = niche",
        },
        "top_artistes": [{"artiste": a, "titres": c, "fans_deezer": artist_fans.get(a),
                          "ratio_deep_cuts": deep_cut_stats.get(a)}
                         for a, c in artist_count.most_common(40)],
        "note_deep_cuts": "1.0 = ne like jamais les hits de l'artiste ; 0.0 = uniquement les hits",
        "artistes_suivis": fav_artists,
        "albums_favoris": fav_albums,
        "genres": [{"genre": g, "part": round(c / n, 2)}
                   for g, c in genre_count.most_common(15)],
        "tags_lastfm": [{"tag": t, "poids": w} for t, w in tag_cloud.most_common(30)]
                       if tag_cloud else "non collecte (--lastfm absent)",
        "decennies_de_sortie": dict(decades.most_common()),
        "ecart_median_sortie_vs_like_annees": gaps[len(gaps) // 2] if gaps else None,
        "note_ecart": "0-1 = chasseur de nouveautes ; eleve = back-catalogue",
        "evolution_par_annee": {y: dict(c.most_common(5))
                                for y, c in sorted(by_year.items())},
        "phases_ajouts_top_mois": dict(add_months.most_common(10)),
        "double_engagement": [f"{t} - {a}" for t, a in double_engagement],
        "note_double_engagement": "titres en favoris ET en playlist : ponderer plus fort",
        "zone_exploration": [{"artiste": a, "liens_avec_bibliotheque": c}
                             for a, c in related_counter.most_common(25)],
        "note_exploration": "artistes proches du profil mais jamais likes : candidats decouverte",
        "echantillon_recent": [f"{t['title']} - {t['artist']['name']}"
                               for t in sorted(favs, key=lambda t: t.get("time_add", 0),
                                               reverse=True)[:40]],
        "fichiers": {"csv": csv_path},
    }
    json_path = os.path.join(outdir, f"deezer_profile_{uid}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=1)
    profile["fichiers"]["json"] = json_path

    out_json(profile)
    return 0


# ---------------------------------------------------------------- verify
def parse_track_lines(lines):
    """Accepte 'Titre - Artiste' (un par ligne ou separes par ';')."""
    items = []
    for raw in lines:
        for part in raw.split(";"):
            part = part.strip().lstrip("-*0123456789. ").strip()
            if not part:
                continue
            if " - " in part:
                title, artist = part.rsplit(" - ", 1)
            else:
                title, artist = part, ""
            items.append({"titre": title.strip(), "artiste": artist.strip()})
    return items


def search_track(title, artist, strict=True):
    q = f'track:"{title}"' + (f' artist:"{artist}"' if artist else "")
    url = f"{API}/search?q={urllib.parse.quote(q)}" + ("&strict=on" if strict else "")
    return dz(url).get("data", [])


def cmd_verify(args):
    lines = []
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            lines = f.readlines()
    if args.tracks:
        lines.append(args.tracks)
    wanted = parse_track_lines(lines)
    if not wanted:
        out_json({"error": "aucun titre fourni (--input ou --tracks)"})
        return 1

    results = []
    for w in wanted:
        log(f"Verif : {w['titre']} - {w['artiste']}")
        status, best = "not_found", None
        try:
            hits = search_track(w["titre"], w["artiste"], strict=True)
            if not hits:  # 2e passe : recherche souple
                hits = search_track(w["titre"], w["artiste"], strict=False)
                if not hits and w["artiste"]:
                    free = urllib.parse.quote(f"{w['titre']} {w['artiste']}")
                    hits = dz(f"{API}/search?q={free}").get("data", [])
                status = "approx" if hits else "not_found"
            else:
                status = "found"
            if hits:
                best = hits[0]
        except Exception as e:
            log(f"  !! {e}")
            status = "error"
        entry = {"demande": f"{w['titre']} - {w['artiste']}".strip(" -"),
                 "statut": status}
        if best:
            entry.update({
                "titre": best["title"],
                "artiste": best["artist"]["name"],
                "album": best.get("album", {}).get("title"),
                "lien": best["link"],
                "preview_30s": best.get("preview"),
                "track_id": best["id"],
                "rank": best.get("rank"),
            })
        results.append(entry)

    found = sum(1 for r in results if r["statut"] == "found")
    out_json({"resume": {"demandes": len(results), "trouves_exact": found,
                         "approximatifs": sum(1 for r in results if r["statut"] == "approx"),
                         "introuvables": sum(1 for r in results if r["statut"] == "not_found")},
              "note": "statut approx = correspondance non stricte, verifier titre/artiste",
              "resultats": results})
    return 0


# ---------------------------------------------------------------- enrich
def cmd_enrich(args):
    uid = resolve_user_id(args)
    log("Favoris...")
    favs = list(paginate(f"{API}/user/{uid}/tracks?limit=200"))
    if args.max:
        favs = sorted(favs, key=lambda t: t.get("time_add", 0), reverse=True)[:args.max]
    log(f"Enrichissement track-level de {len(favs)} titres...")

    rows = []
    for i, t in enumerate(favs):
        try:
            full = dz(f"{API}/track/{t['id']}")
            rows.append({"track_id": t["id"], "titre": t["title"],
                         "artiste": t["artist"]["name"],
                         "bpm": full.get("bpm") or None,
                         "gain_db": full.get("gain"),
                         "isrc": full.get("isrc"),
                         "date_sortie": full.get("release_date"),
                         "rank": full.get("rank")})
        except Exception as e:
            log(f"  !! track {t['id']}: {e}")
        if i and i % 50 == 0:
            log(f"  {i}/{len(favs)}")

    with_bpm = [r for r in rows if r["bpm"]]
    out_json({"resume": {"titres": len(rows),
                         "avec_bpm": len(with_bpm),
                         "note": "bpm souvent absent (0/null) : couverture partielle chez Deezer"},
              "titres": rows})
    return 0


# ---------------------------------------------------------------- trends / releases
def cmd_trends(args):
    gid = args.genre_id
    log(f"Charts genre {gid}...")
    data = dz(f"{API}/chart/{gid}/tracks?limit={args.limit}").get("data", [])
    out_json({"genre_id": gid,
              "note": "genre_id 0 = global ; liste des genres : GET /genre",
              "tracks": [{"position": t.get("position"), "titre": t["title"],
                          "artiste": t["artist"]["name"], "lien": t["link"],
                          "preview_30s": t.get("preview")} for t in data]})
    return 0


def cmd_releases(args):
    gid = args.genre_id
    log(f"Sorties editoriales genre {gid}...")
    data = dz(f"{API}/editorial/{gid}/releases?limit={args.limit}").get("data", [])
    out_json({"genre_id": gid,
              "albums": [{"titre": a["title"], "artiste": a["artist"]["name"],
                          "date_sortie": a.get("release_date"), "lien": a["link"]}
                         for a in data]})
    return 0


# ---------------------------------------------------------------- search
def cmd_search(args):
    q = args.query
    for name in ("bpm_min", "bpm_max", "dur_min", "dur_max"):
        val = getattr(args, name)
        if val is not None:
            q += f" {name}:{val}"
    url = f"{API}/search?q={urllib.parse.quote(q)}&limit={args.limit}"
    if args.strict:
        url += "&strict=on"
    if args.order:
        url += f"&order={args.order}"
    log(f"GET {url}")
    data = dz(url).get("data", [])
    out_json({"query": q, "total_page": len(data),
              "tracks": [{"titre": t["title"], "artiste": t["artist"]["name"],
                          "album": t.get("album", {}).get("title"),
                          "duree_s": t["duration"], "rank": t.get("rank"),
                          "lien": t["link"], "preview_30s": t.get("preview"),
                          "track_id": t["id"]} for t in data]})
    return 0


# ---------------------------------------------------------------- main
def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="Outils Deezer sans token (tool-call friendly)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profile", help="dump + profil de gouts")
    sp.add_argument("user_id", nargs="?", default=None,
                    help="defaut : DEEZER_USER_ID (env ou .env)")
    sp.add_argument("--lastfm", default=os.environ.get("LASTFM_API_KEY", ""),
                    help="cle API Last.fm (ou env LASTFM_API_KEY)")
    sp.add_argument("--outdir", default=".")
    sp.set_defaults(fn=cmd_profile)

    sv = sub.add_parser("verify", help="verifier des titres proposes par une IA")
    sv.add_argument("--input", help="fichier texte, un 'Titre - Artiste' par ligne")
    sv.add_argument("--tracks", help='"Titre - Artiste; Titre - Artiste"')
    sv.set_defaults(fn=cmd_verify)

    se = sub.add_parser("enrich", help="bpm/gain/isrc/date par titre (favoris)")
    se.add_argument("user_id", nargs="?", default=None,
                    help="defaut : DEEZER_USER_ID (env ou .env)")
    se.add_argument("--max", type=int, default=0,
                    help="limiter aux N favoris les plus recents")
    se.set_defaults(fn=cmd_enrich)

    st = sub.add_parser("trends", help="charts par genre")
    st.add_argument("--genre-id", type=int, default=0)
    st.add_argument("--limit", type=int, default=25)
    st.set_defaults(fn=cmd_trends)

    sr = sub.add_parser("releases", help="nouvelles sorties editoriales par genre")
    sr.add_argument("--genre-id", type=int, default=0)
    sr.add_argument("--limit", type=int, default=25)
    sr.set_defaults(fn=cmd_releases)

    ss = sub.add_parser("search", help="recherche avancee")
    ss.add_argument("query", help='ex: artist:"damso" ou texte libre')
    ss.add_argument("--bpm-min", type=int, dest="bpm_min")
    ss.add_argument("--bpm-max", type=int, dest="bpm_max")
    ss.add_argument("--dur-min", type=int, dest="dur_min")
    ss.add_argument("--dur-max", type=int, dest="dur_max")
    ss.add_argument("--strict", action="store_true")
    ss.add_argument("--order", help="RANKING|TRACK_ASC|TRACK_DESC|ARTIST_ASC|"
                                    "ARTIST_DESC|ALBUM_ASC|ALBUM_DESC|RATING_ASC|"
                                    "RATING_DESC|DURATION_ASC|DURATION_DESC")
    ss.add_argument("--limit", type=int, default=25)
    ss.set_defaults(fn=cmd_search)

    args = p.parse_args()
    try:
        sys.exit(args.fn(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        out_json({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
