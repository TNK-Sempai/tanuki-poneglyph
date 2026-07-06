"""Build : Supabase (source de vérité privée) → fichiers statiques dist/.

  dist/
  ├── v1/
  │   ├── cards.json            toutes les cartes, array plat
  │   ├── sets.json             tous les sets + métadonnées
  │   ├── filters.json          valeurs uniques pour chaque filtre
  │   ├── sets/{set_id}.json
  │   └── cards/{id}.json
  ├── images/*.webp             copiées depuis images/ (repo)
  └── schema_version.json

Écrit aussi worker/data/{cards,sets,filters}.json : le Worker Cloudflare les
importe en statique (pas de KV, pas de fetch runtime vers Supabase).

Usage : python build/generate.py
Env   : SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # secrets locaux ; sans effet en CI (env déjà présent)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poneglyph.build")

SCHEMA_VERSION = "1.0"
DIST = REPO_ROOT / "dist"
IMAGES_SRC = REPO_ROOT / "images"
WORKER_DATA = REPO_ROOT / "worker" / "data"

PAGE_SIZE = 1000  # Supabase limite à 1000 lignes par requête


def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants (voir .env.example)")
    return create_client(url, key)


def fetch_all(sb, table: str, order: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            sb.table(table)
            .select("*")
            .order(order)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def public_card(row: dict) -> dict:
    """Ligne Supabase → objet carte public (contrat de l'API v1)."""
    return {
        "id": row["id"],
        "card_number": row["card_number"],
        "version": row["version"],
        "set_id": row["set_id"],
        "name": row["name"],
        "type": row["type"],
        "colors": row["colors"] or [],
        "attribute": row["attribute"],
        "power": row["power"],
        "cost": row["cost"],
        "counter": row["counter"],
        "life": row["life"],
        "effect": row["effect"],
        "trigger_effect": row["trigger_effect"],
        "rarity": row["rarity"],
        "character_name": row["character_name"],
        "affiliations": row["affiliations"] or [],
        "abilities": row["abilities"] or [],
        "image": f"/images/{row['image_filename']}" if row.get("image_filename") else None,
        "block_number": row["block_number"],
    }


def public_set(row: dict) -> dict:
    return {
        "set_id": row["set_id"],
        "name": row["name"],
        "set_type": row["set_type"],
        "release_date": row["release_date"],
        "card_count": row["card_count"],
    }


def int_range(values: list[int | None]) -> dict | None:
    vals = [v for v in values if v is not None]
    return {"min": min(vals), "max": max(vals)} if vals else None


def build_filters(cards: list[dict], sets: list[dict]) -> dict:
    def uniq(key: str) -> list:
        return sorted({c[key] for c in cards if c[key]})

    def uniq_arr(key: str) -> list:
        return sorted({v for c in cards for v in c[key]})

    return {
        "schema_version": SCHEMA_VERSION,
        "total_cards": len(cards),
        "types": uniq("type"),
        "colors": uniq_arr("colors"),
        "attributes": uniq("attribute"),
        "rarities": uniq("rarity"),
        "sets": [
            {"id": s["set_id"], "name": s["name"], "type": s["set_type"], "card_count": s["card_count"]}
            for s in sets
        ],
        "set_types": sorted({s["set_type"] for s in sets}),
        "versions": uniq("version"),
        "abilities": uniq_arr("abilities"),
        "characters": uniq("character_name"),
        "affiliations": uniq_arr("affiliations"),
        "power_range": int_range([c["power"] for c in cards]),
        "cost_range": int_range([c["cost"] for c in cards]),
        "counter_values": sorted({c["counter"] for c in cards if c["counter"] is not None}),
        "life_range": int_range([c["life"] for c in cards]),
    }


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    sb = get_supabase()
    sets = [public_set(r) for r in fetch_all(sb, "poneglyph_sets", order="set_id")]
    cards = [public_card(r) for r in fetch_all(sb, "poneglyph_cards", order="id")]
    log.info("%d sets, %d cartes récupérés depuis Supabase", len(sets), len(cards))
    if not cards:
        log.error("aucune carte en BDD — build annulé")
        return 1

    if DIST.exists():
        shutil.rmtree(DIST)

    filters = build_filters(cards, sets)

    write_json(DIST / "v1" / "cards.json", cards)
    write_json(DIST / "v1" / "sets.json", sets)
    write_json(DIST / "v1" / "filters.json", filters)
    for s in sets:
        set_cards = [c for c in cards if c["set_id"] == s["set_id"]]
        write_json(DIST / "v1" / "sets" / f"{s['set_id']}.json", {**s, "cards": set_cards})
    for c in cards:
        write_json(DIST / "v1" / "cards" / f"{c['id']}.json", c)
    write_json(
        DIST / "schema_version.json",
        {"version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat()},
    )

    # Images servies par Cloudflare Pages (bande passante illimitée)
    dist_images = DIST / "images"
    dist_images.mkdir(parents=True, exist_ok=True)
    copied = 0
    if IMAGES_SRC.exists():
        for img in IMAGES_SRC.glob("*.webp"):
            shutil.copy2(img, dist_images / img.name)
            copied += 1
    log.info("%d images copiées vers dist/images/", copied)

    # Données embarquées en statique dans le Worker
    WORKER_DATA.mkdir(parents=True, exist_ok=True)
    write_json(WORKER_DATA / "cards.json", cards)
    write_json(WORKER_DATA / "sets.json", sets)
    write_json(WORKER_DATA / "filters.json", filters)

    log.info("build OK → %s", DIST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
