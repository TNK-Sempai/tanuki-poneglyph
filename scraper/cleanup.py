"""Audit + correction des incohérences BDD après le scrape combiné Bandai + opecards.

Trois familles de problèmes traitées :

1. DOUBLONS — même carte physique en double sous deux noms de version :
   a. équivalence lexicale ("Special Card" ≡ "Alternative Art 2",
      "SP Card"/"Parallèle" ≡ "Alternative Art", table VERSION_SYNONYMS) ;
   b. variants SP : une ligne opecards "… SP …" est le doublon de LA ligne
      Bandai de rareté SP du même (card_number, set_id) — le libellé Bandai
      varie ("Alternative Art 3" pour ST15-005, "Alternative Art 2" pour
      OP10-045…), le match se fait donc par rareté, pas par libellé.
      Auto uniquement si le match est 1↔1, sinon rapport "ambiguous".
   On garde toujours la ligne Bandai (source de vérité).

2. NOMS DE SETS — les sets auto-créés par scrape_opecards.py portent des noms
   bruts ("Set DPS07 (opecards)"). Renommage propre en français.

3. CARTES MAL ASSIGNÉES — la convention Bandai FR fait référence :
   - doublon inter-sets (même carte, versions équivalentes, sets différents)
     → suppression de la ligne opecards ;
   - variante opecards inédite rattachée au mauvais set alors que Bandai ne
     connaît ce card_number que dans UN seul set → réassignation ;
   - cas ambigus → rapport uniquement, aucune action automatique.

Origine des lignes (pas de colonne source en BDD, et block_number est NULL
partout) : les deux scrapes forment deux grappes temporelles disjointes de
created_at (Bandai a tourné en premier). Le cutoff est auto-détecté (plus
grand écart entre created_at consécutifs, minimum 1 h) et peut être forcé
avec --cutoff "2026-07-07T00:00:00+00:00".

Usage :
  python scraper/cleanup.py            # AUDIT seul (dry-run, aucune écriture)
  python scraper/cleanup.py --apply    # applique les corrections

Rapport avant/après : scraper/report_cleanup.json
Après cleanup : python build/generate.py && wrangler pages deploy dist/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # secrets locaux ; sans effet en CI (env déjà présent)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("poneglyph.cleanup")

IMAGES_DIR = REPO_ROOT / "images"
REPORT_PATH = Path(__file__).resolve().parent / "report_cleanup.json"

MIN_CLUSTER_GAP = timedelta(hours=1)

# ---------------------------------------------------------------------------
# Équivalences de versions inter-sources (forme canonique = libellé Bandai).
# Clés et valeurs sous forme canonisée (minuscules, sans accents/ponctuation).
# Enrichir cette table au fil des audits.
VERSION_SYNONYMS = {
    "sp card": "alternative art",
    "special card": "alternative art 2",
    "special card 2": "alternative art 2",
    "parallele": "alternative art",
    "parallel": "alternative art",
}

# Renommages manuels de sets (prioritaire sur le nettoyage générique)
SET_NAME_OVERRIDES = {
    "DON": "Cartes DON!!",
    "STP": "Tournoi Boutique Promo",
}


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def canon_version(version: str) -> str:
    """Forme canonique d'un nom de version, pour comparer entre sources.

    "Alternative Art (p6)" → "alternative art 6", "Parallèle 2" →
    "alternative art 2", "Special Card" → "alternative art 2", ...
    """
    x = re.sub(r"[^a-z0-9]+", " ", fold(version)).strip()
    x = re.sub(r"^parall?ele?\b", "alternative art", x)
    x = re.sub(r"\balternative art p(\d+)\b", r"alternative art \1", x)
    x = VERSION_SYNONYMS.get(x, x)
    # "alternative art 1" ≡ "alternative art"
    x = re.sub(r"^alternative art 1$", "alternative art", x)
    return x


def is_sp_version(version: str) -> bool:
    """"Alternative Art SP", "SP Parallèle", "SP Parallèle Gold" → True."""
    return "sp" in re.sub(r"[^a-z0-9]+", " ", fold(version)).split()


def is_sp_rarity(rarity: str | None) -> bool:
    """Raretés Bandai des variants SP : "SP CARD", "SP R", "SR SP", ..."""
    return bool(rarity) and "SP" in rarity.upper().split()


def slugify_version(version: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(version)).strip("_")


def card_id(card_number: str, version: str, set_id: str) -> str:
    return f"{card_number}_{slugify_version(version)}_{set_id}"


def clean_set_name(set_id: str, name: str) -> str:
    """Nom de set propre : overrides manuels, puis retrait des références
    opecards ("Set DPS07 (opecards)" → "Double Pack Set 7")."""
    if set_id in SET_NAME_OVERRIDES:
        return SET_NAME_OVERRIDES[set_id]
    if set_id.startswith("DPS") and set_id[3:].isdigit():
        return f"Double Pack Set {int(set_id[3:])}"
    cleaned = re.sub(r"\s*\(opecards\)\s*", " ", name, flags=re.I)
    cleaned = re.sub(r"\bopecards(\.fr)?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    if re.fullmatch(rf"Set {re.escape(set_id)}", cleaned, re.I) or not cleaned:
        cleaned = f"Set {set_id}"
    return cleaned


def detect_cutoff(cards: list[dict]) -> datetime | None:
    """Sépare les deux grappes temporelles Bandai / opecards : plus grand
    écart entre created_at consécutifs (≥ MIN_CLUSTER_GAP)."""
    stamps = sorted({datetime.fromisoformat(c["created_at"]) for c in cards})
    if len(stamps) < 2:
        return None
    best_gap, cutoff = timedelta(0), None
    for a, b in zip(stamps, stamps[1:]):
        if b - a > best_gap:
            best_gap, cutoff = b - a, b
    return cutoff if best_gap >= MIN_CLUSTER_GAP else None


def annotate_origin(cards: list[dict], cutoff: datetime) -> None:
    """Ajoute _bandai (bool) à chaque ligne : created_at < cutoff = Bandai."""
    for c in cards:
        c["_bandai"] = datetime.fromisoformat(c["created_at"]) < cutoff


def plan_card_actions(cards: list[dict]) -> dict:
    """Calcule les actions sans toucher à la BDD (fonction pure, testable).
    Chaque ligne doit porter _bandai (voir annotate_origin).

    Retourne {"delete": [...], "reassign": [...], "ambiguous": [...]}.
    """
    bandai = [c for c in cards if c["_bandai"]]
    opecards = [c for c in cards if not c["_bandai"]]

    by_triplet: dict[tuple, dict] = {}
    by_number_version: dict[tuple, list[dict]] = defaultdict(list)
    sets_by_number: dict[str, set] = defaultdict(set)
    sp_by_group: dict[tuple, list[dict]] = defaultdict(list)
    for b in bandai:
        cv = canon_version(b["version"])
        by_triplet.setdefault((b["card_number"], b["set_id"], cv), b)
        by_number_version[(b["card_number"], cv)].append(b)
        sets_by_number[b["card_number"]].add(b["set_id"])
        if is_sp_rarity(b.get("rarity")):
            sp_by_group[(b["card_number"], b["set_id"])].append(b)

    # lignes opecards SP par groupe, pour le match 1↔1 par rareté
    sp_opecards_by_group: dict[tuple, list[dict]] = defaultdict(list)
    for o in opecards:
        if is_sp_version(o["version"]):
            sp_opecards_by_group[(o["card_number"], o["set_id"])].append(o)

    delete, reassign, ambiguous = [], [], []
    handled: set[str] = set()

    for o in opecards:
        cv = canon_version(o["version"])
        num, sid = o["card_number"], o["set_id"]

        # 1a. doublon lexical même set
        same_set = by_triplet.get((num, sid, cv))
        if same_set:
            delete.append({
                "id": o["id"], "kept": same_set["id"], "reason": "doublon même set",
                "card_number": num, "set_id": sid,
                "version_opecards": o["version"], "version_bandai": same_set["version"],
            })
            handled.add(o["id"])
            continue

        # 1b. variant SP : match par rareté SP Bandai dans le même groupe
        if is_sp_version(o["version"]):
            sp_bandai = sp_by_group.get((num, sid), [])
            sp_ope = sp_opecards_by_group.get((num, sid), [])
            if len(sp_bandai) == 1 and len(sp_ope) == 1:
                delete.append({
                    "id": o["id"], "kept": sp_bandai[0]["id"],
                    "reason": "doublon SP (match par rareté)",
                    "card_number": num, "set_id": sid,
                    "version_opecards": o["version"],
                    "version_bandai": sp_bandai[0]["version"],
                    "rarity_bandai": sp_bandai[0].get("rarity"),
                })
                handled.add(o["id"])
                continue
            if sp_bandai:  # plusieurs SP de part et/ou d'autre : arbitrage manuel
                ambiguous.append({
                    "id": o["id"], "card_number": num, "set_id": sid,
                    "version": o["version"],
                    "candidates_bandai": [
                        {"id": b["id"], "version": b["version"], "rarity": b.get("rarity")}
                        for b in sp_bandai],
                    "reason": "plusieurs variants SP dans le groupe — arbitrage manuel",
                })
                handled.add(o["id"])
                continue

        # 2. doublon inter-sets (la convention Bandai gagne)
        cross = by_number_version.get((num, cv))
        if cross:
            delete.append({
                "id": o["id"], "kept": cross[0]["id"], "reason": "doublon inter-sets",
                "card_number": num, "set_id": sid,
                "version_opecards": o["version"], "version_bandai": cross[0]["version"],
                "set_bandai": cross[0]["set_id"],
            })
            handled.add(o["id"])
            continue

        # 3. variante inédite : vérifier l'assignation de set
        bandai_sets = sets_by_number.get(num, set())
        if not bandai_sets or sid in bandai_sets:
            continue  # carte inédite (DON, P-xxx...) ou set déjà conforme
        if len(bandai_sets) == 1:
            target = next(iter(bandai_sets))
            reassign.append({
                "id": o["id"], "card_number": num, "version": o["version"],
                "from_set": sid, "to_set": target,
                "new_id": card_id(num, o["version"], target),
                "reason": "convention Bandai : card_number connu uniquement dans ce set",
            })
        else:
            ambiguous.append({
                "id": o["id"], "card_number": num, "version": o["version"],
                "set_id": sid, "bandai_sets": sorted(bandai_sets),
                "reason": "card_number présent dans plusieurs sets Bandai — arbitrage manuel",
            })

    # une réassignation qui collisionne avec une ligne existante = doublon
    existing_ids = {c["id"] for c in cards}
    final_reassign = []
    for r in reassign:
        if r["new_id"] in existing_ids:
            delete.append({
                "id": r["id"], "kept": r["new_id"], "reason": "doublon après réassignation",
                "card_number": r["card_number"], "set_id": r["from_set"],
                "version_opecards": r["version"], "version_bandai": r["version"],
                "set_bandai": r["to_set"],
            })
        else:
            final_reassign.append(r)

    return {"delete": delete, "reassign": final_reassign, "ambiguous": ambiguous}


# ---------------------------------------------------------------------------

def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants (voir .env.example)")
    return create_client(url, key)


def fetch_all(sb, table: str, columns: str) -> list[dict]:
    rows, offset, page = [], 0, 1000
    while True:
        res = sb.table(table).select(columns).range(offset, offset + page - 1).execute()
        rows.extend(res.data)
        if len(res.data) < page:
            return rows
        offset += page


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit/correction BDD Tanuki-Poneglyph")
    ap.add_argument("--apply", action="store_true",
                    help="applique les corrections (défaut : audit seul)")
    ap.add_argument("--cutoff", help="force le cutoff Bandai/opecards (ISO 8601)")
    args = ap.parse_args()

    sb = get_supabase()
    cards = fetch_all(
        sb, "poneglyph_cards",
        "id,card_number,version,set_id,rarity,block_number,created_at,image_filename",
    )
    sets = fetch_all(sb, "poneglyph_sets", "set_id,name,set_type")
    log.info("BDD : %d cartes, %d sets", len(cards), len(sets))

    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else detect_cutoff(cards)
    if cutoff is None:
        log.error("impossible de séparer les grappes Bandai/opecards par created_at "
                  "(écart < 1 h) — préciser --cutoff")
        return 1
    annotate_origin(cards, cutoff)
    n_bandai = sum(c["_bandai"] for c in cards)
    log.info("cutoff origine : %s → %d lignes Bandai, %d lignes opecards",
             cutoff.isoformat(), n_bandai, len(cards) - n_bandai)

    actions = plan_card_actions(cards)

    # noms de sets à corriger
    set_renames = []
    for s in sets:
        new_name = clean_set_name(s["set_id"], s["name"])
        if new_name != s["name"]:
            set_renames.append({"set_id": s["set_id"], "before": s["name"], "after": new_name})

    # sets vides après corrections (cartes supprimées ou réassignées ailleurs)
    remaining_per_set: dict[str, int] = defaultdict(int)
    deleted_ids_sim = {d["id"] for d in actions["delete"]}
    moved = {r["id"]: r["to_set"] for r in actions["reassign"]}
    for c in cards:
        if c["id"] in deleted_ids_sim:
            continue
        remaining_per_set[moved.get(c["id"], c["set_id"])] += 1
    empty_sets = sorted(s["set_id"] for s in sets if remaining_per_set[s["set_id"]] == 0)

    # images orphelines après suppression (aucune autre ligne ne les référence)
    deleted_ids = {d["id"] for d in actions["delete"]}
    kept_filenames = {c["image_filename"] for c in cards
                      if c["id"] not in deleted_ids and c.get("image_filename")}
    orphan_images = sorted({
        c["image_filename"] for c in cards
        if c["id"] in deleted_ids and c.get("image_filename")
        and c["image_filename"] not in kept_filenames
        and (IMAGES_DIR / c["image_filename"]).exists()
    })

    report = {
        "mode": "apply" if args.apply else "audit (dry-run)",
        "origin_cutoff": cutoff.isoformat(),
        "before": {
            "total_cards": len(cards),
            "bandai_rows": n_bandai,
            "opecards_rows": len(cards) - n_bandai,
            "total_sets": len(sets),
            "duplicates": len(actions["delete"]),
            "reassignments": len(actions["reassign"]),
            "ambiguous": len(actions["ambiguous"]),
            "set_renames": len(set_renames),
            "empty_sets": len(empty_sets),
            "orphan_images": len(orphan_images),
        },
        "empty_sets": empty_sets,
        "delete": actions["delete"],
        "reassign": actions["reassign"],
        "ambiguous": actions["ambiguous"],
        "set_renames": set_renames,
        "orphan_images": orphan_images,
    }

    log.info("AUDIT : %d doublon(s) à supprimer, %d réassignation(s), %d ambigu(s), "
             "%d set(s) à renommer, %d set(s) vide(s) à supprimer, %d image(s) orpheline(s)",
             len(actions["delete"]), len(actions["reassign"]), len(actions["ambiguous"]),
             len(set_renames), len(empty_sets), len(orphan_images))
    if empty_sets:
        log.info("  sets vides après corrections : %s", ", ".join(empty_sets))
    for d in actions["delete"]:
        log.info("  DEL  %-45s (%s) — garde %s", d["id"], d["reason"], d["kept"])
    for r in actions["reassign"]:
        log.info("  MOVE %-45s : %s → %s", r["id"], r["from_set"], r["to_set"])
    for a in actions["ambiguous"]:
        log.warning("  AMBIGU %-43s : %s", a["id"], a["reason"])

    if args.apply:
        # 1) suppressions (doublons)
        ids = [d["id"] for d in actions["delete"]]
        for i in range(0, len(ids), 100):
            sb.table("poneglyph_cards").delete().in_("id", ids[i:i + 100]).execute()
        # 2) réassignations = delete + insert avec nouvel id/set_id
        for r in actions["reassign"]:
            full = sb.table("poneglyph_cards").select("*").eq("id", r["id"]).execute().data
            if not full:
                continue
            row = full[0]
            row["id"], row["set_id"] = r["new_id"], r["to_set"]
            row.pop("created_at", None)
            sb.table("poneglyph_cards").delete().eq("id", r["id"]).execute()
            sb.table("poneglyph_cards").upsert(
                [row], on_conflict="card_number,version,set_id").execute()
        # 3) renommages de sets
        for s in set_renames:
            sb.table("poneglyph_sets").update({"name": s["after"]}) \
              .eq("set_id", s["set_id"]).execute()
        # 4) sets devenus vides (aucune carte ne les référence plus)
        for sid in empty_sets:
            still = sb.table("poneglyph_cards").select("id").eq("set_id", sid) \
                      .limit(1).execute().data
            if not still:
                sb.table("poneglyph_sets").delete().eq("set_id", sid).execute()
        # 5) images orphelines locales
        for fname in orphan_images:
            (IMAGES_DIR / fname).unlink(missing_ok=True)

        after_cards = fetch_all(sb, "poneglyph_cards", "id")
        after_sets = fetch_all(sb, "poneglyph_sets", "set_id")
        report["after"] = {
            "total_cards": len(after_cards),
            "total_sets": len(after_sets),
            "deleted": len(ids),
            "reassigned": len(actions["reassign"]),
            "sets_renamed": len(set_renames),
            "empty_sets_removed": len(empty_sets),
            "orphan_images_removed": len(orphan_images),
        }
        log.info("APPLIQUÉ : %d cartes, %d sets après cleanup "
                 "(%d supprimées, %d réassignées, %d sets vides retirés)",
                 len(after_cards), len(after_sets), len(ids),
                 len(actions["reassign"]), len(empty_sets))
    else:
        log.info("Dry-run : aucune écriture. Relancer avec --apply pour corriger.")

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("rapport : %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
