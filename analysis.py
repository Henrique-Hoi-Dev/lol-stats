"""Transforma o JSON bruto das partidas (match-v5) em linhas simples, uma por jogo,
do ponto de vista do jogador. A agregação (winrate por campeão, rota, horário...)
é feita no navegador para os filtros funcionarem sem recarregar."""
from __future__ import annotations

import csv
import io

QUEUES = {
    420: "Ranqueada Solo/Duo", 440: "Ranqueada Flex", 400: "Normal (Escolha)",
    430: "Normal (Às cegas)", 490: "Partida Rápida", 480: "Swiftplay", 450: "ARAM",
    700: "Clash", 720: "Clash ARAM", 900: "URF", 1900: "URF", 1010: "URF Neve",
    1700: "Arena", 1710: "Arena", 1300: "Nexus Blitz", 2400: "ARAM: Caos",
    830: "Co-op vs IA", 840: "Co-op vs IA", 850: "Co-op vs IA", 870: "Co-op vs IA",
    880: "Co-op vs IA", 890: "Co-op vs IA", 0: "Personalizada",
}

ROLES = {"TOP": "Top", "JUNGLE": "Selva", "MIDDLE": "Meio", "BOTTOM": "Atirador",
         "UTILITY": "Suporte", "": "—"}


def _ratio(a, b):
    return round(a / b, 2) if b else float(a)


def flatten_match(match: dict, puuid: str) -> dict | None:
    info = match.get("info", {})
    parts = info.get("participants", [])
    me = next((p for p in parts if p.get("puuid") == puuid), None)
    if not me:
        return None

    duration = info.get("gameDuration", 0)
    # partidas antigas vinham em ms
    if duration > 100_000:
        duration //= 1000
    minutes = max(duration / 60, 1e-9)

    team = [p for p in parts if p["teamId"] == me["teamId"]]
    enemies = [p for p in parts if p["teamId"] != me["teamId"]]
    team_kills = sum(p.get("kills", 0) for p in team)
    team_dmg = sum(p.get("totalDamageDealtToChampions", 0) for p in team)
    ch = me.get("challenges", {}) or {}

    cs = me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)
    k, d, a = me.get("kills", 0), me.get("deaths", 0), me.get("assists", 0)
    pos = me.get("teamPosition") or me.get("individualPosition") or ""
    if pos == "Invalid":
        pos = ""

    opp = next((p for p in enemies if pos and p.get("teamPosition") == pos), None)

    my_team_obj = next((t for t in info.get("teams", []) if t["teamId"] == me["teamId"]), {})
    obj = my_team_obj.get("objectives", {})

    remake = bool(me.get("gameEndedInEarlySurrender")) or duration < 300

    return {
        "matchId": match["metadata"]["matchId"],
        "ts": info.get("gameStartTimestamp") or info.get("gameCreation"),
        "duration": duration,
        "queueId": info.get("queueId"),
        "queue": QUEUES.get(info.get("queueId"), info.get("gameMode", "?")),
        "patch": ".".join(str(info.get("gameVersion", "")).split(".")[:2]),
        "champion": me.get("championName"),
        "championId": me.get("championId"),
        "champLevel": me.get("champLevel"),
        "role": ROLES.get(pos, pos),
        "win": bool(me.get("win")),
        "remake": remake,
        "side": "Azul" if me["teamId"] == 100 else "Vermelho",
        "kills": k, "deaths": d, "assists": a,
        "kda": _ratio(k + a, d),
        "kp": round((k + a) / team_kills * 100, 1) if team_kills else 0.0,
        "cs": cs, "csMin": round(cs / minutes, 2),
        "gold": me.get("goldEarned", 0), "goldMin": round(me.get("goldEarned", 0) / minutes, 1),
        "dmg": me.get("totalDamageDealtToChampions", 0),
        "dmgMin": round(me.get("totalDamageDealtToChampions", 0) / minutes, 1),
        "dmgShare": round(me.get("totalDamageDealtToChampions", 0) / team_dmg * 100, 1) if team_dmg else 0.0,
        "dmgTaken": me.get("totalDamageTaken", 0),
        "vision": me.get("visionScore", 0), "visionMin": round(me.get("visionScore", 0) / minutes, 2),
        "wardsPlaced": me.get("wardsPlaced", 0), "wardsKilled": me.get("wardsKilled", 0),
        "controlWards": me.get("visionWardsBoughtInGame", 0),
        "firstBlood": bool(me.get("firstBloodKill") or me.get("firstBloodAssist")),
        "double": me.get("doubleKills", 0), "triple": me.get("tripleKills", 0),
        "quadra": me.get("quadraKills", 0), "penta": me.get("pentaKills", 0),
        "soloKills": ch.get("soloKills", 0),
        "cs10": ch.get("laneMinionsFirst10Minutes"),
        "turrets": me.get("turretTakedowns", 0),
        "timeDead": me.get("totalTimeSpentDead", 0),
        "teamDragons": obj.get("dragon", {}).get("kills", 0),
        "teamBarons": obj.get("baron", {}).get("kills", 0),
        "teamTowers": obj.get("tower", {}).get("kills", 0),
        "opponent": opp.get("championName") if opp else None,
        "goldDiffOpp": (me.get("goldEarned", 0) - opp.get("goldEarned", 0)) if opp else None,
        "items": [me.get(f"item{i}", 0) for i in range(7)],
        "spells": [me.get("summoner1Id"), me.get("summoner2Id")],
        "teammates": [
            {"puuid": p["puuid"],
             "name": f'{p.get("riotIdGameName") or p.get("summonerName", "?")}#{p.get("riotIdTagline", "")}',
             "champion": p.get("championName")}
            for p in team if p["puuid"] != puuid
        ],
    }


CSV_FIELDS = ["matchId", "ts", "queue", "patch", "champion", "role", "win", "remake", "side",
              "duration", "kills", "deaths", "assists", "kda", "kp", "cs", "csMin", "gold",
              "goldMin", "dmg", "dmgMin", "dmgShare", "dmgTaken", "vision", "visionMin",
              "wardsPlaced", "wardsKilled", "controlWards", "firstBlood", "soloKills", "cs10",
              "turrets", "timeDead", "double", "triple", "quadra", "penta", "opponent",
              "goldDiffOpp"]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()
