"""Bases de referência: partidas Solo/Duo recentes de jogadores de um elo, agregadas por
rota (e por rota+campeão) em percentis 25/50/75.

tier = "ELITE" (top Challenger + Grão-Mestre por PDL) ou um elo da Riot (IRON ... CHALLENGER).

Cada partida baixada rende 10 linhas (o matchmaking junta jogadores de elo parecido),
então ~200 partidas já dão ~400 jogos por rota."""
from __future__ import annotations

import json
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from analysis import flatten_match
from riot import CACHE_DIR, RiotClient, RiotError, completed_items

TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND",
         "MASTER", "GRANDMASTER", "CHALLENGER"]
APEX = {"MASTER", "GRANDMASTER", "CHALLENGER"}
ELITE = "ELITE"

METRICS = ["kda", "csMin", "cs10", "goldMin", "dmgMin", "dmgShare", "kp", "deaths", "visionMin",
           "controlWards", "wardsKilled", "soloKills", "turrets"]
# goldDiffOpp fica de fora: jogadores do mesmo elo um contra o outro, a mediana é sempre ~0
MIN_CHAMP_GAMES = 15
MIN_BUILD_GAMES = 5
TOP1_MATCHES = 20


def _path(platform: str, tier: str):
    # a base da elite mantém o nome antigo do arquivo
    name = f"benchmark_{platform}.json" if tier == ELITE else f"benchmark_{platform}_{tier.lower()}.json"
    return CACHE_DIR / name


def valid_tier(tier: str) -> bool:
    return tier == ELITE or tier in TIERS


def load(platform: str, tier: str = ELITE) -> dict | None:
    f = _path(platform, tier)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _summary(rows: list[dict]) -> dict:
    out = {}
    for m in METRICS:
        vals = [r[m] for r in rows if r.get(m) is not None]
        if len(vals) >= 5:
            q1, q2, q3 = statistics.quantiles(vals, n=4)
            out[m] = [round(q1, 2), round(q2, 2), round(q3, 2)]
    return {"n": len(rows), "metrics": out}


def _builds(rows: list[dict]) -> dict:
    """Itens completos, runas, feitiços e melhores jogadores de um campeão numa rota."""
    done = completed_items()
    n = len(rows)
    items = Counter(i for r in rows for i in set(r["items"][:6]) if i in done)
    keys = Counter(r.get("keystone") for r in rows if r.get("keystone"))
    sec = Counter(r.get("runeSecondary") for r in rows if r.get("runeSecondary"))
    spells = Counter("-".join(map(str, sorted(r["spells"]))) for r in rows if all(r["spells"]))
    players: dict[str, list] = {}
    for r in rows:
        players.setdefault(r["name"], []).append(r)
    best = sorted(players.items(), key=lambda kv: (len(kv[1]), sum(x["win"] for x in kv[1])), reverse=True)[:5]
    return {
        "n": n, "wins": sum(r["win"] for r in rows),
        "items": items.most_common(8), "keystones": keys.most_common(3),
        "secondary": sec.most_common(3), "spells": spells.most_common(3),
        "players": [{"name": k, "games": len(v), "wins": sum(x["win"] for x in v),
                     "kda": round(sum(x["kills"] + x["assists"] for x in v) / max(sum(x["deaths"] for x in v), 1), 2)}
                    for k, v in best],
    }


def _top1(client: RiotClient, entry: dict, rows: list[dict]) -> dict:
    try:
        acc = client.account_by_puuid(entry["puuid"])
        name = f'{acc.get("gameName", "?")}#{acc.get("tagLine", "")}'
    except RiotError:
        name = rows[0]["name"] if rows else "?"
    by_role: dict[str, list] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)
    champs = Counter(r["champion"] for r in rows)
    return {
        "name": name, "lp": entry.get("leaguePoints"), "wins": entry.get("wins"), "losses": entry.get("losses"),
        "games": len(rows),
        "roles": {k: {"n": len(v), "wins": sum(x["win"] for x in v),
                      "avg": {m: round(statistics.fmean(x[m] for x in v if x.get(m) is not None), 2)
                              for m in METRICS if any(x.get(m) is not None for x in v)}}
                  for k, v in by_role.items()},
        "champs": [[c, n, sum(r["win"] for r in rows if r["champion"] == c)] for c, n in champs.most_common(8)],
    }


def _players(client: RiotClient, tier: str, n: int) -> list[dict]:
    entries = []
    if tier == ELITE:
        for t in ("challenger", "grandmaster"):
            try:
                entries += client.apex_league(t).get("entries", [])
            except RiotError:
                pass
        entries.sort(key=lambda e: e.get("leaguePoints", 0), reverse=True)
        entries = [e for e in entries if e.get("puuid")][:n]
    else:
        if tier in APEX:
            entries = client.apex_league(tier.lower()).get("entries", [])
        else:
            for div in ("I", "II", "III", "IV"):
                try:
                    entries += client.league_division(tier, div)
                except RiotError:
                    pass
        entries = [e for e in entries if e.get("puuid") and not e.get("inactive")]
        entries = random.sample(entries, min(n, len(entries)))
    return entries


def build(platform: str, tier: str = ELITE, players: int = 40, per_player: int = 5,
          job: dict | None = None, label: str = "") -> dict:
    job = job if job is not None else {}
    client = RiotClient(platform=platform)
    pre = f"[{label or tier}] "

    job["message"] = pre + "Buscando jogadores do elo..."
    entries = _players(client, tier, players)
    puuids = [e["puuid"] for e in entries]
    if not puuids:
        raise RiotError(404, f"Não foi possível obter jogadores do elo {label or tier}")

    job["message"] = pre + "Listando partidas dos jogadores..."
    job["total"], job["progress"] = len(puuids), 0
    ids: list[str] = []
    top1_ids: list[str] = []
    for i, p in enumerate(puuids):
        try:
            got = client.match_ids(p, count=TOP1_MATCHES if tier == ELITE and i == 0 else per_player, queue=420)
            if tier == ELITE and i == 0:
                top1_ids = got
            ids += got
        except RiotError:
            pass
        job["progress"] += 1
    ids = list(dict.fromkeys(ids))

    job["message"] = pre + "Baixando partidas..."
    job["total"], job["progress"] = len(ids), 0

    def fetch(mid):
        try:
            return client.match(mid)
        except RiotError:
            return None
        finally:
            job["progress"] += 1

    rows, top1_rows = [], []
    top1 = puuids[0] if tier == ELITE else None
    top1_set = set(top1_ids)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for match in pool.map(fetch, ids):
            if not match:
                continue
            for p in match.get("info", {}).get("participants", []):
                r = flatten_match(match, p["puuid"])
                if r and not r["remake"] and r["role"] != "—":
                    rows.append(r)
                    if p["puuid"] == top1 and r["matchId"] in top1_set:
                        top1_rows.append(r)

    by_role: dict[str, list] = {}
    by_champ: dict[str, list] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)
        by_champ.setdefault(f'{r["role"]}|{r["champion"]}', []).append(r)

    data = {
        "platform": platform,
        "tier": tier,
        "builtAt": int(time.time() * 1000),
        "players": len(puuids),
        "matches": len(ids),
        "games": len(rows),
        "roles": {k: _summary(v) for k, v in by_role.items()},
        "champs": {k: _summary(v) for k, v in by_champ.items() if len(v) >= MIN_CHAMP_GAMES},
        "builds": {k: _builds(v) for k, v in by_champ.items() if len(v) >= MIN_BUILD_GAMES},
    }
    if top1:
        data["top1"] = _top1(client, entries[0], top1_rows)
    _path(platform, tier).write_text(json.dumps(data), encoding="utf-8")
    return data
