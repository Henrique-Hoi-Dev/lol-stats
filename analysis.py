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


def _perk(p: dict, style: int, keystone: bool = False):
    try:
        s = p["perks"]["styles"][style]
        return s["selections"][0]["perk"] if keystone else s["style"]
    except (KeyError, IndexError, TypeError):
        return None


def timeline_summary(match: dict, tl: dict, completed: set[int]) -> dict:
    """Resumo por jogador (puuid) da linha do tempo: ouro/CS/XP aos 10 e 15 min,
    mortes com tempo e posição e minuto do primeiro item completo."""
    parts = match.get("info", {}).get("participants", [])
    pid2puuid = {p["participantId"]: p["puuid"] for p in parts if "participantId" in p}
    frames = tl.get("info", {}).get("frames", [])

    def snap(minute: int, pid: int):
        if len(frames) <= minute:
            return None
        f = frames[minute]["participantFrames"].get(str(pid), {})
        return [f.get("totalGold", 0), f.get("minionsKilled", 0) + f.get("jungleMinionsKilled", 0), f.get("xp", 0)]

    deaths = {pid: [] for pid in pid2puuid}
    first_item: dict[int, int] = {}
    for fr in frames:
        for ev in fr.get("events", []):
            t = ev.get("timestamp", 0) // 1000
            if ev.get("type") == "CHAMPION_KILL" and ev.get("victimId") in deaths:
                pos = ev.get("position", {})
                deaths[ev["victimId"]].append([t, pos.get("x", 0), pos.get("y", 0)])
            elif (ev.get("type") == "ITEM_PURCHASED" and ev.get("itemId") in completed
                  and ev.get("participantId") not in first_item):
                first_item[ev["participantId"]] = t

    return {puuid: {"at10": snap(10, pid), "at15": snap(15, pid),
                    "deaths": deaths[pid], "firstItem": first_item.get(pid)}
            for pid, puuid in pid2puuid.items()}


def merge_timeline(row: dict, summary: dict, puuid: str) -> dict:
    """Adiciona à linha do jogo as diferenças contra o oponente direto aos 10/15 min."""
    me, op = summary.get(puuid), summary.get(row.get("oppPuuid") or "")
    if not me:
        return row
    row["deathsTl"] = me["deaths"]
    row["firstItem"] = me["firstItem"]
    if op:
        row["oppFirstItem"] = op["firstItem"]
        for m in (10, 15):
            a, b = me[f"at{m}"], op[f"at{m}"]
            if a and b:
                row[f"gd{m}"], row[f"csd{m}"], row[f"xpd{m}"] = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return row


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
        "oppPuuid": opp.get("puuid") if opp else None,
        "name": f'{me.get("riotIdGameName") or me.get("summonerName", "?")}#{me.get("riotIdTagline", "")}',
        "keystone": _perk(me, 0, True),
        "runePrimary": _perk(me, 0),
        "runeSecondary": _perk(me, 1),
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
              "goldDiffOpp", "gd10", "gd15", "csd10", "csd15", "xpd10", "xpd15", "firstItem"]


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def match_detail(match: dict, tl: dict, puuid: str, completed: set[int]) -> dict:
    """Visão de uma partida só: as 10 linhas, curvas de ouro minuto a minuto,
    ordem de compra dos itens completos e objetivos com horário."""
    info = match.get("info", {})
    parts = info.get("participants", [])
    me = next(p for p in parts if p.get("puuid") == puuid)
    rows = [flatten_match(match, p["puuid"]) for p in parts]
    for r, p in zip(rows, parts):
        r["puuid"], r["teamId"], r["participantId"] = p["puuid"], p["teamId"], p.get("participantId")
    my_row = next(r for r in rows if r["puuid"] == puuid)
    opp = next((p for p in parts if p["puuid"] == my_row.get("oppPuuid")), None)
    pid_me, pid_opp = me.get("participantId"), opp.get("participantId") if opp else None
    team_of = {p.get("participantId"): p["teamId"] for p in parts}

    frames = tl.get("info", {}).get("frames", [])
    curve = []
    for i, fr in enumerate(frames):
        pf = fr.get("participantFrames", {})
        g = lambda pid: pf.get(str(pid), {}).get("totalGold", 0)  # noqa: E731
        team = sum(g(pid) for pid, t in team_of.items() if t == me["teamId"])
        enemy = sum(g(pid) for pid, t in team_of.items() if t != me["teamId"])
        f = pf.get(str(pid_me), {})
        curve.append({"m": i, "me": g(pid_me), "opp": g(pid_opp) if pid_opp else None, "teamDiff": team - enemy,
                      "cs": f.get("minionsKilled", 0) + f.get("jungleMinionsKilled", 0), "xp": f.get("xp", 0)})

    build, events = [], []
    for fr in frames:
        for ev in fr.get("events", []):
            t, typ = ev.get("timestamp", 0) // 1000, ev.get("type")
            if typ == "ITEM_PURCHASED" and ev.get("participantId") == pid_me and ev.get("itemId") in completed:
                build.append([t, ev["itemId"]])
            elif typ == "ELITE_MONSTER_KILL":
                events.append({"t": t, "kind": ev.get("monsterType"), "sub": ev.get("monsterSubType"),
                               "mine": team_of.get(ev.get("killerId")) == me["teamId"]})
            elif typ == "BUILDING_KILL":
                # teamId do evento = time DONO da estrutura destruída
                events.append({"t": t, "kind": ev.get("buildingType"), "sub": ev.get("towerType"),
                               "mine": ev.get("teamId") != me["teamId"]})
            elif typ == "CHAMPION_KILL" and pid_me in [ev.get("killerId"), ev.get("victimId"), *ev.get("assistingParticipantIds", [])]:
                pos = ev.get("position", {})
                events.append({"t": t, "kind": "DEATH" if ev.get("victimId") == pid_me else
                               "KILL" if ev.get("killerId") == pid_me else "ASSIST",
                               "x": pos.get("x", 0), "y": pos.get("y", 0),
                               "vs": next((r["champion"] for r in rows if r["participantId"] ==
                                           (ev.get("killerId") if ev.get("victimId") == pid_me else ev.get("victimId"))), None)})

    summary = timeline_summary(match, tl, completed)
    merge_timeline(my_row, summary, puuid)
    return {"me": my_row, "players": rows, "curve": curve, "build": build, "events": events,
            "teams": [{"teamId": t["teamId"], "win": t.get("win"),
                       "objectives": {k: v.get("kills", 0) for k, v in t.get("objectives", {}).items()}}
                      for t in info.get("teams", [])]}
