"""Scraper Playwright du cardlist officiel Bandai FR.

Flow :
  1. Liste des séries FR sur https://fr.onepiece-cardgame.com/cardlist/
  2. Pour chaque série (?series=XXXXXX) : rendu JS complet, parse du DOM,
     téléchargement des images (vraie URL, pas le dummy.gif), conversion WebP.
  3. Upsert Supabase (ON CONFLICT (card_number, version, set_id) DO UPDATE).
  4. Validation : count scrapé vs card_count attendu (delta > 10 % = échec).

Usage :
  python scraper/scrape.py                     # tous les sets
  python scraper/scrape.py --series 569201     # une série précise
  python scraper/scrape.py --skip-images       # données seules
  python scraper/scrape.py --dry-run           # sans écrire dans Supabase

Sortie : scraper/report.json (consommé par scrape.yml pour ouvrir une
GitHub Issue en cas d'échec). Exit code 1 si la validation échoue.

⚠️  Le mapping DOM (classes CSS Bandai) est celui du cardlist officiel ; il
    doit être vérifié au premier run réel et adapté si Bandai change le HTML.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from upload_images import download_and_convert

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # secrets locaux ; sans effet en CI (env déjà présent)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poneglyph.scraper")

BASE_URL = "https://fr.onepiece-cardgame.com/cardlist/"
IMAGES_DIR = REPO_ROOT / "images"
REPORT_PATH = Path(__file__).resolve().parent / "report.json"

VALIDATION_DELTA = 0.10  # delta > 10 % vs card_count attendu → échec

# Mots-clés d'abilities reconnus dans le texte d'effet FR (entre crochets).
# À enrichir au fil des sets si Bandai introduit de nouveaux mots-clés.
KNOWN_ABILITIES = {
    "Bloqueur",
    "Double attaque",
    "Initiative",
    "Contre",
    "Banish",
    "Une fois par tour",
}

# Suffixe d'image Bandai (_p1, _p2, ...) → libellé de version.
# Bandai ne publie pas le type exact de variante (Manga, Wanted, ...) dans le
# DOM ; on mappe génériquement, à affiner manuellement si besoin.
VERSION_BY_SUFFIX = {
    None: "Standard",
    "p1": "Alternative Art",
    "p2": "Alternative Art 2",
    "p3": "Alternative Art 3",
    "p4": "Alternative Art 4",
    "p5": "Alternative Art 5",
}

TYPES_WITHOUT_CHARACTER = {"ÉVÉNEMENT", "LIEU", "DON!!"}


def slugify_version(version: str) -> str:
    """"Alternative Art" → "alternative_art" (pour id + nom de fichier image)."""
    v = unicodedata.normalize("NFKD", version).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", v.lower()).strip("_")


def card_id(card_number: str, version: str, set_id: str) -> str:
    """Slug primaire : "OP16-001_standard_OP16"."""
    return f"{card_number}_{slugify_version(version)}_{set_id}"


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(" ", "").replace(" ", ""))
    return int(m.group()) if m else None


# Séries FR sans crochets dans le libellé → set_id fixe.
SPECIAL_SERIES = {
    "622901": "PROMO",  # Carte promo
    "622801": "OTHER",  # Carte d'autres produits
}


def set_id_from_label(label: str, series_value: str) -> str:
    """Résout le set_id d'une série. Objectif : 100 % des séries FR scrapées.

    - "... [OP-16]" / "... [OP16]"      → "OP16"
    - "... [OP15-EB04]" (composé)       → dernier code : "EB04"
    - Carte promo (series=622901)       → "PROMO"
    - Carte d'autres produits (622801)  → "OTHER"
    - Sinon fallback "S{series_value}" (jamais de série ignorée)
    """
    if series_value in SPECIAL_SERIES:
        return SPECIAL_SERIES[series_value]

    m = re.search(r"\[([^\]]+)\]", label)
    if m:
        codes = re.findall(r"([A-Z]+)[-‐]?(\d+)", m.group(1))
        if codes:
            prefix, num = codes[-1]  # "[OP15-EB04]" → ("EB", "04")
            return f"{prefix}{int(num):02d}"

    # Pas de crochets exploitables : détection par mots-clés du libellé
    folded = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    if "promo" in folded:
        return "PROMO"
    if "autres produits" in folded or "other products" in folded:
        return "OTHER"

    log.warning(
        "set_id non reconnu pour %r (series=%s) — fallback S%s", label, series_value, series_value
    )
    return f"S{series_value}"


def set_type_from_id(set_id: str) -> str:
    m = re.match(r"^([A-Z]+)", set_id)
    return m.group(1) if m else "AUTRE"


class FieldReader:
    """Lit les blocs .backCol du DOM Bandai : chaque champ est un <div> avec
    un <h3> libellé (Coût, Puissance, Couleur, ...) suivi de la valeur."""

    def __init__(self, back_col):
        self.fields: dict[str, str] = {}
        if back_col is None:
            return
        for div in back_col.find_all("div"):
            h3 = div.find("h3")
            if not h3:
                continue
            label = h3.get_text(strip=True)
            # Valeur = texte du div sans le libellé ; l'attribut est parfois
            # porté par l'alt d'une icône <img>.
            img = div.find("img")
            if img and img.get("alt"):
                value = img["alt"].strip()
            else:
                value = div.get_text(" ", strip=True)
                if value.startswith(label):
                    value = value[len(label):].strip()
            self.fields[label] = value

    def get(self, *labels: str) -> str | None:
        for label in labels:
            v = self.fields.get(label)
            if v and v not in {"-", "ー", ""}:
                return v
        return None


def parse_card(dl, set_id: str) -> dict | None:
    """Parse un <dl class="modalCol"> en ligne poneglyph_cards."""
    info = dl.select_one(".infoCol")
    name_el = dl.select_one(".cardName")
    if not info or not name_el:
        return None

    info_parts = [s.get_text(strip=True) for s in info.find_all("span")]
    if len(info_parts) < 3:
        info_parts = [p.strip() for p in info.get_text("|", strip=True).split("|")]
    if len(info_parts) < 3:
        log.warning("infoCol illisible dans %s : %r", set_id, info_parts)
        return None
    card_number, rarity, card_type = info_parts[0], info_parts[1], info_parts[2]
    card_type = card_type.upper()

    # Image : URL réelle en data-src (lazy-load), sinon src.
    img = dl.select_one(".frontCol img")
    img_url = None
    if img:
        img_url = img.get("data-src") or img.get("src")
        if img_url and "dummy" in img_url:
            img_url = img.get("data-src")
        if img_url:
            # Le DOM Bandai fournit des URLs relatives ("../images/cardlist/card/...")
            img_url = urljoin(BASE_URL, img_url)

    # Version déduite du suffixe du fichier image : OP16-001_p1.png → p1.
    suffix = None
    if img_url:
        m = re.search(r"_(p\d+)\.(?:png|jpg|jpeg|webp)", img_url, re.I)
        if m:
            suffix = m.group(1).lower()
    version = VERSION_BY_SUFFIX.get(suffix, f"Alternative Art ({suffix})" if suffix else "Standard")

    reader = FieldReader(dl.select_one(".backCol"))

    colors_raw = reader.get("Couleur", "Couleurs") or ""
    colors = [c.strip() for c in re.split(r"[/,]", colors_raw) if c.strip()]

    affiliations_raw = reader.get("Caractéristique", "Caractéristiques", "Type") or ""
    affiliations = [a.strip() for a in affiliations_raw.split("/") if a.strip()]

    effect = reader.get("Effet", "Texte") or None
    trigger_effect = reader.get("Déclencheur", "Trigger") or None

    abilities = []
    if effect:
        for kw in re.findall(r"\[([^\]\[]+)\]", effect):
            kw = kw.strip()
            if kw in KNOWN_ABILITIES and kw not in abilities:
                abilities.append(kw)

    name = name_el.get_text(strip=True)
    version_slug = slugify_version(version)

    row = {
        "id": card_id(card_number, version, set_id),
        "card_number": card_number,
        "version": version,
        "set_id": set_id,
        "name": name,
        "type": card_type,
        "colors": colors,
        "attribute": reader.get("Attribut", "Attributs"),
        "power": parse_int(reader.get("Puissance")),
        "cost": parse_int(reader.get("Coût")),
        "counter": parse_int(reader.get("Contre")),
        "life": parse_int(reader.get("Vie")),
        "effect": effect,
        "trigger_effect": trigger_effect,
        "rarity": rarity,
        # Bandai n'expose pas le personnage illustré ; par défaut le nom de la
        # carte pour LEADER/PERSONNAGE. Affinable à la main (V1.2).
        "character_name": name if card_type not in TYPES_WITHOUT_CHARACTER else None,
        "affiliations": affiliations,
        "abilities": abilities,
        "image_filename": f"{card_number}_{version_slug}.webp",
        "block_number": parse_int(reader.get("Bloc", "Block")),
        "_image_url": img_url,  # champ interne, retiré avant upsert
    }

    if not colors:
        log.warning("carte %s sans couleur — colors est NOT NULL, à vérifier", row["id"])
    return row


def get_series_list(page) -> list[dict]:
    """Options du <select> séries : [{value, label, set_id}]."""
    page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)
    soup = BeautifulSoup(page.content(), "html.parser")
    select = soup.select_one("select#series") or soup.select_one("select[name=series]")
    if not select:
        raise RuntimeError("select#series introuvable — le DOM Bandai a changé.")
    series = []
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        if not value:
            continue
        label = opt.get_text(strip=True)
        series.append({"value": value, "label": label, "set_id": set_id_from_label(label, value)})
    return series


def clean_set_name(label: str) -> str:
    """Retire le suffixe "[OP-16]" du libellé pour obtenir le nom du set."""
    return re.sub(r"\s*\[[^\]]+\]\s*$", "", label).strip(" -–—")


def scrape_series(page, serie: dict, skip_images: bool) -> list[dict]:
    url = f"{BASE_URL}?series={serie['value']}"
    log.info("scrape %s (%s) → %s", serie["set_id"], serie["label"], url)
    page.goto(url, wait_until="networkidle", timeout=90_000)
    page.wait_for_selector(".resultCol", timeout=30_000)
    soup = BeautifulSoup(page.content(), "html.parser")

    cards = []
    for dl in soup.select("dl.modalCol"):
        row = parse_card(dl, serie["set_id"])
        if row:
            cards.append(row)

    log.info("%s : %d cartes parsées", serie["set_id"], len(cards))

    if not skip_images:
        for row in cards:
            url = row.pop("_image_url")
            if not url:
                log.warning("pas d'URL image pour %s", row["id"])
                continue
            try:
                download_and_convert(url, IMAGES_DIR / row["image_filename"])
            except Exception as exc:  # une image manquante ne bloque pas le set
                log.error("image KO pour %s : %s", row["id"], exc)
    else:
        for row in cards:
            row.pop("_image_url", None)

    return cards


def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants (voir .env.example)")
    return create_client(url, key)


def upsert(sb, table: str, rows: list[dict], on_conflict: str, chunk: int = 200):
    for i in range(0, len(rows), chunk):
        sb.table(table).upsert(rows[i : i + chunk], on_conflict=on_conflict).execute()


def main() -> int:
    ap = argparse.ArgumentParser(description="Scraper Bandai FR → Supabase")
    ap.add_argument("--series", help="valeur(s) de série à scraper, séparées par des virgules")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="ne pas écrire dans Supabase")
    args = ap.parse_args()

    report = {"sets": [], "errors": [], "ok": True}

    sb = None if args.dry_run else get_supabase()

    # card_count attendus (déjà en BDD) pour la validation
    expected_counts: dict[str, int] = {}
    if sb:
        res = sb.table("poneglyph_sets").select("set_id,card_count").execute()
        expected_counts = {r["set_id"]: r["card_count"] for r in res.data if r.get("card_count")}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        series_list = get_series_list(page)
        if args.series:
            wanted = {s.strip() for s in args.series.split(",")}
            series_list = [s for s in series_list if s["value"] in wanted or s["set_id"] in wanted]
        log.info("%d série(s) à scraper", len(series_list))

        for serie in series_list:
            sid = serie["set_id"]
            try:
                cards = scrape_series(page, serie, args.skip_images)
            except Exception as exc:
                report["ok"] = False
                report["errors"].append(f"{sid} : échec scraping — {exc}")
                log.exception("échec sur %s", sid)
                continue

            # Validation : delta > 10 % vs card_count attendu → set rejeté
            expected = expected_counts.get(sid)
            scraped = len(cards)
            entry = {"set_id": sid, "scraped": scraped, "expected": expected}
            if expected and abs(scraped - expected) / expected > VALIDATION_DELTA:
                report["ok"] = False
                entry["status"] = "REJETÉ (delta > 10 %)"
                report["errors"].append(
                    f"{sid} : {scraped} cartes scrapées vs {expected} attendues (delta > 10 %)"
                )
                report["sets"].append(entry)
                continue
            entry["status"] = "OK"
            report["sets"].append(entry)

            if sb:
                set_row = {
                    "set_id": sid,
                    "name": clean_set_name(serie["label"]),
                    "set_type": set_type_from_id(sid),
                }
                if sid not in expected_counts:
                    set_row["card_count"] = scraped
                upsert(sb, "poneglyph_sets", [set_row], on_conflict="set_id")
                upsert(sb, "poneglyph_cards", cards, on_conflict="card_number,version,set_id")
                log.info("%s : %d cartes upsertées", sid, scraped)

        browser.close()

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["ok"]:
        log.error("scraping terminé avec erreurs — pas de build. Voir %s", REPORT_PATH)
        return 1
    log.info("scraping OK — %d set(s)", len(report["sets"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
