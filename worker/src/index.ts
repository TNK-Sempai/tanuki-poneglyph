/**
 * TANUKI-PONEGLYPH — API REST Cloudflare Worker.
 *
 * Les données sont importées en STATIQUE dans le bundle (contrainte projet :
 * pas de KV, pas de fetch runtime vers Supabase). Les fichiers worker/data/*
 * sont générés par build/generate.py avant chaque deploy.
 *
 * Toute route hors API (ex. /v1/cards.json, /images/*) est passée telle
 * quelle à l'origine (Cloudflare Pages).
 */

import cardsData from "../data/cards.json";
import setsData from "../data/sets.json";
import filtersData from "../data/filters.json";

interface Card {
  id: string;
  card_number: string;
  version: string;
  set_id: string;
  name: string;
  type: string;
  colors: string[];
  attribute: string | null;
  power: number | null;
  cost: number | null;
  counter: number | null;
  life: number | null;
  effect: string | null;
  trigger_effect: string | null;
  rarity: string;
  character_name: string | null;
  affiliations: string[];
  abilities: string[];
  image: string | null;
  block_number: number | null;
}

interface CardSet {
  set_id: string;
  name: string;
  set_type: string;
  release_date: string | null;
  card_count: number | null;
}

const CARDS = cardsData as unknown as Card[];
const SETS = setsData as unknown as CardSet[];
const FILTERS = filtersData as unknown as Record<string, unknown>;

const SCHEMA_VERSION = "1.0";
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

const SET_TYPE_BY_ID = new Map(SETS.map((s) => [s.set_id, s.set_type]));
const CARD_BY_ID = new Map(CARDS.map((c) => [c.id, c]));

const RARITY_ORDER = ["DON!!", "C", "UC", "R", "SR", "L", "SEC", "SP", "P", "TR"];

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

/** Normalisation insensible casse + accents ("Événement" → "evenement"). */
function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function json(data: unknown, status = 200, cache = "public, max-age=3600"): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": cache,
      ...CORS_HEADERS,
    },
  });
}

function notFound(message: string): Response {
  return json({ schema_version: SCHEMA_VERSION, error: message }, 404, "no-store");
}

/** Multi-valeurs : "?color=Rouge,Bleu" → ["rouge","bleu"] (foldés). */
function multi(params: URLSearchParams, key: string): string[] | null {
  const raw = params.get(key);
  if (!raw) return null;
  const values = raw.split(",").map((v) => fold(v.trim())).filter(Boolean);
  return values.length ? values : null;
}

function intParam(params: URLSearchParams, key: string): number | null {
  const raw = params.get(key);
  if (raw === null || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** OR sur un champ scalaire. */
function matchScalar(value: string | null, wanted: string[]): boolean {
  return value !== null && wanted.includes(fold(value));
}

/** OR sur un champ array : au moins une valeur demandée présente. */
function matchArray(values: string[], wanted: string[]): boolean {
  return values.some((v) => wanted.includes(fold(v)));
}

function filterCards(params: URLSearchParams): Card[] {
  const name = params.get("name");
  const nameFolded = name ? fold(name) : null;
  const cardNumber = params.get("card_number");

  const types = multi(params, "type");
  const colors = multi(params, "color");
  const attributes = multi(params, "attribute");
  const rarities = multi(params, "rarity");
  const sets = multi(params, "set");
  const setTypes = multi(params, "set_type");
  const versions = multi(params, "version");
  const characters = multi(params, "character");
  const affiliations = multi(params, "affiliation");
  const abilities = multi(params, "ability");

  const powerEq = intParam(params, "power_eq");
  const powerGte = intParam(params, "power_gte");
  const powerLte = intParam(params, "power_lte");
  const costEq = intParam(params, "cost_eq");
  const costGte = intParam(params, "cost_gte");
  const costLte = intParam(params, "cost_lte");
  const lifeEq = intParam(params, "life_eq");
  const counterEq = intParam(params, "counter_eq");

  return CARDS.filter((c) => {
    if (nameFolded && !fold(c.name).includes(nameFolded)) return false;
    if (cardNumber && c.card_number.toLowerCase() !== cardNumber.toLowerCase()) return false;

    if (types && !matchScalar(c.type, types)) return false;
    if (colors && !matchArray(c.colors, colors)) return false;
    if (attributes && !matchScalar(c.attribute, attributes)) return false;
    if (rarities && !matchScalar(c.rarity, rarities)) return false;
    if (sets && !matchScalar(c.set_id, sets)) return false;
    if (setTypes && !matchScalar(SET_TYPE_BY_ID.get(c.set_id) ?? null, setTypes)) return false;
    if (versions && !matchScalar(c.version, versions)) return false;
    if (characters && !matchScalar(c.character_name, characters)) return false;
    if (affiliations && !matchArray(c.affiliations, affiliations)) return false;
    if (abilities && !matchArray(c.abilities, abilities)) return false;

    if (powerEq !== null && c.power !== powerEq) return false;
    if (powerGte !== null && (c.power === null || c.power < powerGte)) return false;
    if (powerLte !== null && (c.power === null || c.power > powerLte)) return false;
    if (costEq !== null && c.cost !== costEq) return false;
    if (costGte !== null && (c.cost === null || c.cost < costGte)) return false;
    if (costLte !== null && (c.cost === null || c.cost > costLte)) return false;
    if (lifeEq !== null && c.life !== lifeEq) return false;
    if (counterEq !== null && c.counter !== counterEq) return false;

    return true;
  });
}

function sortCards(cards: Card[], sortParam: string | null): Card[] {
  if (!sortParam) return cards;
  const m = sortParam.match(/^(name|power|cost|card_number|rarity)(?:_(asc|desc))?$/);
  if (!m) return cards;
  const [, field, dir] = m;
  const sign = dir === "desc" ? -1 : 1;

  const key = (c: Card): string | number => {
    switch (field) {
      case "name": return fold(c.name);
      case "power": return c.power ?? -1;
      case "cost": return c.cost ?? -1;
      case "rarity": {
        const idx = RARITY_ORDER.indexOf(c.rarity);
        return idx === -1 ? RARITY_ORDER.length : idx;
      }
      default: return c.card_number;
    }
  };

  return [...cards].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    if (ka < kb) return -sign;
    if (ka > kb) return sign;
    return a.id < b.id ? -1 : 1; // tri stable et déterministe
  });
}

function handleCards(params: URLSearchParams): Response {
  const filtered = sortCards(filterCards(params), params.get("sort"));

  let limit = intParam(params, "limit") ?? DEFAULT_LIMIT;
  limit = Math.min(Math.max(limit, 1), MAX_LIMIT);
  let offset = intParam(params, "offset") ?? 0;
  offset = Math.max(offset, 0);

  return json({
    schema_version: SCHEMA_VERSION,
    total: CARDS.length,
    filtered: filtered.length,
    limit,
    offset,
    cards: filtered.slice(offset, offset + limit),
  });
}

export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // Les fichiers statiques (.json, /images/) restent servis par Pages.
    if (path.includes(".")) return fetch(request);

    if (path === "/v1/cards") return handleCards(url.searchParams);

    if (path === "/v1/sets") {
      return json({ schema_version: SCHEMA_VERSION, total: SETS.length, sets: SETS });
    }

    if (path === "/v1/filters") {
      return json({ schema_version: SCHEMA_VERSION, ...FILTERS });
    }

    const cardMatch = path.match(/^\/v1\/cards\/([^/]+)$/);
    if (cardMatch) {
      const card = CARD_BY_ID.get(decodeURIComponent(cardMatch[1]));
      return card
        ? json({ schema_version: SCHEMA_VERSION, card })
        : notFound(`Carte introuvable : ${cardMatch[1]}`);
    }

    const setMatch = path.match(/^\/v1\/sets\/([^/]+)$/);
    if (setMatch) {
      const setId = decodeURIComponent(setMatch[1]).toUpperCase();
      const set = SETS.find((s) => s.set_id === setId);
      if (!set) return notFound(`Set introuvable : ${setId}`);
      const cards = CARDS.filter((c) => c.set_id === setId);
      return json({ schema_version: SCHEMA_VERSION, ...set, filtered: cards.length, cards });
    }

    // Tout le reste → Cloudflare Pages (statique)
    return fetch(request);
  },
};
