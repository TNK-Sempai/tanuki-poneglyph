# TANUKI / OP-TCG API — Direction artistique UI

## Objectif

Moderniser `editor.html` et `cleanup-review.html` pour créer une interface d'administration premium dédiée à l'API One Piece Card Game FR.

L'interface doit appartenir clairement à la même famille graphique que le portfolio Tanuki : **sombre, technique, éditoriale, précise, premium**, avec une inspiration terminal / data-lab / sci-fi très légère.

**IMPORTANT : ne pas produire un dashboard CRUD générique, Bootstrap ou SaaS 2010.**
L'interface doit avoir une vraie direction artistique et une forte identité.

---

## Palette

Utiliser les tokens du portfolio :

```css
--bg: #09090e;
--surface: #0e0e15;
--surface2: #13131c;
--border: rgba(255,255,255,0.06);

--text: #ede8df;
--muted: #9a9490;
--muted2: #9c9590;

--accent: #c8f060;
--accent-dim: rgba(200,240,96,0.12);
```

### Proportion visuelle

**85% neutral / 10% structure / 5% lime accent**

Le lime doit être utilisé avec parcimonie :

* actions principales
* états actifs
* indicateurs importants
* petits détails graphiques
* hover / focus
* statistiques clés

Éviter les interfaces entièrement vertes.

Les images des cartes One Piece peuvent naturellement apporter toute leur richesse chromatique : **le dashboard doit servir d'écrin aux cartes**.

---

## Typographie

Conserver les 4 familles du portfolio :

* **Bebas Neue** → grands chiffres, titres majeurs, branding
* **DM Serif Display** → emphases éditoriales ponctuelles
* **DM Mono** → labels, metadata, numéros, statuts, navigation technique
* **Instrument Sans** → contenu et interface

DM Mono doit être utilisé pour donner le côté **data / terminal / système**.

---

## Signatures visuelles

Réutiliser les éléments du portfolio lorsque pertinents :

* grain/noise overlay très subtil
* lignes et bordures 1px
* petits corners décoratifs lime
* eyebrows avec `●`
* numérotation `01 /`, `02 /`, etc.
* texte outline ponctuel
* micro-glows lime
* animations très sobres
* reveal / hover avec transitions fluides
* aucun effet spectaculaire inutile

L'ensemble doit rester **sobre et fonctionnel**.

---

# 01 — EDITOR

Structure principale en **3 zones** :

```text
┌─────────────────────────────────────────────────────────────┐
│ TANUKI / OP-TCG API                    ● API ONLINE  v0.8.4│
├──────────────┬──────────────────────────────┬───────────────┤
│ SETS         │ CARD GRID                    │ CARD EDITOR   │
│              │                              │               │
│ OP01         │ [card] [card] [card] [card] │ image         │
│ OP02         │ [card] [card] [card] [card] │ name          │
│ OP03         │ [card] [card] [card] [card] │ fields        │
│ ...          │                              │               │
└──────────────┴──────────────────────────────┴───────────────┘
```

### Colonne Sets

Interface compacte et éditoriale.

Exemple :

```text
SETS
────────────────

01  ROMANCE DAWN
    121 / 121

02  PARAMOUNT WAR
    126 / 126

03  OP03
    125 / 126   !
```

Afficher :

* numéro du set
* nom
* cartes présentes / attendues
* éventuellement verrouillées
* état incomplet avec indicateur discret

Pas de gros badges colorés.

---

## Grille des cartes

Les cartes doivent être visuellement prioritaires.

Chaque item :

```text
┌────────────┐
│            │
│ CARD IMAGE │
│            │
└────────────┘
OP09-001
MONKEY D. LUFFY
SR · PARALLEL
```

Au hover :

* léger zoom image
* bordure / accent lime
* apparition d'une action `OPEN →`
* transition rapide et élégante

Éviter les cartes UI arrondies façon dashboard SaaS.

---

# CARD EDITOR

La fiche carte ne doit **pas ressembler à un long formulaire CRUD**.

Organiser les 19 champs en sections logiques.

### Header

```text
CARD / OP09-001

MONKEY D. LUFFY
Super Rare · Parallel

● VERIFIED
```

### Sections

```text
IDENTITY
────────────────────────

CARD NUMBER
OP09-001

NAME
Monkey D. Luffy

VERSION
Parallel

RARITY
SR

TYPE
Character

COLOR
Red


CHARACTER DATA
────────────────────────

POWER
6000

COUNTER
+1000

ATTRIBUTE
Strike

SUBTYPE
Straw Hat Crew


SOURCE
────────────────────────

SET
OP09

SOURCE ID
...
```

Les champs doivent être **denses mais respirants**, avec une vraie hiérarchie typographique.

### Actions

Action principale :

`SAVE CHANGES`

Actions secondaires :

`DUPLICATE`

`DELETE`

Le bouton Delete doit rester discret jusqu'au hover / interaction.

---

# VARIANTS / DUPLICATION

Le système doit rendre naturel le cas des variantes comme Sabo.

Lors d'une duplication :

```text
DUPLICATE CARD
────────────────────────

SOURCE
OP09-001 / SABO

NEW VARIANT

VERSION
[ Parallel ]

IMAGE
[ URL / Upload ]

────────────────────────

CANCEL             CREATE VARIANT →
```

Si plusieurs versions existent, pouvoir les comprendre rapidement :

```text
OP09-001

├── Normal
├── Parallel
└── SP
```

---

# 02 — CLEANUP REVIEW

Cette page doit ressembler davantage à un **centre de contrôle qualité / data review**.

Header :

```text
CLEANUP / REVIEW

506 PENDING ACTIONS

491 DELETE
15 REASSIGN
```

Filtres :

```text
ALL
REVIEW
RISK
IMAGE DIFF
APPROVED
REJECTED
DELETE
REASSIGN
```

Le filtre actif utilise simplement le lime + une ligne / bordure.

---

## Review item

Exemple :

```text
┌──────────────────────────────────────────────────────────────┐
│ OP09-045                                  ! HIGH RISK         │
│                                                              │
│ [IMAGE]   MONKEY D. SABO                                    │
│           OP09 · SR                                          │
│                                                              │
│           CURRENT                                            │
│           └─ DELETE                                          │
│                                                              │
│           SUGGESTED                                          │
│           └─ OP09-045 / PARALLEL                            │
│                                                              │
│ [ REJECT ]                              [ APPROVE ]          │
└──────────────────────────────────────────────────────────────┘
```

Les risques doivent être visibles mais sans transformer l'interface en dashboard rouge.

---

# SEARCH

La recherche globale doit être très visible mais minimaliste.

Recherche sur :

* nom
* numéro
* set
* version
* personnage

Style terminal / command interface :

```text
⌕ SEARCH CARDS...
```

Prévoir éventuellement `⌘K` / `Ctrl+K`.

---

# 03 — HOME / DASHBOARD

Si pertinent, la racine `8099` peut devenir un mini dashboard plutôt que d'ouvrir directement l'éditeur.

Exemple :

```text
TANUKI
OP-TCG API
────────────────────────────────────

54
SETS

12,847
CARDS

98.4%
COMPLETION

506
CLEANUP QUEUE


RECENT ACTIVITY
────────────────

OP09       126 / 126        COMPLETE
OP10       120 / 124        96.7%
PROMO       84 / 91         92.3%


CLEANUP
────────────────

491  DELETE
15   REASSIGN

                         REVIEW QUEUE →
```

Les chiffres importants peuvent utiliser **Bebas Neue** en grand, comme dans le portfolio.

---

# PRINCIPES UX

L'interface doit donner immédiatement la sensation :

**"outil interne professionnel pour gérer une vraie base de données TCG"**

et non :

**"template admin acheté sur ThemeForest".**

Priorités :

1. Lisibilité
2. Densité maîtrisée
3. Hiérarchie claire
4. Rapidité d'édition
5. Images de cartes mises en valeur
6. Feedback visuel précis
7. Identité Tanuki forte

### À ÉVITER ABSOLUMENT

* Bootstrap look
* grosses cards arrondies
* glassmorphism excessif
* gradients omniprésents
* boutons géants
* badges multicolores
* ombres lourdes
* icônes partout
* sidebar SaaS générique
* lime utilisé partout
* animations gadgets

### À RECHERCHER

**Premium terminal / editorial data interface**

Noir bleuté profond + blanc cassé + typographie forte + DM Mono + lignes fines + micro-détails lime + images TCG colorées.

Le résultat doit être immédiatement reconnaissable comme **Tanuki**, tout en étant clairement une **interface d'outil data/API**, pas une copie du portfolio.
