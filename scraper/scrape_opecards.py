"""Scraper complémentaire opecards.fr — cartes FR manquantes dans la BDD.

opecards.fr recense l'intégralité des cartes FR (promos, event packs, regional
packs, championship, variantes non listées par Bandai, DON!!...). Ce scraper
complète la BDD Supabase alimentée par scrape.py (Bandai) : il n'insère QUE
les cartes absentes du triplet (card_number, version, set_id).

Fonctionnement du site (découvert par exploration) :
  - GET /cards/liste-cartes-francaises  → pose le filtre langue=FR en session
  - GET /cards/{n}                      → page n de la liste filtrée (30/page)
  - grille : a.item-main-link (title "Page détaillée de OP16-001-L Nom (Version)")
  - page détail : tables de specs + effet dans .item-description-inner div[lang=fr]
    + JSON-LD Product (sku, personnage illustré)
  - les requêtes hors navigateur sont bloquées → tout passe par Playwright,
    téléchargement des images inclus (context.request).

Usage :
  python scraper/scrape_opecards.py                  # scrape complet
  python scraper/scrape_opecards.py --dry-run        # sans écriture Supabase
  python scraper/scrape_opecards.py --limit-pages 3  # debug : N pages de liste
  python scraper/scrape_opecards.py --details-limit 10  # debug : N pages détail

Sortie : scraper/report_opecards.json + logs "poneglyph.opecards".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from upload_images import convert_and_save

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # secrets locaux ; sans effet en CI (env déjà présent)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poneglyph.opecards")

BASE = "https://www.opecards.fr"
LIST_FR = f"{BASE}/cards/liste-cartes-francaises"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
IMAGES_DIR = REPO_ROOT / "images"
DIST_IMAGES_DIR = REPO_ROOT / "dist" / "images"
REPORT_PATH = Path(__file__).resolve().parent / "report_opecards.json"

REQUEST_DELAY_S = 0.6      # politesse entre requêtes
VALIDATION_DELTA = 0.05    # delta > 5 % entre total opecards et BDD → warning

RARITY_BY_SLUG_CODE = {
    "c": "C", "uc": "UC", "r": "R", "sr": "SR", "sec": "SEC",
    "l": "L", "p": "P", "tr": "TR", "sp": "SP", "don": "DON!!",
}

# Alias série opecards → set_id BDD (alignés sur les sets issus de Bandai)
SET_ALIASES = {"P": "PROMO"}

# Mots-clés d'abilities (mêmes que scrape.py, dupliqués pour éviter d'importer
# tout le module Bandai)
KNOWN_ABILITIES = {
    "Bloqueur", "Double attaque", "Initiative", "Contre", "Banish", "Une fois par tour",
}

TYPES_WITHOUT_CHARACTER = {"ÉVÉNEMENT", "LIEU", "DON!!"}

CHAMPIONSHIP_KEYWORDS = re.compile(
    r"(championship|champion|winner|finalist|finaliste|regional|régional|tournoi|top \d|participant)",
    re.I,
)


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def slugify_version(version: str) -> str:
    """Même convention que scrape.py : "Alternative Art" → "alternative_art"."""
    return re.sub(r"[^a-z0-9]+", "_", fold(version)).strip("_")


def card_id(card_number: str, version: str, set_id: str) -> str:
    return f"{card_number}_{slugify_version(version)}_{set_id}"


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value.replace(" ", "").replace(" ", "").replace("\xa0", ""))
    return int(m.group()) if m else None


def parse_tile_title(title: str) -> tuple[str, str, str, str] | None:
    """Cartes classiques :
      "Page détaillée de OP16-001-L Portgas D. Ace (Parallèle)"
      → ("OP16-001", "L", "Portgas D. Ace", "Parallèle")
    Cartes DON!! (numéro sans tiret, pas de code rareté) :
      "Page détaillée de DON0045 Charlotte Katakuri DON!! (PRB01 Gold)"
      → ("DON0045", "DON!!", "Charlotte Katakuri DON!!", "PRB01 Gold")
    """
    title = title.strip()
    m = re.match(
        r"^Page détaillée de\s+([A-Z0-9]+-\d+[a-zA-Z]?)-([A-Z]{1,3}|DON)\s+(.*)$",
        title,
    )
    rarity_code = None
    if m:
        card_number, rarity_code, rest = m.group(1), m.group(2).upper(), m.group(3).strip()
    else:
        m = re.match(r"^(?:Page détaillée de\s+)?(DON\d+)\s+(.*)$", title)
        if not m:
            return None
        card_number, rest = m.group(1), m.group(2).strip()
        rarity_code = "DON!!"

    version_raw = ""
    vm = re.search(r"\(([^()]+)\)\s*$", rest)
    if vm:
        version_raw = vm.group(1).strip()
        rest = rest[: vm.start()].strip()
    return card_number, rarity_code, rest, version_raw


def resolve_set_and_version(card_number: str, version_raw: str) -> tuple[str, str]:
    """Détermine (set_id, version) alignés sur les données Bandai.

    - défaut : set = préfixe du numéro ("OP16-001" → "OP16", "P-008" → "PROMO")
    - un token de version qui est un code de set ("PRB02", "EB02", "OP16") =
      produit FR de réédition → devient le set_id et sort de la version
    - "Parallèle" → "Alternative Art" (+ numéro éventuel), comme les _pN Bandai
    - version vide → "Standard"
    - DON!! ("DON0045") : set par défaut "DON", "(PRB01 Gold)" → set PRB01 +
      version Gold, "(Double Pack Set 7)" → set DPS07 + version Standard
    """
    if card_number.upper().startswith("DON"):
        m = re.match(r"^Double Pack Set\s*(\d+)(?:\s+(.*))?$", version_raw or "", re.I)
        if m:
            version = (m.group(2) or "").strip()
            return f"DPS{int(m.group(1)):02d}", version or "Standard"
        set_id = "DON"
    else:
        prefix = card_number.split("-")[0].upper()
        set_id = SET_ALIASES.get(prefix, prefix)

    tokens = version_raw.split() if version_raw else []
    kept: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"(OP|ST|EB|PRB|STP|CH|PCC)-?\d+", tok, re.I):
            set_id = tok.upper().replace("-", "")
        else:
            kept.append(tok)

    version = " ".join(kept).strip()
    m = re.match(r"^Parall[èe]le(?:\s+(\d+))?(?:\s+(.*))?$", version, re.I)
    if m:
        n, rest = m.group(1), (m.group(2) or "").strip()
        version = "Alternative Art" + (f" {n}" if n else "") + (f" {rest}" if rest else "")
    return set_id, version or "Standard"


def set_type_from_id(set_id: str) -> str:
    m = re.match(r"^([A-Z]+)", set_id)
    return m.group(1) if m else "AUTRE"


def effect_text(container) -> str | None:
    """Texte d'effet : les mots-clés en <span class="skill …"> reprennent la
    notation Bandai entre crochets ("[Une fois par tour]")."""
    if container is None:
        return None
    clone = BeautifulSoup(str(container), "html.parser")
    for span in clone.select("span.skill"):
        span.replace_with(f"[{span.get_text(strip=True)}]")
    for br in clone.find_all("br"):
        br.replace_with("\n")
    text = re.sub(r"[ \t\xa0]+", " ", clone.get_text())
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


class DetailPage:
    """Parse une page détail opecards (tables de specs + effet + JSON-LD)."""

    def __init__(self, html: str):
        self.soup = BeautifulSoup(html, "html.parser")
        self.fields: dict[str, str] = {}
        for table in self.soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) >= 2:
                    label = fold(cells[0].get_text(" ", strip=True))
                    value = cells[1].get_text(" ", strip=True)
                    if label and value:
                        self.fields.setdefault(label, value)

    def get(self, *labels: str) -> str | None:
        for label in labels:
            v = self.fields.get(fold(label))
            if v and v not in {"-", "?", ""}:
                return v
        return None

    def get_list(self, *labels: str) -> list[str]:
        raw = self.get(*labels) or ""
        return [x.strip() for x in re.split(r"[/,]", raw) if x.strip()]

    def character_name(self) -> str | None:
        for sc in self.soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(sc.string or "")
            except (TypeError, json.JSONDecodeError):
                continue
            chars = d.get("character") if isinstance(d, dict) else None
            if chars:
                return chars[0].get("name")
        return None

    def effects(self) -> tuple[str | None, str | None]:
        blocks = self.soup.select(".item-description-inner div[lang]")
        texts = [t for t in (effect_text(b) for b in blocks) if t]
        effect = texts[0] if texts else None
        trigger = None
        for t in texts[1:]:
            if "déclench" in fold(t) or t.startswith("["):
                trigger = t
                break
        # certains layouts mettent le déclencheur dans l'effet : "[Déclenchement] ..."
        if effect and trigger is None:
            m = re.search(r"\[Déclench[^\]]*\]\s*", effect)
            if m and m.start() > 0:
                trigger = effect[m.start():].strip()
                effect = effect[: m.start()].strip() or None
        return effect, trigger


def build_card(tile: dict, detail: DetailPage) -> dict:
    card_number, rarity_code = tile["card_number"], tile["rarity_code"]
    set_id, version = tile["set_id"], tile["version"]

    card_type = (detail.get("Type") or ("DON!!" if rarity_code == "DON!!" else "")).upper()
    colors = detail.get_list("Couleurs", "Couleur")
    effect, trigger = detail.effects()

    abilities = []
    if effect:
        for kw in re.findall(r"\[([^\]\[]+)\]", effect):
            kw = kw.strip()
            if kw in KNOWN_ABILITIES and kw not in abilities:
                abilities.append(kw)

    character = detail.character_name()
    if not character and card_type not in TYPES_WITHOUT_CHARACTER:
        character = tile["name"]
    if card_type == "DON!!" and not character:
        # "Charlotte Katakuri DON!!" → personnage illustré "Charlotte Katakuri"
        character = re.sub(r"\s*DON!!\s*$", "", tile["name"]).strip() or None

    return {
        "id": card_id(card_number, version, set_id),
        "card_number": card_number,
        "version": version,
        "set_id": set_id,
        "name": tile["name"],
        "type": card_type or "PERSONNAGE",
        "colors": colors,
        "attribute": detail.get("Attributs", "Attribut"),
        "power": parse_int(detail.get("Puissance")),
        "cost": parse_int(detail.get("Coût en énergie", "Coût d'énergie", "Coût")),
        "counter": parse_int(detail.get("Contre", "Counter")),
        "life": parse_int(detail.get("Points de vie", "Vie")),
        "effect": effect,
        "trigger_effect": trigger,
        "rarity": rarity_code,
        "character_name": character,
        "affiliations": detail.get_list("Origines", "Origine"),
        "abilities": abilities,
        "image_filename": f"{card_number}_{slugify_version(version)}.webp",
        "block_number": None,  # non exposé par opecards
    }


def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants (voir .env.example)")
    return create_client(url, key)


def fetch_existing(sb) -> tuple[set[tuple[str, str, str]], set[str], int]:
    """Triplets (card_number, version, set_id) existants + set_ids + total."""
    triplets: set[tuple[str, str, str]] = set()
    offset, page = 0, 1000
    while True:
        res = (
            sb.table("poneglyph_cards")
            .select("card_number,version,set_id")
            .range(offset, offset + page - 1)
            .execute()
        )
        for r in res.data:
            triplets.add((r["card_number"], r["version"], r["set_id"]))
        if len(res.data) < page:
            break
        offset += page
    sets = {r["set_id"] for r in sb.table("poneglyph_sets").select("set_id").execute().data}
    return triplets, sets, len(triplets)


def upsert(sb, table: str, rows: list[dict], on_conflict: str, chunk: int = 200):
    for i in range(0, len(rows), chunk):
        sb.table(table).upsert(rows[i : i + chunk], on_conflict=on_conflict).execute()


def harvest_raw(page) -> list[dict]:
    """Tuiles brutes (href/title/img) de la page de liste courante."""
    return page.eval_on_selector_all(
        "a.item-main-link",
        """els => els.map(e => ({
             href: e.getAttribute('href'),
             title: e.getAttribute('title') || '',
             img: e.querySelector('img')?.dataset?.src
                  || e.querySelector('img')?.getAttribute('src') || null,
           }))""",
    )


def parse_tiles(raw: list[dict], warnings: list[str]) -> list[dict]:
    """Tuiles brutes → dicts pré-parsés. Une tuile illisible est loggée et
    comptée mais n'interrompt jamais la pagination."""
    tiles = []
    for r in raw:
        parsed = parse_tile_title(r["title"])
        if not parsed:
            log.warning("tuile illisible : %r (%s)", r["title"], r["href"])
            warnings.append(f"tuile illisible : {r['title']!r} ({r['href']})")
            continue
        card_number, rarity_code, name, version_raw = parsed
        rarity = RARITY_BY_SLUG_CODE.get(rarity_code.lower(), rarity_code)
        set_id, version = resolve_set_and_version(card_number, version_raw)
        tiles.append({
            "href": r["href"],
            "image_url": r["img"],
            "card_number": card_number,
            "rarity_code": rarity,
            "name": name,
            "version": version,
            "set_id": set_id,
        })
    return tiles


def fetch_series_names(page) -> dict[str, str]:
    """/series → {set_id: nom FR} pour créer les sets manquants."""
    names: dict[str, str] = {}
    try:
        page.goto(f"{BASE}/series", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1000)
        soup = BeautifulSoup(page.content(), "html.parser")
        for a in soup.select('a[href^="/series/"]'):
            slug = a["href"].split("/series/")[-1]
            m = re.match(r"^([a-z]+\d*)-(.+)$", slug)
            if not m:
                continue
            code = m.group(1).upper()
            code = SET_ALIASES.get(code, code)
            label = a.get_text(" ", strip=True) or m.group(2).replace("-", " ").title()
            names.setdefault(code, label)
    except Exception as exc:
        log.warning("liste des séries irrécupérable : %s", exc)
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description="Scraper opecards.fr → cartes FR manquantes")
    ap.add_argument("--dry-run", action="store_true", help="ne pas écrire dans Supabase")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--limit-pages", type=int, default=None, help="debug : N pages de liste max")
    ap.add_argument("--details-limit", type=int, default=None, help="debug : N pages détail max")
    args = ap.parse_args()

    sb = None if args.dry_run else get_supabase()
    existing, existing_sets, db_total = (set(), set(), 0) if sb is None else fetch_existing(sb)
    log.info("BDD : %d cartes, %d sets", db_total, len(existing_sets))

    report: dict = {"source": "opecards.fr", "ok": True, "warnings": [], "added": []}
    added_cards: list[dict] = []
    skipped = 0
    opecards_total = 0
    per_set_opecards: dict[str, int] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        series_names = fetch_series_names(page)

        # 1) filtre langue FR en session, puis pagination /cards/{n}
        page.goto(LIST_FR, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("a.item-main-link", timeout=30_000)
        page_numbers = page.eval_on_selector_all(
            "span.page-link[data-page]", "els => els.map(e => parseInt(e.dataset.page))")
        last_page = max(page_numbers) if page_numbers else 1
        if args.limit_pages:
            last_page = min(last_page, args.limit_pages)
        log.info("liste FR : %d pages à parcourir", last_page)

        def load_page_raw(n: int) -> list[dict]:
            if n > 1:
                page.goto(f"{BASE}/cards/{n}", wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("a.item-main-link", timeout=30_000)
            return harvest_raw(page)

        all_tiles: list[dict] = []
        prev_first = None
        for n in range(1, last_page + 1):
            raw: list[dict] = []
            for attempt in (1, 2):
                try:
                    raw = load_page_raw(n)
                except Exception as exc:
                    log.warning("liste page %d, tentative %d : %s", n, attempt, exc)
                    raw = []
                first = raw[0]["href"] if raw else None
                # contenu vide ou identique à la page précédente = session
                # (filtre FR) probablement expirée côté serveur
                if raw and not (n > 1 and prev_first and first == prev_first):
                    break
                if attempt == 1:
                    log.warning("liste page %d : contenu vide/répété — "
                                "rétablissement de la session FR puis retry", n)
                    page.goto(LIST_FR, wait_until="domcontentloaded", timeout=60_000)
                    page.wait_for_selector("a.item-main-link", timeout=30_000)
                    time.sleep(REQUEST_DELAY_S)
            else:
                msg = (f"pagination interrompue à la page {n}/{last_page} "
                       f"(contenu vide ou répété malgré le retry)")
                report["warnings"].append(msg)
                log.error(msg)
                break
            prev_first = raw[0]["href"]
            all_tiles.extend(parse_tiles(raw, report["warnings"]))
            time.sleep(REQUEST_DELAY_S)
            if n % 10 == 0 or n == last_page:
                log.info("liste : page %d/%d (%d cartes)", n, last_page, len(all_tiles))

        opecards_total = len(all_tiles)
        for t in all_tiles:
            per_set_opecards[t["set_id"]] = per_set_opecards.get(t["set_id"], 0) + 1

        # 2) tri manquant / existant
        missing = []
        for t in all_tiles:
            if (t["card_number"], t["version"], t["set_id"]) in existing:
                skipped += 1
            else:
                missing.append(t)
        log.info("opecards : %d cartes FR, %d déjà en BDD, %d manquantes",
                 opecards_total, skipped, len(missing))
        if args.details_limit:
            missing = missing[: args.details_limit]

        # 3) pages détail des cartes manquantes uniquement
        for i, tile in enumerate(missing, 1):
            try:
                page.goto(BASE + tile["href"], wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_selector("table", timeout=20_000)
                card = build_card(tile, DetailPage(page.content()))
                added_cards.append(card)
            except Exception as exc:
                report["warnings"].append(f"détail KO {tile['href']} : {exc}")
                log.error("détail KO %s : %s", tile["href"], exc)
            if i % 25 == 0 or i == len(missing):
                log.info("détails : %d/%d", i, len(missing))
            time.sleep(REQUEST_DELAY_S)

        # dédoublonnage par id, dernière occurrence conservée
        deduped = list({c["id"]: c for c in added_cards}.values())
        if len(deduped) < len(added_cards):
            log.warning("%d doublon(s) d'id supprimé(s)", len(added_cards) - len(deduped))
            added_cards = deduped

        # 4) images manquantes (via le contexte navigateur, requests étant bloqué)
        images_downloaded = 0
        if not args.skip_images:
            added_filenames = {c["image_filename"] for c in added_cards}
            for tile in missing:
                fname = f"{tile['card_number']}_{slugify_version(tile['version'])}.webp"
                if fname not in added_filenames or not tile["image_url"]:
                    continue
                dest = IMAGES_DIR / fname
                if dest.exists():
                    continue
                try:
                    resp = ctx.request.get(tile["image_url"])
                    if not resp.ok:
                        raise RuntimeError(f"HTTP {resp.status}")
                    convert_and_save(resp.body(), dest)
                    if DIST_IMAGES_DIR.exists():
                        convert_and_save(resp.body(), DIST_IMAGES_DIR / fname)
                    images_downloaded += 1
                except Exception as exc:
                    report["warnings"].append(f"image KO {fname} : {exc}")
                    log.error("image KO %s : %s", fname, exc)
                time.sleep(REQUEST_DELAY_S)

        browser.close()

    # 5) sets manquants + upsert
    def set_name(s: str) -> str:
        if s in series_names:
            return series_names[s]
        if s == "DON":
            return "Cartes DON!!"
        if s.startswith("DPS"):
            return f"Double Pack Set {int(s[3:])}"
        return f"Set {s} (opecards)"

    new_sets = sorted({c["set_id"] for c in added_cards} - existing_sets) if sb else []
    if sb and added_cards:
        set_rows = [{
            "set_id": s,
            "name": set_name(s),
            "set_type": set_type_from_id(s),
        } for s in new_sets]
        if set_rows:
            upsert(sb, "poneglyph_sets", set_rows, on_conflict="set_id")
            log.info("sets créés : %s", ", ".join(new_sets))
        upsert(sb, "poneglyph_cards", added_cards, on_conflict="card_number,version,set_id")
        log.info("%d cartes upsertées", len(added_cards))

    # 6) rapport
    dons = [c for c in added_cards if c["type"] == "DON!!" or c["card_number"].startswith("DON")]
    promos = [c for c in added_cards if c["set_id"] in ("PROMO", "STP")
              or c["card_number"].startswith("P-")]
    championship = [c for c in added_cards
                    if CHAMPIONSHIP_KEYWORDS.search(f"{c['version']} {c['set_id']}")]

    db_total_after = db_total + (len(added_cards) if sb else 0)
    delta_msg = None
    if opecards_total:
        delta = abs(db_total_after - opecards_total) / opecards_total
        if delta > VALIDATION_DELTA:
            # séries potentiellement incomplètes : comparaison par set
            suspects = []
            if sb:
                per_set_db: dict[str, int] = {}
                for cn, v, s in existing:
                    per_set_db[s] = per_set_db.get(s, 0) + 1
                for c in added_cards:
                    per_set_db[c["set_id"]] = per_set_db.get(c["set_id"], 0) + 1
                for s, n in sorted(per_set_opecards.items()):
                    if per_set_db.get(s, 0) < n:
                        suspects.append(f"{s} (BDD {per_set_db.get(s, 0)} < opecards {n})")
            delta_msg = (
                f"delta {delta:.1%} entre BDD ({db_total_after}) et opecards "
                f"({opecards_total}) ; séries potentiellement incomplètes : "
                + (", ".join(suspects) if suspects else "n/a")
            )
            report["warnings"].append(delta_msg)
            log.warning(delta_msg)

    report.update({
        "opecards_total_fr": opecards_total,
        "db_total_before": db_total,
        "db_total_after": db_total_after,
        "added_count": len(added_cards),
        "added_don": len(dons),
        "added_promo": len(promos),
        "added_championship": len(championship),
        "images_downloaded": images_downloaded,
        "skipped_existing": skipped,
        "new_sets": new_sets,
        "added": sorted(c["id"] for c in added_cards),
    })
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info(
        "TERMINÉ : %d ajoutées (dont %d DON!!, %d promos, %d championship), "
        "%d images, %d déjà en BDD — rapport : %s",
        len(added_cards), len(dons), len(promos), len(championship),
        images_downloaded, skipped, REPORT_PATH,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
