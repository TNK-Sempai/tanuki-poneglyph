# -*- coding: utf-8 -*-
"""Recalcul de AUDIT-CLEANUP-DETAIL.json contre l'état actuel de la base.

C'est le MÊME calcul que `python scraper/cleanup.py` en mode audit : les
fonctions de planification sont importées de cleanup.py, pas réécrites. Ce
module ne fait qu'enrichir le plan (jointure avec les lignes de la base,
alertes de divergence) et l'écrire au format que lit cleanup-review.html.

Aucune écriture en base : c'est un dry-run.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scraper"))

import cleanup  # noqa: E402  — logique de planification, telle quelle

DEST = REPO_ROOT / "AUDIT-CLEANUP-DETAIL.json"
IMAGES_DIR = REPO_ROOT / "images"
LIVE = "https://tanuki-poneglyph.pages.dev/images/"

# Frontière Bandai / opecards du run du 20 août. Fixée volontairement : la
# détection automatique de cleanup.py cherche le plus grand écart entre deux
# created_at, ce qui n'a plus de sens depuis que des éditions manuelles créent
# leurs propres grappes. Les lignes corrigées à la main sont protégées par
# leur drapeau locked, pas par le cutoff.
CUTOFF = "2026-08-20T00:00:00+00:00"

CARD_COLS = ("id,card_number,version,set_id,name,type,rarity,locked,"
             "block_number,created_at,image_filename")


def _fetch(sb, table, cols):
    rows, off = [], 0
    while True:
        res = sb.table(table).select(cols).range(off, off + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return rows
        off += 1000


def _vue(row, role, sets, tracked):
    if row is None:
        return {"role": role, "INTROUVABLE_EN_BASE": True}
    fn = row.get("image_filename")
    return {
        "role": role,
        "id": row["id"],
        "nom": row["name"],
        "numero": row["card_number"],
        "set_id": row["set_id"],
        "set_nom": sets.get(row["set_id"], {}).get("name"),
        "rarete": row["rarity"],
        "version": row["version"],
        "version_canonique": cleanup.canon_version(row["version"]),
        "type": row.get("type"),
        "image_fichier": fn,
        "image_locale": f"images/{fn}" if fn else None,
        "image_url": (LIVE + fn) if fn else None,
        "image_deja_en_ligne": bool(fn and fn in tracked),
        "scrape_le": row.get("created_at"),
        "verrouillee": bool(row.get("locked")),
        "origine_probable": ("Bandai" if str(row.get("created_at") or "") < CUTOFF
                             else "opecards / édition manuelle"),
    }


def rebuild(sb, tracked_images: set[str] | None = None) -> dict:
    """Recalcule le plan et réécrit le fichier. Retourne un résumé."""
    tracked = tracked_images or set()
    cards = _fetch(sb, "poneglyph_cards", CARD_COLS)
    sets_rows = _fetch(sb, "poneglyph_sets", "set_id,name,set_type")
    sets = {s["set_id"]: s for s in sets_rows}
    by_id = {c["id"]: c for c in cards}

    cutoff = datetime.fromisoformat(CUTOFF)
    cleanup.annotate_origin(cards, cutoff)
    actions = cleanup.plan_card_actions(cards)

    locked_ids = {c["id"] for c in cards if c.get("locked")}
    protected = cleanup.protect_locked(actions, locked_ids)

    # renommages de sets, sets vidés, images orphelines : mêmes règles que
    # cleanup.py, recalculées sur le plan effectivement retenu.
    set_renames = []
    for s in sets_rows:
        neuf = cleanup.clean_set_name(s["set_id"], s["name"])
        if neuf != s["name"]:
            set_renames.append({"set_id": s["set_id"], "before": s["name"], "after": neuf})

    restants: dict[str, int] = defaultdict(int)
    supprimes = {d["id"] for d in actions["delete"]}
    deplaces = {r["id"]: r["to_set"] for r in actions["reassign"]}
    for c in cards:
        if c["id"] in supprimes:
            continue
        restants[deplaces.get(c["id"], c["set_id"])] += 1
    empty_sets = sorted(s["set_id"] for s in sets_rows if restants[s["set_id"]] == 0)

    gardees = {c["image_filename"] for c in cards
               if c["id"] not in supprimes and c.get("image_filename")}
    orphan_images = sorted({
        c["image_filename"] for c in cards
        if c["id"] in supprimes and c.get("image_filename")
        and c["image_filename"] not in gardees
        and (IMAGES_DIR / c["image_filename"]).exists()})

    # ---- enrichissement pour la revue humaine ----
    entrees = []
    for d in actions["delete"]:
        src = _vue(by_id.get(d["id"]), "A_SUPPRIMER", sets, tracked)
        cible = _vue(by_id.get(d["kept"]), "CONSERVEE", sets, tracked)
        r = d["reason"]
        if r == "doublon inter-sets":
            detail = (f"Doublon inter-sets : le numero {src.get('numero')} en version "
                      f"'{src.get('version')}' (canonique '{src.get('version_canonique')}') "
                      f"existe deja sous le code Bandai {cible.get('id')} dans le set "
                      f"{cible.get('set_id')}. Critere utilise : card_number + version "
                      f"canonique. Le set_id N'EST PAS compare, la rarete NON PLUS.")
        elif r == "doublon SP (match par rareté)":
            detail = (f"Variante SP : la ligne opecards '{src.get('version')}' est appariee a "
                      f"l'unique ligne Bandai de rarete SP du meme (numero, set) -> "
                      f"{cible.get('id')} rarete '{cible.get('rarete')}'.")
        elif r == "doublon même set":
            detail = (f"Meme set et meme version canonique que {cible.get('id')}. "
                      f"Critere : card_number + set_id + version canonique.")
        elif r == "doublon après réassignation":
            detail = (f"Apres reassignation vers {d.get('set_bandai')}, l'identifiant "
                      f"{d['kept']} existe deja : la ligne source fait double emploi.")
        else:
            detail = r

        alertes = []
        if src.get("rarete") != cible.get("rarete"):
            alertes.append(f"RARETES DIFFERENTES ({src.get('rarete')} vs {cible.get('rarete')}) "
                           f"- verifier qu'il s'agit bien de la meme impression")
        if src.get("version") != cible.get("version"):
            alertes.append(f"LIBELLES DE VERSION DIFFERENTS ('{src.get('version')}' vs "
                           f"'{cible.get('version')}') rapproches par la canonisation")
        if src.get("set_id") != cible.get("set_id"):
            alertes.append(f"SETS DIFFERENTS ({src.get('set_id')} -> {cible.get('set_id')}) "
                           f"- si ce sont deux impressions distinctes, la suppression est A TORT")
        if src.get("nom") != cible.get("nom"):
            alertes.append(f"NOMS DIFFERENTS ('{src.get('nom')}' vs '{cible.get('nom')}')")

        entrees.append({"action": "SUPPRESSION", "raison_script": r,
                        "raison_detaillee": detail, "alertes": alertes,
                        "carte_source": src, "carte_cible": cible})

    for r in actions["reassign"]:
        src = _vue(by_id.get(r["id"]), "A_DEPLACER", sets, tracked)
        entrees.append({
            "action": "REASSIGNATION", "raison_script": r["reason"],
            "raison_detaillee": (f"Le numero {r['card_number']} n'est connu de Bandai que dans "
                                 f"le set {r['to_set']}. La ligne, classee dans {r['from_set']}, "
                                 f"y est deplacee. Nouvel identifiant : {r['new_id']}. "
                                 f"Critere : card_number seul."),
            "alertes": [f"La carte quitte {r['from_set']} pour {r['to_set']} - verifier que "
                        f"{r['from_set']} n'est pas son set d'impression reel"],
            "carte_source": src,
            "cible": {"set_id": r["to_set"], "set_nom": sets.get(r["to_set"], {}).get("name"),
                      "nouvel_id": r["new_id"], "version_conservee": r["version"]},
        })

    # suppressions a risque : regle inter-sets ET raretes divergentes
    risque = []
    for e in entrees:
        if e["action"] != "SUPPRESSION" or e["raison_script"] != "doublon inter-sets":
            continue
        s, c = e["carte_source"], e["carte_cible"]
        if s.get("rarete") == c.get("rarete"):
            continue
        meme_rarete = [x for x in cards
                       if x["card_number"] == s["numero"] and x["id"] != s["id"]
                       and x["rarity"] == s["rarete"]]
        risque.append({
            "numero": s["numero"], "nom": s["nom"],
            "supprimee": {"id": s["id"], "set": s["set_id"], "set_nom": s["set_nom"],
                          "rarete": s["rarete"], "version": s["version"],
                          "image": s["image_locale"]},
            "conservee": {"id": c["id"], "set": c["set_id"], "set_nom": c["set_nom"],
                          "rarete": c["rarete"], "version": c["version"],
                          "image": c["image_locale"]},
            "pourquoi_c_est_risque": (
                "La regle 'doublon inter-sets' compare card_number + version canonique "
                "uniquement. Elle ne regarde NI le set NI la rarete. Ici les deux lignes ont "
                "des raretes differentes : il peut s'agir de deux impressions distinctes."),
            "verification": {
                "la_rarete_survit_ailleurs": bool(meme_rarete),
                "ou": [{"id": x["id"], "set": x["set_id"], "version": x["version"]}
                       for x in meme_rarete],
                "conclusion": ("L'impression n'est PAS perdue : une autre ligne porte la meme "
                               "rarete." if meme_rarete else
                               "ATTENTION : plus aucune ligne ne porte cette rarete apres "
                               "suppression."),
            },
        })

    par_set: dict[str, dict] = {}
    for e in entrees:
        sid = e["carte_source"].get("set_id")
        par_set.setdefault(sid, {"suppressions": 0, "reassignations": 0})
        par_set[sid]["suppressions" if e["action"] == "SUPPRESSION"
                     else "reassignations"] += 1

    out = {
        "genere_le": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": f"recalcul depuis le dashboard — cleanup.py (audit) --cutoff {CUTOFF}",
        "avertissement": ("Aucune de ces actions n'a ete appliquee. Recalcul complet contre "
                          "l'etat actuel de la base : les entrees deja resolues a la main ont "
                          "disparu d'elles-memes."),
        "critere_identite_du_script": {
            "regle_1a_doublon_meme_set": "card_number + set_id + version canonique.",
            "regle_1b_doublon_SP": "card_number + set_id, apparie par rarete SP, match 1 contre 1.",
            "regle_2_doublon_inter_sets": ("card_number + version canonique. Le set_id N'EST PAS "
                                           "compare, la rarete non plus. Regle a surveiller."),
            "regle_3_reassignation": "card_number seul. Ne supprime rien.",
            "cartes_verrouillees": ("Toute action visant une ligne locked=true est retiree du "
                                    "plan : les corrections manuelles ne sont jamais defaites."),
        },
        "etat_base_actuel": {"cartes": len(cards), "sets": len(sets_rows),
                             "verrouillees": len(locked_ids)},
        "projection_si_applique": {"cartes": len(cards) - len(actions["delete"]),
                                   "sets": len(sets_rows) - len(empty_sets),
                                   "sets_retires": empty_sets},
        "totaux": {"suppressions": len(actions["delete"]),
                   "reassignations": len(actions["reassign"]),
                   "ambigus_non_traites": len(actions["ambiguous"]),
                   "protegees_car_verrouillees": len(protected),
                   "entrees_avec_alerte": sum(1 for e in entrees if e["alertes"])},
        "suppressions_a_risque": risque,
        "protegees_car_verrouillees": protected,
        "repartition_par_set_source": dict(sorted(par_set.items())),
        "cas_ambigus": actions["ambiguous"],
        "set_renames": set_renames,
        "images_orphelines_qui_seraient_effacees": orphan_images,
        "actions": entrees,
    }
    DEST.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "ok": True,
        "genere_le": out["genere_le"],
        "actions": len(entrees),
        "suppressions": len(actions["delete"]),
        "reassignations": len(actions["reassign"]),
        "a_risque": len(risque),
        "ambigus": len(actions["ambiguous"]),
        "protegees_car_verrouillees": len(protected),
        "cartes_en_base": len(cards),
    }
