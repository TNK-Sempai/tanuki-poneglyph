# -*- coding: utf-8 -*-
"""Backend local de l'éditeur manuel Ponéglyphe.

Sert le dashboard et les images depuis la racine du repo, et expose une petite
API JSON vers Supabase. La clé service_role reste ICI, côté serveur : elle
contourne le RLS et ne doit jamais atteindre le navigateur.

    python dashboard/server.py          # http://127.0.0.1:8099/dashboard/editor.html
    python dashboard/server.py --port 9000

Outil local uniquement : écoute sur 127.0.0.1, aucune exposition publique prévue.

Accès protégé par mot de passe (DASHBOARD_PASSWORD dans .env). La garde est
posée côté serveur sur TOUTES les routes — API comme fichiers — pour qu'un
appel direct à /api/... sans session soit refusé, et pas seulement masqué par
l'interface.

Toute écriture passant par cette API marque la ligne locked=true : les scrapers
et cleanup.py ne réécrasent jamais une carte corrigée à la main.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from functools import partial
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
from supabase import create_client

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # pour audit.py
load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poneglyph.editor")

# Colonnes editables. created_at et id sont derives / gerés par la base.
CARD_COLUMNS = [
    "card_number", "version", "set_id", "name", "type", "colors", "attribute",
    "power", "cost", "counter", "life", "effect", "trigger_effect", "rarity",
    "character_name", "affiliations", "abilities", "image_filename",
    "block_number", "locked",
]
ARRAY_COLUMNS = {"colors", "affiliations", "abilities"}
INT_COLUMNS = {"power", "cost", "counter", "life", "block_number"}

# Colonnes editables d'un set. created_at est gere par la base.
SET_COLUMNS = ["set_id", "name", "set_type", "release_date", "card_count"]
SET_REQUIRED = ["set_id", "name", "set_type"]

_sb = None

# ---------------------------------------------------------------------------
# Authentification.
#
# Le mot de passe vit dans .env (DASHBOARD_PASSWORD), jamais dans le code.
# La session est un cookie signe HMAC : "<expiration>.<signature>". La cle de
# signature est tiree au hasard au demarrage, donc redemarrer le serveur
# invalide les sessions en cours. Le cookie est HttpOnly : JavaScript ne peut
# pas le lire, seul le navigateur le renvoie.
COOKIE = "pgl_session"
SESSION_SECRET = secrets.token_bytes(32)
SESSION_TTL = 12 * 3600

# Seules ressources accessibles sans session : la page de connexion, sa
# feuille de style, et l'endpoint de connexion lui-meme.
PUBLIC_PATHS = {"/dashboard/login.html", "/dashboard/tanuki.css", "/api/login", "/favicon.ico"}


def password() -> str:
    p = os.getenv("DASHBOARD_PASSWORD") or ""
    if not p:
        raise RuntimeError(
            "DASHBOARD_PASSWORD absent de .env — le dashboard refuse de démarrer sans "
            "mot de passe. Ajoutez la variable puis relancez.")
    return p


def make_token() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(SESSION_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(tok: str | None) -> bool:
    if not tok or "." not in tok:
        return False
    exp, _, sig = tok.partition(".")
    attendu = hmac.new(SESSION_SECRET, exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, attendu):   # comparaison a temps constant
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def sb():
    global _sb
    if _sb is None:
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY absents de .env")
        _sb = create_client(url, key)
    return _sb


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def slugify_version(version: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(version or "")).strip("_")


def card_id(card_number: str, version: str, set_id: str) -> str:
    """Même convention que les scrapers : {numero}_{version}_{set}."""
    return f"{card_number}_{slugify_version(version)}_{set_id}"


def fetch_all(table: str, columns: str = "*", **eq):
    rows, off = [], 0
    while True:
        q = sb().table(table).select(columns)
        for k, v in eq.items():
            q = q.eq(k, v)
        res = q.range(off, off + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            return rows
        off += 1000


def clean_payload(raw: dict) -> dict:
    """Ne garde que les colonnes connues, normalise types et vides."""
    out = {}
    for col in CARD_COLUMNS:
        if col not in raw:
            continue
        v = raw[col]
        if col in ARRAY_COLUMNS:
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",") if x.strip()]
            out[col] = v or []
        elif col in INT_COLUMNS:
            if v in ("", None):
                out[col] = None
            else:
                try:
                    out[col] = int(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{col} doit être un entier (reçu {v!r})")
        elif col == "locked":
            out[col] = bool(v)
        else:
            out[col] = None if v == "" and col in ("attribute", "effect", "trigger_effect",
                                                   "character_name", "image_filename") else v
    return out


# ---------------------------------------------------------------------------
# Handlers d'API. Chacun retourne (status, objet JSON).

def api_sets():
    sets = fetch_all("poneglyph_sets")
    cards = fetch_all("poneglyph_cards", "set_id,locked")
    counts, locked = {}, {}
    for c in cards:
        counts[c["set_id"]] = counts.get(c["set_id"], 0) + 1
        if c.get("locked"):
            locked[c["set_id"]] = locked.get(c["set_id"], 0) + 1
    for s in sets:
        s["cards_en_base"] = counts.get(s["set_id"], 0)
        s["cards_verrouillees"] = locked.get(s["set_id"], 0)
    sets.sort(key=lambda s: s["set_id"])
    return 200, {"sets": sets, "total_cartes": len(cards)}


def api_cards(set_id: str):
    rows = fetch_all("poneglyph_cards", "*", set_id=set_id)
    rows.sort(key=lambda c: (c["card_number"], c["version"]))
    return 200, {"set_id": set_id, "total": len(rows), "cards": rows}


def api_search(q: str, limit: int = 300):
    if not q or len(q) < 2:
        return 400, {"error": "Recherche trop courte (2 caractères minimum)."}
    needle = fold(q)
    rows = fetch_all("poneglyph_cards", "*")
    hits = [c for c in rows if needle in fold(
        f"{c['card_number']} {c['name']} {c['set_id']} {c['version']} "
        f"{c.get('character_name') or ''}")]
    hits.sort(key=lambda c: (c["set_id"], c["card_number"], c["version"]))
    return 200, {"total": len(hits), "tronque": len(hits) > limit, "cards": hits[:limit]}


def api_update(cid: str, payload: dict):
    cur = sb().table("poneglyph_cards").select("*").eq("id", cid).execute().data
    if not cur:
        return 404, {"error": f"Carte introuvable : {cid}"}
    cur = cur[0]

    row = {**{k: cur[k] for k in CARD_COLUMNS if k in cur}, **clean_payload(payload)}
    row["locked"] = True  # toute edition manuelle verrouille la ligne

    for champ in ("card_number", "version", "set_id", "name", "type", "rarity"):
        if not row.get(champ):
            return 400, {"error": f"Champ obligatoire vide : {champ}"}

    new_id = card_id(row["card_number"], row["version"], row["set_id"])

    if new_id == cid:
        sb().table("poneglyph_cards").update(row).eq("id", cid).execute()
        return 200, {"ok": True, "id": cid, "id_change": False,
                     "message": "Carte enregistrée et verrouillée."}

    # Le trio (numero, version, set) a change : l'identifiant aussi.
    clash = sb().table("poneglyph_cards").select("id").eq("id", new_id).execute().data
    if clash:
        return 409, {"error": f"Une carte porte déjà l'identifiant {new_id}. "
                              f"Modifiez le numéro, la version ou le set."}
    row["id"] = new_id
    row["created_at"] = cur.get("created_at")
    sb().table("poneglyph_cards").insert(row).execute()
    sb().table("poneglyph_cards").delete().eq("id", cid).execute()
    return 200, {"ok": True, "id": new_id, "id_change": True, "ancien_id": cid,
                 "message": f"Carte enregistrée, verrouillée, et réidentifiée en {new_id}."}


def api_create(payload: dict):
    row = clean_payload(payload)
    for champ in ("card_number", "version", "set_id", "name", "type", "rarity"):
        if not row.get(champ):
            return 400, {"error": f"Champ obligatoire vide : {champ}"}
    for col in CARD_COLUMNS:
        row.setdefault(col, [] if col in ARRAY_COLUMNS else None)
    row["locked"] = True
    row["id"] = card_id(row["card_number"], row["version"], row["set_id"])

    if sb().table("poneglyph_cards").select("id").eq("id", row["id"]).execute().data:
        return 409, {"error": f"Une carte porte déjà l'identifiant {row['id']}."}
    if not sb().table("poneglyph_sets").select("set_id").eq("set_id", row["set_id"]).execute().data:
        return 400, {"error": f"Le set {row['set_id']} n'existe pas en base."}

    sb().table("poneglyph_cards").insert(row).execute()
    return 200, {"ok": True, "id": row["id"], "message": "Carte créée et verrouillée."}


def api_duplicate(cid: str):
    cur = sb().table("poneglyph_cards").select("*").eq("id", cid).execute().data
    if not cur:
        return 404, {"error": f"Carte introuvable : {cid}"}
    row = {k: v for k, v in cur[0].items() if k in CARD_COLUMNS}

    base = row["version"] or "Standard"
    for n in range(2, 40):
        essai = f"{base} copie {n}" if n > 2 else f"{base} copie"
        nid = card_id(row["card_number"], essai, row["set_id"])
        if not sb().table("poneglyph_cards").select("id").eq("id", nid).execute().data:
            row["version"], row["id"] = essai, nid
            break
    else:
        return 409, {"error": "Trop de copies existantes pour cette carte."}

    row["locked"] = True
    sb().table("poneglyph_cards").insert(row).execute()
    return 200, {"ok": True, "id": row["id"],
                 "message": f"Copie créée sous la version « {row['version']} ». "
                            f"À vous de la renommer et de l'ajuster."}


def clean_set_payload(raw: dict) -> dict:
    """Ne garde que les colonnes de set connues, normalise types et vides."""
    out = {}
    for col in SET_COLUMNS:
        if col not in raw:
            continue
        v = raw[col]
        if isinstance(v, str):
            v = v.strip()
        if col == "card_count":
            if v in ("", None):
                out[col] = None
            else:
                try:
                    out[col] = int(v)
                except (TypeError, ValueError):
                    raise ValueError(f"card_count doit être un entier (reçu {v!r})")
        elif col == "release_date":
            out[col] = v or None
        else:
            out[col] = v
    return out


def count_cards(set_id: str) -> int:
    return sb().table("poneglyph_cards").select("id", count="exact") \
               .eq("set_id", set_id).limit(1).execute().count or 0


_tracked_images: set[str] | None = None


def tracked_images() -> set[str]:
    """Images déjà commitées : sert à marquer celles qui sont en ligne."""
    global _tracked_images
    if _tracked_images is None:
        import subprocess
        try:
            out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "images"],
                                 capture_output=True, text=True, timeout=60).stdout
            _tracked_images = {l.split("/")[-1] for l in out.split() if l}
        except Exception:
            _tracked_images = set()
    return _tracked_images


def api_cleanup_refresh():
    """Recalcule le plan de cleanup contre l'état actuel et réécrit le fichier.

    Dry-run : aucune écriture en base. Les entrées déjà résolues à la main
    disparaissent d'elles-mêmes puisque le plan est recalculé de zéro."""
    import audit
    res = audit.rebuild(sb(), tracked_images())
    log.info("audit cleanup recalculé : %d action(s) sur %d carte(s)",
             res["actions"], res["cartes_en_base"])
    return 200, res


def api_set_create(payload: dict):
    row = clean_set_payload(payload)
    for champ in SET_REQUIRED:
        if not row.get(champ):
            return 400, {"error": f"Champ obligatoire vide : {champ}"}
    if re.search(r"\s", row["set_id"]):
        return 400, {"error": "set_id ne doit pas contenir d'espace."}
    if sb().table("poneglyph_sets").select("set_id").eq("set_id", row["set_id"]).execute().data:
        return 409, {"error": f"Le set {row['set_id']} existe déjà."}
    for col in SET_COLUMNS:
        row.setdefault(col, None)
    sb().table("poneglyph_sets").insert(row).execute()
    return 200, {"ok": True, "set_id": row["set_id"], "message": f"Set {row['set_id']} créé."}


def api_set_update(sid: str, payload: dict):
    cur = sb().table("poneglyph_sets").select("*").eq("set_id", sid).execute().data
    if not cur:
        return 404, {"error": f"Set introuvable : {sid}"}
    row = {**{k: cur[0][k] for k in SET_COLUMNS if k in cur[0]}, **clean_set_payload(payload)}
    for champ in SET_REQUIRED:
        if not row.get(champ):
            return 400, {"error": f"Champ obligatoire vide : {champ}"}

    if row["set_id"] != sid:
        # set_id est la cle primaire, referencee par poneglyph_cards et encodee
        # dans l'identifiant de chaque carte. On ne la renomme que si le set est
        # vide : sinon il faudrait reecrire l'id de toutes ses cartes.
        n = count_cards(sid)
        if n:
            return 409, {"error": f"Impossible de renommer {sid} en {row['set_id']} : "
                                  f"{n} carte(s) y sont rattachées. Déplacez-les d'abord "
                                  f"depuis l'éditeur de cartes, ou créez le nouveau set à côté."}
        if re.search(r"\s", row["set_id"]):
            return 400, {"error": "set_id ne doit pas contenir d'espace."}
        if sb().table("poneglyph_sets").select("set_id").eq("set_id", row["set_id"]).execute().data:
            return 409, {"error": f"Le set {row['set_id']} existe déjà."}
        row["created_at"] = cur[0].get("created_at")
        sb().table("poneglyph_sets").insert(row).execute()
        sb().table("poneglyph_sets").delete().eq("set_id", sid).execute()
        return 200, {"ok": True, "set_id": row["set_id"], "id_change": True,
                     "message": f"Set renommé {sid} → {row['set_id']}."}

    sb().table("poneglyph_sets").update(row).eq("set_id", sid).execute()
    return 200, {"ok": True, "set_id": sid, "id_change": False,
                 "message": f"Set {sid} enregistré."}


def api_set_delete(sid: str, cascade: bool):
    cur = sb().table("poneglyph_sets").select("*").eq("set_id", sid).execute().data
    if not cur:
        return 404, {"error": f"Set introuvable : {sid}"}
    n = count_cards(sid)

    # Garde-fou : jamais de suppression en cascade silencieuse. Sans demande
    # explicite, un set peuple est refuse et l'appelant recoit le compte exact.
    if n and not cascade:
        return 409, {"error": f"Le set {sid} contient {n} carte(s). "
                              f"Suppression refusée sans confirmation explicite.",
                     "cards": n, "set_id": sid, "needs_cascade": True}

    if n:
        ids = [c["id"] for c in fetch_all("poneglyph_cards", "id", set_id=sid)]
        for i in range(0, len(ids), 100):
            sb().table("poneglyph_cards").delete().in_("id", ids[i:i + 100]).execute()
    sb().table("poneglyph_sets").delete().eq("set_id", sid).execute()
    return 200, {"ok": True, "set_id": sid, "cards_supprimees": n,
                 "message": (f"Set {sid} supprimé avec ses {n} carte(s)." if n
                             else f"Set {sid} supprimé (il était vide).")}


def api_delete(cid: str):
    cur = sb().table("poneglyph_cards").select("id,name,card_number").eq("id", cid).execute().data
    if not cur:
        return 404, {"error": f"Carte introuvable : {cid}"}
    sb().table("poneglyph_cards").delete().eq("id", cid).execute()
    return 200, {"ok": True, "id": cid,
                 "message": f"{cur[0]['card_number']} — {cur[0]['name']} supprimée."}


# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if "/api/" in (self.path or ""):
            log.info("%s %s", self.command, self.path)

    def _send(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- authentification -------------------------------------------------

    def _session(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            return SimpleCookie(raw)[COOKIE].value
        except KeyError:
            return None

    def authed(self) -> bool:
        return token_ok(self._session())

    def _set_cookie(self, value, max_age):
        self.send_header(
            "Set-Cookie",
            f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict")

    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def guard(self, path) -> bool:
        """True si la requete peut continuer. Sinon a deja repondu 401 ou 302.

        Le refus est cote serveur, pour l'API comme pour les fichiers : masquer
        une page sans proteger l'API laisserait passer un appel direct."""
        if path in PUBLIC_PATHS or self.authed():
            return True
        if path.startswith("/api/"):
            self._send(401, {"error": "Non authentifié.", "login": "/dashboard/login.html"})
        else:
            self._redirect("/dashboard/login.html")
        return False

    def api_login(self):
        fourni = str(self._body().get("password") or "")
        if not hmac.compare_digest(fourni, password()):
            log.warning("connexion refusée depuis %s", self.client_address[0])
            return self._send(401, {"error": "Mot de passe incorrect."})
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cookie(make_token(), SESSION_TTL)
        self.end_headers()
        self.wfile.write(body)

    def api_logout(self):
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._set_cookie("", 0)
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        u = urlparse(self.path)
        return u.path, parse_qs(u.query)

    def _dispatch(self, fn, *a, **k):
        try:
            status, obj = fn(*a, **k)
        except ValueError as exc:
            status, obj = 400, {"error": str(exc)}
        except Exception as exc:
            log.exception("erreur API")
            status, obj = 500, {"error": f"{type(exc).__name__}: {exc}"}
        self._send(status, obj)

    def do_GET(self):
        path, qs = self._route()
        if path in ("/", "/dashboard", "/dashboard/"):
            return self._redirect("/dashboard/editor.html" if self.authed()
                                  else "/dashboard/login.html")
        if not self.guard(path):
            return
        if not path.startswith("/api/"):
            return super().do_GET()
        if path == "/api/sets":
            return self._dispatch(api_sets)
        if path == "/api/cards":
            sid = (qs.get("set_id") or [""])[0]
            if not sid:
                return self._send(400, {"error": "set_id requis"})
            return self._dispatch(api_cards, sid)
        if path == "/api/search":
            return self._dispatch(api_search, (qs.get("q") or [""])[0])
        if path == "/api/columns":
            return self._send(200, {"colonnes": CARD_COLUMNS,
                                    "tableaux": sorted(ARRAY_COLUMNS),
                                    "entiers": sorted(INT_COLUMNS)})
        self._send(404, {"error": "endpoint inconnu"})

    def do_POST(self):
        path, qs = self._route()
        if path == "/api/login":
            return self.api_login()
        if not self.guard(path):
            return
        if path == "/api/logout":
            return self.api_logout()
        if path == "/api/card":
            return self._dispatch(api_create, self._body())
        if path == "/api/set":
            return self._dispatch(api_set_create, self._body())
        if path == "/api/cleanup-refresh":
            return self._dispatch(api_cleanup_refresh)
        if path == "/api/duplicate":
            return self._dispatch(api_duplicate, (qs.get("id") or [""])[0])
        self._send(404, {"error": "endpoint inconnu"})

    def do_PUT(self):
        path, qs = self._route()
        if not self.guard(path):
            return
        if path == "/api/card":
            cid = (qs.get("id") or [""])[0]
            if not cid:
                return self._send(400, {"error": "id requis"})
            return self._dispatch(api_update, cid, self._body())
        if path == "/api/set":
            sid = (qs.get("id") or [""])[0]
            if not sid:
                return self._send(400, {"error": "id requis"})
            return self._dispatch(api_set_update, sid, self._body())
        self._send(404, {"error": "endpoint inconnu"})

    def do_DELETE(self):
        path, qs = self._route()
        if not self.guard(path):
            return
        if path == "/api/card":
            cid = (qs.get("id") or [""])[0]
            if not cid:
                return self._send(400, {"error": "id requis"})
            return self._dispatch(api_delete, cid)
        if path == "/api/set":
            sid = (qs.get("id") or [""])[0]
            if not sid:
                return self._send(400, {"error": "id requis"})
            cascade = (qs.get("cascade") or ["0"])[0] in ("1", "true", "yes")
            return self._dispatch(api_set_delete, sid, cascade)
        self._send(404, {"error": "endpoint inconnu"})


def main():
    ap = argparse.ArgumentParser(description="Éditeur manuel Ponéglyphe (local)")
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()

    password()  # échoue tout de suite si DASHBOARD_PASSWORD manque
    sb()        # idem si les identifiants Supabase manquent
    srv = ThreadingHTTPServer(("127.0.0.1", args.port),
                              partial(Handler, directory=str(REPO_ROOT)))
    log.info("éditeur : http://127.0.0.1:%d/dashboard/editor.html", args.port)
    log.info("sets : http://127.0.0.1:%d/dashboard/sets.html", args.port)
    log.info("revue cleanup : http://127.0.0.1:%d/dashboard/cleanup-review.html", args.port)
    log.info("connexion : http://127.0.0.1:%d/dashboard/login.html", args.port)
    log.info("authentification active (DASHBOARD_PASSWORD) — Ctrl+C pour arrêter.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("arrêt.")


if __name__ == "__main__":
    main()
