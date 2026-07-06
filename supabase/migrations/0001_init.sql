-- TANUKI-PONEGLYPH — Schéma initial Supabase
-- Compte Supabase DÉDIÉ. Préfixe tables : poneglyph_ (convention Tanuki Corporation).

-- Table des sets
CREATE TABLE poneglyph_sets (
  set_id        text PRIMARY KEY,           -- "OP16", "ST29", "EB01"
  name          text NOT NULL,              -- Nom FR officiel du set
  set_type      text NOT NULL,              -- OP / ST / EB / CH / PCC / PRB / REGIO
  release_date  date,
  card_count    integer,                    -- Nombre attendu de cartes (pour validation scraper)
  created_at    timestamptz DEFAULT now()
);

-- Table des cartes
CREATE TABLE poneglyph_cards (
  id              text PRIMARY KEY,         -- Slug : "{card_number}_{version}_{set_id}"
  card_number     text NOT NULL,            -- "OP16-001", "ST29-003"
  version         text NOT NULL DEFAULT 'Standard',  -- Standard, Alternative Art, Manga, ...
  set_id          text NOT NULL REFERENCES poneglyph_sets(set_id),
  name            text NOT NULL,            -- Nom FR officiel
  type            text NOT NULL,            -- LEADER / PERSONNAGE / ÉVÉNEMENT / LIEU / DON!!
  colors          text[] NOT NULL,          -- '{Rouge}' ou '{Rouge,Vert}' pour multicolore
  attribute       text,                     -- Frappe / Tranche / Spécial / Distance / Sagesse
  power           integer,                  -- NULL pour Événement/Lieu
  cost            integer,                  -- Coût d'énergie (DON!!)
  counter         integer,                  -- Valeur de contre (0, 1000, 2000)
  life            integer,                  -- Points de vie (Leaders uniquement)
  effect          text,                     -- Texte d'effet FR complet
  trigger_effect  text,                     -- "trigger" est un mot réservé SQL
  rarity          text NOT NULL,            -- TR / L / C / UC / R / SR / SEC / P / DON!!
  character_name  text,                     -- Personnage illustré : "Monkey D. Luffy"
  affiliations    text[],                   -- '{Équipage de Chapeau de paille,East Blue}'
  abilities       text[],                   -- '{Bloqueur,Double attaque,Initiative}'
  image_filename  text,                     -- "OP16-001_standard.webp"
  block_number    integer,
  created_at      timestamptz DEFAULT now(),
  UNIQUE(card_number, version, set_id)
);

-- Index pour les requêtes fréquentes du build script
CREATE INDEX idx_cards_set ON poneglyph_cards(set_id);
CREATE INDEX idx_cards_type ON poneglyph_cards(type);
CREATE INDEX idx_cards_rarity ON poneglyph_cards(rarity);

-- RLS : BDD privée, aucun accès public direct.
-- Aucune policy créée => anon/authenticated ne peuvent rien lire.
-- Seule la service_role key (scraper + build, côté CI) contourne le RLS.
ALTER TABLE poneglyph_sets  ENABLE ROW LEVEL SECURITY;
ALTER TABLE poneglyph_cards ENABLE ROW LEVEL SECURITY;
