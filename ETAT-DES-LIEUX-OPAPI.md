# État des lieux — TANUKI-PONEGLYPH (API cartes One Piece FR)

**Audit en lecture seule — 20 août 2026.** Aucun fichier du projet modifié, aucune dépendance installée, aucun scraper exécuté. Seules des requêtes HTTP unitaires de vérification de statut ont été émises.

> **Résumé en une phrase** — Le projet n'est pas un chantier à l'abandon : c'est une API en production, déployée et vivante, avec 3 499 cartes FR et 3 411 images servies publiquement. Le nom `opecards_scraper_v2.py` du souvenir n'existe pas ici ; il a été remplacé par une architecture complète à deux sources (Bandai FR + opecards.fr), et opecards.fr n'est qu'une source secondaire d'appoint (235 cartes ajoutées sur 3 499).

---

## 1. Inventaire des fichiers et stack

**Racine** : `c:\Users\tanuki\Desktop\PonéglypheAPI` — dépôt git, branche `main`, remote `git@github.com:TNK-Sempai/tanuki-poneglyph.git`. 3 commits, dernier : `562835b feat: multi-source scraping (Bandai FR + opecards), cleanup, 3499 cards`.

**Stack** : Python 3.12 (scraping + build) + TypeScript (Cloudflare Worker) + Supabase Postgres (source de vérité privée) + Cloudflare Pages/Worker (diffusion). Licence MIT.

| Chemin | Rôle | Volume |
|---|---|---|
| `scraper/scrape.py` | Scraper Playwright du cardlist officiel **Bandai FR** (`fr.onepiece-cardgame.com/cardlist/`) vers Supabase. Source primaire. | 464 lignes |
| `scraper/scrape_opecards.py` | Scraper complémentaire **opecards.fr** — n'insère que les cartes absentes du triplet `(card_number, version, set_id)`. | 621 lignes |
| `scraper/cleanup.py` | Audit + correction des doublons et mauvaises assignations issus de la fusion des deux sources. | 476 lignes |
| `scraper/upload_images.py` | Téléchargement + conversion WebP (qualité 85, max 800 px). | 58 lignes |
| `scraper/report*.json` | Rapports des 3 derniers runs (7–8 juillet 2026). | 3 fichiers |
| `build/generate.py` | Supabase vers `dist/` (JSON statiques) + `worker/data/*.json`. | 197 lignes |
| `build/validate.py` | Validation d'intégrité de `dist/` avant deploy (exit 1 bloquant). | 90 lignes |
| `worker/src/index.ts` | Cloudflare Worker : API REST filtrable, données embarquées en statique (pas de fetch runtime). | 253 lignes |
| `supabase/migrations/0001_init.sql` | Schéma : `poneglyph_sets`, `poneglyph_cards`, RLS activé sans policy (BDD strictement privée). | 1 fichier |
| `dist/` | Sortie build : `v1/cards.json`, `v1/sets.json`, `v1/filters.json`, 3 499 fiches unitaires, 40 fiches de set, 3 411 images. **Gitignoré** (régénéré en CI). | 331 Mo |
| `images/` | Images WebP sources, **commitées** (3 411 fichiers). | 314 Mo |
| `.github/workflows/` | `scrape.yml` (cron mensuel, ouvre une Issue en cas d'échec), `build-deploy.yml`, `keep-alive.yml` (ping Supabase quotidien). | 3 workflows |

Dépendances : `playwright>=1.45`, `beautifulsoup4>=4.12`, `Pillow>=10.0`, `supabase>=2.5`, `requests>=2.32`, `python-dotenv>=1.0` côté Python ; `wrangler ^4.0`, `typescript ^5.5`, `@cloudflare/workers-types` côté Worker. Toutes des versions courantes et installables — `worker/node_modules/` est déjà présent, et `.claude/settings.local.json` conserve la trace de vérifications d'environnement réussies (`deps OK`, `chromium OK`) lors des sessions de juillet.

**Fichier `.env`** : `SUPABASE_URL` et `SUPABASE_SERVICE_ROLE_KEY` sont remplis ; `CLOUDFLARE_API_TOKEN` et `CLOUDFLARE_ACCOUNT_ID` sont vides (le déploiement Cloudflare est passé par `wrangler login`, pas par token — cf. `.wrangler/cache/wrangler-account.json`).

---

## 2. Statut des sources aujourd'hui (20 août 2026)

| URL testée | Résultat |
|---|---|
| `https://api.opecards.fr/` | **HTTP 000 — DNS ne résout pas** (`Non-existent domain`). L'ancienne API est bien morte. |
| `https://www.opecards.fr/` | **HTTP 200** (1,4 s) — le site web est vivant, IP `146.59.234.20`. |
| `https://opecards.fr/` | **HTTP 200**, redirige vers `www.`. |
| `https://fr.onepiece-cardgame.com/cardlist/` | **HTTP 200** — redirige vers `?series=622116`. La source primaire est vivante. |
| `https://tanuki-poneglyph.pages.dev/v1/sets.json` | **HTTP 200 — 40 sets servis. L'API statique est EN LIGNE.** |
| `https://tanuki-poneglyph.pages.dev/images/OP16-001_standard.webp` | **HTTP 200** — les images sont servies. |
| `https://tanuki-poneglyph.pages.dev/v1/cards?name=luffy` | **HTTP 404** — le Worker (filtres REST) n'est pas déployé. |
| `https://poneglyph.tanuki-corporation.com/v1/sets` | **HTTP 000 — DNS ne résout pas.** Le domaine public annoncé dans le README n'existe pas encore. |

`tanuki-corporation.com` résout bien (185.199.108–111.153, GitHub Pages) ; c'est uniquement le sous-domaine `poneglyph.` qui manque.

---

## 3. Ce qui fonctionne déjà — avec preuves

### 3.1 Données collectées

- **3 499 cartes**, **40 sets**, **3 411 images WebP** — `dist/v1/cards.json` (2,2 Mo), généré le **7 juillet 2026 à 02:51 UTC** (`dist/schema_version.json` : `{"version":"1.0","generated_at":"2026-07-07T02:51:01.753765+00:00"}`).
- **Zéro carte sans image, sans nom ou sans rareté.** Vérifié sur les 3 499 lignes.
- Répartition des versions : 1 724 Standard, 739 Alternative Art, 272 Version 2, 218 Alternative Art 2, 98 Alternative Art 3, puis Jolly Roger Foil / Full Art / Gold / Manga / Special Card… — **57 libellés de version distincts**.
- Raretés : C 957, R 767, SR 547, UC 526, L 244, SEC 136, P 135, DON!! 92, SP CARD 73, TR 9, plus variantes SP.
- Champs texte FR complets : `effect`, `trigger_effect`, `character_name` (752 personnages distincts), `affiliations` (240 valeurs), `abilities`, `attribute`, `power` / `cost` / `counter` / `life`.
- 237 cartes non-DON sans `effect` (vanilla ou effet non extrait) — à considérer comme la marge d'erreur connue.

### 3.2 Preuve que les scrapers ont réellement tourné

- `scraper/report.json` (7 juillet, 00:37) : **30 sets, tous en `status: OK`, `errors: []`, `ok: true`** — chaque set validé par comparaison `scraped` vs `expected` (PRB02 316/316, PRB01 319/319, OP16 155/155, OP13 175/175…).
- `scraper/report_opecards.json` (7 juillet, 04:22) : `opecards_total_fr: 2627`, `db_total_before: 3644`, `db_total_after: 3879`, **235 cartes ajoutées** (dont 92 DON!!, 4 promos), 227 images téléchargées, 2 392 déjà présentes, 1 nouveau set.
- `scraper/report_cleanup.json` (8 juillet, 16:43) : après nettoyage, `total_cards: 3499` dont **2 620 lignes d'origine Bandai et 879 d'origine opecards**, `duplicates: 0`, `reassignments: 0`, `empty_sets: 0`, `orphan_images: 0`, et **26 cas ambigus documentés** (cartes SP dont le `card_number` existe dans plusieurs sets Bandai — arbitrage manuel volontairement laissé en attente).

### 3.3 Diffusion

L'API **statique est déployée et publique** sur `https://tanuki-poneglyph.pages.dev` (projet Cloudflare Pages `tanuki-poneglyph`, compte `7a827fc0…`). Le contenu en ligne est identique au `dist/` local : même `generated_at` du 7 juillet 2026, 40 sets, images accessibles. Ce ne sont donc pas des données mortes sur un disque — elles sont déjà servies sur Internet.

Fichiers directement consommables aujourd'hui, sans aucun travail supplémentaire :
`/v1/cards.json`, `/v1/sets.json`, `/v1/filters.json`, `/v1/sets/{SET_ID}.json`, `/v1/cards/{id}.json`, `/images/{fichier}.webp`.

### 3.4 Ce qui ne fonctionne pas encore

| Élément | Statut |
|---|---|
| Worker REST (`/v1/cards?filtres`) | Codé (253 lignes, une vingtaine de filtres, tri, pagination) et testé en local (`wrangler dev` port 8787, trace dans `.claude/settings.local.json`) mais **jamais déployé** : `routes` commenté dans `wrangler.toml`. |
| Domaine `poneglyph.tanuki-corporation.com` | **DNS inexistant.** Le README documente une URL qui ne répond pas. |
| Fraîcheur | Données figées au **7 juillet 2026**, soit environ 6 semaines. Le cron `scrape.yml` (1er du mois, 03h00 UTC) aurait dû produire un rafraîchissement le 1er août ; le `generated_at` en ligne prouve que **rien n'a été republié depuis le 7 juillet**. À vérifier côté GitHub Actions (workflow jamais déclenché, ou secrets `SUPABASE_*` / `CLOUDFLARE_*` absents du dépôt). |
| Travail en cours non commité | 4 fichiers modifiés (`scrape.py`, `scrape_opecards.py`, `cleanup.py`, `report_cleanup.json`, +102/−14 lignes) : ajout d'une notion de **carte verrouillée** (`locked=true` = corrigée à la main, jamais réécrasée par un re-scrape). Le code est complet et cohérent, mais **la colonne `locked` n'existe pas dans `supabase/migrations/0001_init.sql`** — elle a été créée directement en base (le rapport `report_cleanup.json` contient déjà `locked_protected: 0`, donc le code a tourné). **Dérive de schéma à régulariser.** |

---

## 4. Écart précis à combler pour servir Goriki

### 4.1 Ce que Goriki consomme aujourd'hui

`Goriki/goriki/lib/opecards.ts` tape sur `https://api.opecards.fr` (mort) et attend :

```ts
OPESet  = { id, name, code, releaseDate?, cardCount?, image? }
OPECard = { id, number, name, image?, rarity?, type?, color?, power?, life? }
// endpoints : GET /sets  et  GET /sets/{setId}/cards
```

`app/api/import/onepiece/route.ts` mappe ensuite vers `onepiece_sets` (upsert `onConflict: 'code'`) et `onepiece_cards` (upsert **`onConflict: 'set_id,number'`**), puis crée un listing STANDARD par carte.

### 4.2 Correspondance avec ce que Poneglyph sert déjà

| Besoin Goriki | Poneglyph | Verdict |
|---|---|---|
| `GET /sets` | `GET /v1/sets.json` (200 en ligne) | OK — renommage de champs seulement |
| `GET /sets/{id}/cards` | `GET /v1/sets/{SET_ID}.json` — renvoie le set **et** son tableau `cards` complet | OK — même forme |
| `card.number` | `card_number` | OK — renommage |
| `card.name` (FR) | `name` (FR officiel Bandai) | OK |
| `card.rarity` | `rarity` | OK |
| `card.image` | `image` = chemin **relatif** `/images/XXX.webp` | À adapter — préfixer par l'origine |
| `card.type` | `type` (LEADER / PERSONNAGE / ÉVÉNEMENT / LIEU / DON!!) | OK |
| `card.color` (string) | `colors` (**tableau**) — 80 cartes multicolores, 92 sans couleur | À adapter — aplatir (`join(', ')` ou `colors[0]`) |
| `card.power`, `card.life` | `power`, `life` | OK |
| `set.code` | `set_id` | OK — renommage |
| `set.cardCount` | `card_count` — **null sur 10 des 40 sets** | À adapter — fallback `cards.length` (déjà prévu côté Goriki) |
| `set.releaseDate` | `release_date` — **null sur 40/40** | Manquant — donnée jamais collectée |
| `set.image` | inexistant | Manquant — champ optionnel côté Goriki, sans impact |
| Variantes | `version` + `id` slug `{numéro}_{version}_{set}` | **Voir 4.3** |

### 4.3 Le vrai point dur : les variantes

Poneglyph est **plus riche que ce que Goriki sait absorber**. Sur 3 499 lignes il n'y a que **1 960 couples `(set_id, card_number)` distincts** : 783 couples portent plusieurs variantes. Comme Goriki upserte sur `onConflict: 'set_id,number'`, un import brut **écraserait 1 539 lignes** (44 % des données) — seule la dernière variante rencontrée survivrait, de façon non déterministe.

Trois issues possibles, à trancher côté Goriki :

1. **Filtrer** `version === 'Standard'` à l'import → 1 724 cartes propres, variantes perdues. Le plus rapide, et cohérent avec le modèle actuel de Goriki (les variantes y vivent au niveau `onepiece_listings` / `onepiece_variant_types`, pas `onepiece_cards`).
2. **Mapper les variantes Poneglyph sur `onepiece_variant_types`** — c'est l'usage prévu par le schéma Goriki, mais il faut une table de correspondance entre les 57 libellés Poneglyph et les codes de variantes Goriki.
3. **Ajouter une colonne `version`** à `onepiece_cards` et passer la clé d'unicité à `(set_id, number, version)` → migration Goriki.

### 4.4 Couverture des sets — limite structurelle à connaître

Les 40 sets couverts vont de **OP09 à OP16**, **ST15 à ST30**, **EB01–EB03**, **PRB01/PRB02**, plus `PROMO`, `OTHER`, `DPS07`. Les sets anciens sont quasi absents : **aucune carte OP01, OP02, OP04** ; OP03 (3 cartes), OP05 (2), OP06 (7), OP07 (3), OP08 (2) ; **aucun ST01–ST14** (ST02 : 1 carte, ST04 : 1 carte).

Ce n'est pas un bug du scraper : ni le cardlist Bandai FR ni la liste FR d'opecards.fr (2 627 cartes au total, cf. `report_opecards.json`) ne référencent ces séries — vraisemblablement parce que l'édition française du jeu a démarré à OP09/ST15, les cartes antérieures n'étant rééditées en FR que via PRB01/PRB02. **Point à confirmer avant de promettre à Goriki une couverture « complète ».** Si Goriki a besoin des sets anciens, aucune source FR identifiée ici ne les fournira.

---

## 5. Estimation de taille des morceaux manquants

| # | Morceau manquant | Taille | Détail |
|---|---|---|---|
| 1 | **Réécrire `Goriki/lib/opecards.ts`** vers `https://tanuki-poneglyph.pages.dev` | **S** | Environ 50 lignes : changer la base, renommer les champs, préfixer `image`, aplatir `colors`. Le contrat `fetchOPESets()` / `fetchOPESet()` reste identique, donc `route.ts` est inchangé. **Faisable aujourd'hui, l'endpoint répond déjà.** |
| 2 | **Décision + implémentation variantes** (§4.3) | **S** si option 1 (filtre `Standard`, quelques lignes) · **M** si option 2 ou 3 (migration Goriki + table de correspondance) |
| 3 | **DNS `poneglyph.tanuki-corporation.com`** + décommenter `routes` dans `wrangler.toml` | **S** | Un CNAME Cloudflare et un `wrangler deploy`. Cosmétique tant que `pages.dev` suffit. |
| 4 | **Déployer le Worker REST** | **S** | Le code est écrit et testé en local. **Goriki n'en a pas besoin** (import par set = fichiers statiques). Utile seulement pour l'API publique filtrable. |
| 5 | **Régulariser la colonne `locked`** (migration `0002_locked.sql`) + committer les 4 fichiers WIP | **S** | Sinon un `supabase db push` sur une base neuve casse `cleanup.py` et `scrape.py`. |
| 6 | **Remettre la chaîne d'actualisation en marche** (vérifier les secrets GitHub, relancer `scrape.yml` puis `build-deploy.yml`) | **S/M** | S si les secrets sont déjà configurés ; M s'il faut refaire un run complet et arbitrer les 26 cas ambigus. |
| 7 | **`release_date` par set** | **M** | Donnée absente des deux sources ; saisie manuelle (40 valeurs) ou source tierce. Aucun impact bloquant — le champ est nullable côté Goriki. |
| 8 | **Couverture OP01–OP08 / ST01–ST14** | **L** | Nécessite une source FR qui n'a pas été identifiée, voire qui n'existe pas. À ne lancer que si le besoin métier est confirmé. |

---

## 6. Risques

**Juridique / conditions d'utilisation**

- `opecards.fr/robots.txt` (HTTP 200) n'interdit que `/decks/print`, `/decks/delete`, `/wishlists/print`, `/wishlists/delete` et `/account` — **`/cards/*`, la zone effectivement scrapée, n'est pas interdite**. Aucune page de mentions légales ou de CGU n'a été trouvée (`/mentions-legales`, `/cgu`, `/conditions-generales`, `/legal`, `/a-propos` répondent 404 ; seul `/contact` répond 200). Absence de CGU n'équivaut pas à une autorisation, mais aucun interdit explicite n'a été constaté.
- `fr.onepiece-cardgame.com/robots.txt` répond **404** : aucune directive d'exclusion publiée.
- Le README assume déjà le cadre : projet communautaire non officiel, sans usage commercial des assets, crédits Bandai / Toei / Shueisha. **Réserve** : les images sont recopiées et re-servies depuis un domaine tiers (314 Mo commités dans le dépôt) — c'est le point le plus exposé en cas de réclamation Bandai, et le disclaimer ne l'atténue pas.

**Technique**

- **Fragilité du parsing DOM.** `scrape.py` porte son propre avertissement en en-tête : le mapping des classes CSS Bandai doit être revérifié à chaque run. `scrape_opecards.py` dépend de sélecteurs découverts par exploration (`a.item-main-link`, `.item-description-inner div[lang=fr]`, JSON-LD) et note que « les requêtes hors navigateur sont bloquées » — d'où Playwright obligatoire, y compris pour le téléchargement des images. Un redesign de l'un ou l'autre site casse le scraper. Atténuations déjà en place : validation `scraped` vs `expected` avec seuil de 10 %, exit code 1, ouverture automatique d'une GitHub Issue, et blocage du build si le scrape échoue.
- **Supabase en point de passage unique.** `dist/` et `worker/data/*.json` sont gitignorés : le build CI ne peut rien produire sans la base. Si le free tier est mis en pause ou perdu, la chaîne s'arrête — d'où le workflow `keep-alive.yml`. La sauvegarde de fait est le contenu déjà déployé sur `pages.dev` (les images, elles, sont commitées).
- **Dérive de schéma** entre `0001_init.sql` et la base réelle (colonne `locked`).
- **26 cas ambigus** non arbitrés : cartes SP dont le `card_number` apparaît dans plusieurs sets Bandai (ex. `PRB02-006` rattachée à OP14 alors que Bandai la connaît dans OP14-EB04 **et** PRB02). Impact limité mais réel sur le `set_id` servi.
- **Aucun test automatisé** dans le dépôt, et aucun `typecheck` du Worker en CI.

---

## 7. Recommandation

### Repartir de ce projet. Sans hésitation.

Justification factuelle :

1. **Le travail est fait à environ 90 %, pas à 30 %.** 3 499 cartes FR avec nom, numéro, rareté, image, effet, personnage, affiliations et variantes ; 3 411 images converties en WebP ; 40 sets ; zéro champ obligatoire manquant. Reconstruire cela, c'est refaire les deux scrapers Playwright (1 085 lignes), la logique de fusion et de dédoublonnage (476 lignes), et un run complet de plusieurs heures.
2. **L'API demandée existe déjà et répond.** `https://tanuki-poneglyph.pages.dev/v1/sets.json` renvoie HTTP 200 aujourd'hui. La question du brief — « manque-t-il l'exposition HTTP ? » — a une réponse : non, elle est déployée. Ce qui manque n'est qu'un nom de domaine propre.
3. **Le format est déjà à la bonne granularité pour Goriki.** `/v1/sets/{id}.json` renvoie exactement `set + cards[]`, c'est-à-dire la forme que `fetchOPESet()` attend. L'écart se résume à des renommages de champs, plus deux décisions (aplatir `colors`, gérer les variantes).
4. **L'infrastructure d'entretien est écrite.** Cron mensuel, validation avec seuil, alerte par Issue, keep-alive Supabase, build et deploy automatiques. Rien de tout cela n'est à réinventer.
5. **L'alternative n'existe pas.** `api.opecards.fr` est morte (DNS). Repartir de zéro voudrait dire re-scraper les mêmes deux sites avec les mêmes contraintes (Playwright obligatoire, blocage hors navigateur sur opecards.fr) — donc réécrire ce code-ci, en moins abouti.

### Chemin le plus court pour débloquer Goriki

1. Réécrire `Goriki/goriki/lib/opecards.ts` vers `https://tanuki-poneglyph.pages.dev` (**S**) — `route.ts` n'a pas besoin d'être touché.
2. Trancher la question des variantes (**S** avec le filtre `Standard`) — sinon 1 539 lignes s'écrasent silencieusement.
3. En parallèle, côté Poneglyph : committer le WIP `locked` avec sa migration `0002` (**S**), puis relancer `scrape.yml` pour rafraîchir des données vieilles de 6 semaines (**S/M**).

Le DNS custom, le Worker REST et les sets anciens relèvent du confort ou du hors-scope : ils ne bloquent pas l'import Goriki.

---

*Audit réalisé le 20 août 2026 — lecture seule. Aucun scraper exécuté, aucune requête de masse émise vers opecards.fr ou fr.onepiece-cardgame.com.*
