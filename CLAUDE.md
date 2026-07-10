# Deezer Recommandation — instructions pour Claude

## Outil principal : `deezer_tool.py`

CLI Deezer sans token, conçu pour être appelé par Claude Code via `python deezer_tool.py <sous-commande>`.

**Contrat tool-call :**
- `stdout` = JSON pur (machine-readable)
- `stderr` = progression / erreurs
- Code retour `0` si OK, `1` si erreur fatale
- Stdlib uniquement, aucune dépendance

## Sous-commandes

```
profile USER_ID [--lastfm KEY] [--outdir DIR]
    Dump complet (CSV) + profil de goûts compact (JSON) → stdout.

verify --input FICHIER | --tracks "Titre - Artiste; Titre - Artiste"
    Vérifie que des titres proposés (par une IA) existent sur Deezer.
    Sortie : lien, preview 30s, statut found/not_found/approx.

enrich USER_ID [--max N]
    Données track-level des favoris : bpm, gain, isrc, date de sortie.

trends [--genre-id N] [--limit N]
    Charts Deezer (0 = tous genres). Ex : 116 = Rap/Hip Hop.

releases [--genre-id N] [--limit N]
    Nouvelles sorties éditoriales par genre.

search QUERY [--bpm-min N] [--bpm-max N] [--dur-min N] [--dur-max N]
             [--strict] [--order ORDER] [--limit N]
    Recherche avancée (filtres artist:/track:/album:/label: acceptés dans QUERY).
```

## Exemples

```bash
python deezer_tool.py profile 2770287342
python deezer_tool.py verify --tracks "Exutoire - Damso; Tricheur - Nekfeu"
python deezer_tool.py search 'artist:"damso"' --bpm-min 130 --strict
python deezer_tool.py trends --genre-id 116 --limit 20
```

## Workflow recommandé

1. **Analyser le profil** : `profile USER_ID` → JSON complet avec genres, artistes, deep cuts, zone d'exploration.
2. **Proposer des titres** → les vérifier avec `verify` avant de les présenter à l'utilisateur.
3. **Enrichir** : `enrich` pour obtenir BPM/ISRC si nécessaire pour des recommandations tempo-based.
4. **Découverte** : `trends` ou `releases` pour les nouveautés, `search` pour cibler par critères.

## Notes importantes

- `verify` effectue 2 passes (stricte puis souple) : `statut approx` = vérifier manuellement.
- `profile` génère aussi un CSV dans `--outdir` (défaut `.`).
- `enrich` : le BPM est souvent absent chez Deezer (couverture partielle).
- Rate limit : 50 req/5s — le script gère la pause automatiquement.
