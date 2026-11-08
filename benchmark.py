"""Base de referência da elite: partidas Solo/Duo recentes de jogadores Challenger e
Grão-Mestre, agregadas por rota (e por rota+campeão) em percentis 25/50/75.

Cada partida baixada rende 10 linhas (todos os participantes estão no topo do ladder),
então ~200 partidas já dão ~400 jogos por rota."""
from __future__ import annotations

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from analysis import flatten_match
from riot import CACHE_DIR, RiotClient, RiotError

METRICS = ["csMin", "cs10", "goldMin", "dmgMin", "dmgShare", "kp", "deaths", "visionMin",
           "controlWards", "wardsKilled", "soloKills", "turrets"]
# goldDiffOpp fica de fora: elite contra elite, a mediana é sempre ~0
MIN_CHAMP_GAMES = 15


def _path(platform: str):
    return CACHE_DIR / f"benchmark_{platform}.json"


def load(platform: str) -> dict | None:
    f = _path(platform)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _summary(rows: list[dict]) -> dict:
    out = {}
    for m in METRICS:
        vals = [r[m] for r in rows if r.get(m) is not None]
        if len(vals) >= 5:
            q1, q2, q3 = statistics.quantiles(vals, n=4)
            out[m] = [round(q1, 2), round(q2, 2), round(q3, 2)]
    return {"n": len(rows), "metrics": out}


def build(platform: str, players: int = 40, per_player: int = 5, job: dict | None = None) -> dict:
    job = job if job is not None else {}
    client = RiotClient(platform=platform)

    job["message"] = "Buscando ladder Challenger e Grão-Mestre..."
    entries = []
    for tier in ("challenger", "grandmaster"):
        try:
            entries += client.apex_league(tier).get("entries", [])
        except RiotError:
            pass
    entries = [e for e in entries if e.get("puuid")]
    if not entries:
        raise RiotError(404, "Não foi possível obter jogadores do ladder (Challenger/Grão-Mestre)")
    entries.sort(key=lambda e: e.get("leaguePoints", 0), reverse=True)
    top = entries[:players]

    job["message"] = "Listando partidas dos melhores jogadores..."
    job["total"], job["progress"] = len(top), 0
    ids: list[str] = []
    for e in top:
        try:
            ids += client.match_ids(e["puuid"], count=per_player, queue=420)
        except RiotError:
            pass
        job["progress"] += 1
    ids = list(dict.fromkeys(ids))

    job["message"] = "Baixando partidas da elite..."
    job["total"], job["progress"] = len(ids), 0

    def fetch(mid):
        try:
            return client.match(mid)
        except RiotError:
            return None
        finally:
            job["progress"] += 1

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for match in pool.map(fetch, ids):
            if not match:
                continue
            for p in match.get("info", {}).get("participants", []):
                r = flatten_match(match, p["puuid"])
                if r and not r["remake"] and r["role"] != "—":
                    rows.append(r)

    by_role: dict[str, list] = {}
    by_champ: dict[str, list] = {}
    for r in rows:
        by_role.setdefault(r["role"], []).append(r)
        by_champ.setdefault(f'{r["role"]}|{r["champion"]}', []).append(r)

    data = {
        "platform": platform,
        "builtAt": int(time.time() * 1000),
        "players": len(top),
        "matches": len(ids),
        "games": len(rows),
        "roles": {k: _summary(v) for k, v in by_role.items()},
        "champs": {k: _summary(v) for k, v in by_champ.items() if len(v) >= MIN_CHAMP_GAMES},
    }
    _path(platform).write_text(json.dumps(data), encoding="utf-8")
    return data
