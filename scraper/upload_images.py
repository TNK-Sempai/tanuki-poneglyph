"""Téléchargement + conversion WebP des images de cartes.

Les images sont stockées dans images/ à la racine du repo (commitées par le
workflow scrape.yml), puis copiées dans dist/images/ par build/generate.py.
Elles sont servies par Cloudflare Pages — jamais par Supabase Storage.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import requests
from PIL import Image

log = logging.getLogger("poneglyph.images")

WEBP_QUALITY = 85
MAX_WIDTH = 800
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://fr.onepiece-cardgame.com/cardlist/",
}


def download_and_convert(url: str, dest: Path, force: bool = False) -> bool:
    """Télécharge une image et l'écrit en WebP (quality 85, max 800px de large).

    Retourne True si le fichier a été écrit, False s'il existait déjà.
    Lève une exception en cas d'échec réseau ou de décodage.
    """
    if dest.exists() and not force:
        return False

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    img = Image.open(io.BytesIO(resp.content))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, round(img.height * ratio)), Image.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.webp")
    img.save(tmp, "WEBP", quality=WEBP_QUALITY)
    tmp.replace(dest)
    log.info("image écrite : %s", dest.name)
    return True
