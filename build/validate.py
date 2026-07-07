"""Validation d'intégrité de dist/ avant deploy.

Erreurs (exit 1) : JSON invalide, id dupliqué, champ requis manquant,
set_id orphelin, incohérence cards.json vs fichiers unitaires.
Avertissements (exit 0) : image manquante, counter inhabituel.

Usage : python build/validate.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poneglyph.validate")

DIST = Path(__file__).resolve().parent.parent / "dist"

REQUIRED_CARD_FIELDS = ["id", "card_number", "version", "set_id", "name", "type", "colors", "rarity"]
EXPECTED_COUNTERS = {0, 1000, 2000}


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        cards = json.loads((DIST / "v1" / "cards.json").read_text(encoding="utf-8"))
        sets = json.loads((DIST / "v1" / "sets.json").read_text(encoding="utf-8"))
        filters = json.loads((DIST / "v1" / "filters.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("dist/ illisible : %s", exc)
        return 1

    set_ids = {s["set_id"] for s in sets}
    seen_ids: set[str] = set()

    for c in cards:
        cid = c.get("id", "<sans id>")
        for f in REQUIRED_CARD_FIELDS:
            # les DON!! n'ont pas d'identité de couleur (colors = [])
            if f == "colors" and c.get("type") == "DON!!":
                continue
            if c.get(f) in (None, "", []):
                errors.append(f"{cid} : champ requis vide : {f}")
        if cid in seen_ids:
            errors.append(f"id dupliqué : {cid}")
        seen_ids.add(cid)
        if c.get("set_id") not in set_ids:
            errors.append(f"{cid} : set_id orphelin {c.get('set_id')}")
        if c.get("counter") is not None and c["counter"] not in EXPECTED_COUNTERS:
            warnings.append(f"{cid} : counter inhabituel {c['counter']}")
        if c.get("image"):
            img = DIST / c["image"].lstrip("/")
            if not img.exists():
                warnings.append(f"{cid} : image manquante {c['image']}")
        else:
            warnings.append(f"{cid} : pas d'image")
        card_file = DIST / "v1" / "cards" / f"{cid}.json"
        if not card_file.exists():
            errors.append(f"{cid} : fichier unitaire manquant v1/cards/{cid}.json")

    for s in sets:
        if not (DIST / "v1" / "sets" / f"{s['set_id']}.json").exists():
            errors.append(f"fichier set manquant : v1/sets/{s['set_id']}.json")

    if filters.get("total_cards") != len(cards):
        errors.append(
            f"filters.total_cards={filters.get('total_cards')} ≠ {len(cards)} cartes dans cards.json"
        )

    for w in warnings[:50]:
        log.warning(w)
    if len(warnings) > 50:
        log.warning("... et %d autres avertissements", len(warnings) - 50)
    for e in errors:
        log.error(e)

    if errors:
        log.error("validation ÉCHOUÉE : %d erreur(s), %d avertissement(s)", len(errors), len(warnings))
        return 1
    log.info("validation OK : %d cartes, %d sets, %d avertissement(s)", len(cards), len(sets), len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
