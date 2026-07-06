# 🏴‍☠️ TANUKI-PONEGLYPH

**Première API publique gratuite des cartes One Piece TCG en français.**

Données scrapées depuis le site officiel Bandai FR, servies en JSON statique + API REST filtrable sur Cloudflare.

- **Base URL** : `https://poneglyph.tanuki-corporation.com`
- **Gratuit, sans clé API, CORS ouvert** (`Access-Control-Allow-Origin: *`)
- Open source (MIT) — un outil communautaire par [Tanuki Corporation](https://tanuki-corporation.com)

---

## Endpoints

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/v1/cards` | Toutes les cartes (paginé, filtrable) |
| GET | `/v1/cards/:id` | Fiche d'une carte par slug (ex. `OP16-001_standard_OP16`) |
| GET | `/v1/sets` | Liste des sets |
| GET | `/v1/sets/:set_id` | Un set et toutes ses cartes (ex. `OP16`) |
| GET | `/v1/filters` | Valeurs disponibles pour chaque filtre |

Les fichiers statiques bruts sont aussi accessibles directement :
`/v1/cards.json`, `/v1/sets.json`, `/v1/filters.json`, `/v1/sets/OP16.json`, `/v1/cards/{id}.json`, `/images/{fichier}.webp`.

## Filtres de `/v1/cards`

| Param | Type | Exemple | Description |
|---|---|---|---|
| `name` | string | `?name=luffy` | Recherche texte, insensible casse et accents (contains) |
| `card_number` | string | `?card_number=OP16-001` | Numéro exact |
| `type` | string | `?type=PERSONNAGE,LEADER` | Multi-valeurs (OU), séparées par virgule |
| `color` | string | `?color=Rouge,Bleu` | Multi-valeurs (OU) |
| `attribute` | string | `?attribute=Frappe` | Multi-valeurs |
| `rarity` | string | `?rarity=SR,SEC` | Multi-valeurs |
| `set` | string | `?set=OP16` | Multi-valeurs |
| `set_type` | string | `?set_type=OP,ST` | Multi-valeurs |
| `version` | string | `?version=Alternative Art` | Multi-valeurs |
| `character` | string | `?character=Monkey D. Luffy` | Multi-valeurs |
| `affiliation` | string | `?affiliation=Équipage de Chapeau de paille` | Multi-valeurs |
| `ability` | string | `?ability=Bloqueur` | Multi-valeurs |
| `power_eq` / `power_gte` / `power_lte` | int | `?power_gte=5000` | Puissance exacte / >= / <= |
| `cost_eq` / `cost_gte` / `cost_lte` | int | `?cost_lte=7` | Coût exact / >= / <= |
| `life_eq` | int | `?life_eq=5` | Vie exacte (Leaders) |
| `counter_eq` | int | `?counter_eq=2000` | Contre exact |
| `sort` | string | `?sort=power_desc` | `name`, `power`, `cost`, `card_number`, `rarity` + `_asc`/`_desc` |
| `limit` | int | `?limit=20` | Pagination (défaut 50, max 200) |
| `offset` | int | `?offset=40` | Offset de pagination |

**Logique multi-valeurs** : `?color=Rouge,Bleu` retourne les cartes Rouge **OU** Bleu. Pour les champs tableaux (`affiliation`, `ability`), la carte matche si elle contient **au moins une** des valeurs demandées.

### Exemple

```
GET /v1/cards?color=Rouge&type=PERSONNAGE&power_gte=5000&sort=power_desc&limit=5
```

```json
{
  "schema_version": "1.0",
  "total": 2200,
  "filtered": 45,
  "limit": 5,
  "offset": 0,
  "cards": [
    {
      "id": "OP16-001_standard_OP16",
      "card_number": "OP16-001",
      "version": "Standard",
      "set_id": "OP16",
      "name": "Portgas D. Ace",
      "type": "LEADER",
      "colors": ["Rouge"],
      "attribute": "Spécial",
      "power": 5000,
      "cost": null,
      "counter": null,
      "life": 5,
      "effect": "[Activation : Principal] [Une fois par tour] ...",
      "trigger_effect": null,
      "rarity": "L",
      "character_name": "Portgas D. Ace",
      "affiliations": ["Équipage de Barbe Blanche"],
      "abilities": [],
      "image": "/images/OP16-001_standard.webp",
      "block_number": 5
    }
  ]
}
```

Les images sont servies en WebP (max 800 px de large) : `https://poneglyph.tanuki-corporation.com/images/OP16-001_standard.webp`.

---

## Architecture

```
Scraper Playwright (GitHub Actions, cron mensuel)
    ↓ parse fr.onepiece-cardgame.com/cardlist/
Supabase (source de vérité privée — jamais exposée)
    ↓ build/generate.py
JSON statiques + images WebP
    ↓ Cloudflare Pages + Cloudflare Worker (filtres REST)
poneglyph.tanuki-corporation.com
```

| Dossier | Rôle |
|---|---|
| `scraper/` | Playwright Python : parse le cardlist Bandai FR, télécharge et convertit les images en WebP, upsert Supabase |
| `build/` | Génère `dist/` (JSON statiques) depuis Supabase + valide l'intégrité |
| `worker/` | Cloudflare Worker TypeScript : API REST filtrable (données embarquées en statique) |
| `supabase/` | Migration SQL du schéma (tables `poneglyph_sets`, `poneglyph_cards`, RLS privé) |
| `images/` | Images WebP commitées par le workflow de scraping |
| `.github/workflows/` | `scrape.yml` (cron mensuel), `build-deploy.yml`, `keep-alive.yml` (ping quotidien Supabase) |

## Développement local

```bash
# 1. Secrets
cp .env.example .env          # remplir SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY

# 2. Schéma Supabase (SQL Editor du dashboard, ou supabase db push)
#    → supabase/migrations/0001_init.sql

# 3. Scraper
pip install -r scraper/requirements.txt
playwright install chromium
python scraper/scrape.py --series OP16      # un set pour tester

# 4. Build des JSON statiques
pip install -r build/requirements.txt
python build/generate.py && python build/validate.py

# 5. Worker en local
cd worker && npm install && npm run dev     # http://localhost:8787/v1/cards
```

### Secrets GitHub Actions à configurer

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.

⚠️ **Jamais de vraies valeurs commitées** — uniquement `.env.example`.

---

## Crédits & mentions légales

- **One Piece Card Game** © Eiichiro Oda / Shueisha, Toei Animation — © **Bandai**
- Données et images issues du site officiel [fr.onepiece-cardgame.com](https://fr.onepiece-cardgame.com/cardlist/)
- Projet **communautaire non officiel**, sans affiliation avec Bandai. Aucun usage commercial des assets.
- API développée par **Tanuki Corporation** — licence [MIT](LICENSE) pour le code.
