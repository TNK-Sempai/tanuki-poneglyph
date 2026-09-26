-- TANUKI-PONEGLYPH — verrouillage manuel des cartes
--
-- Régularise une colonne créée à la main en base lors des corrections de
-- juillet 2026 et utilisée depuis par scrape.py, scrape_opecards.py et
-- cleanup.py, sans jamais avoir été décrite dans une migration.
--
-- locked = true  → la ligne a été corrigée ou saisie à la main (dashboard,
--                  arbitrage d'un cas ambigu). Les scrapers ne la réécrivent
--                  pas et cleanup.py ne la supprime ni ne la réassigne.
--
-- Idempotent : rejouable sans risque sur une base où la colonne existe déjà.

ALTER TABLE poneglyph_cards
  ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN poneglyph_cards.locked IS
  'Carte corrigée/saisie manuellement : protégée des scrapers et de cleanup.py.';

-- Les cartes verrouillées sont rares : index partiel, pas d'index complet.
CREATE INDEX IF NOT EXISTS idx_cards_locked
  ON poneglyph_cards (id) WHERE locked;
