"""Cliente da Riot API com rate limit e cache em disco.

Endpoints usados:
  - account-v1        : Riot ID (Nome#TAG) -> PUUID
  - summoner-v4       : nível, ícone
  - league-v4         : elo (Solo/Duo e Flex)
  - champion-mastery-v4 : maestrias
  - match-v5          : lista de partidas + detalhes de cada partida
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import requests

# plataforma -> roteamento regional (match-v5 / account-v1)
PLATFORMS = {
    "br1": "americas", "na1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "sg2": "sea", "tw2": "sea", "vn2": "sea",
}

CACHE_DIR = Path(os.environ.get("LOL_CACHE_DIR", Path(__file__).parent / "cache"))


class RiotError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status


class RateLimiter:
    """Respeita vários limites ao mesmo tempo. Padrão = chave de desenvolvimento
    (20 req / 1 s e 100 req / 120 s)."""

    def __init__(self, limits=((20, 1.0), (100, 120.0))):
        self.limits = limits
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            while True:
                now = time.monotonic()
                longest = max(w for _, w in self.limits)
                while self.calls and now - self.calls[0] > longest:
                    self.calls.popleft()
                sleep_for = 0.0
                for max_calls, window in self.limits:
                    recent = [t for t in self.calls if now - t < window]
                    if len(recent) >= max_calls:
                        sleep_for = max(sleep_for, window - (now - recent[0]) + 0.05)
                if sleep_for <= 0:
                    self.calls.append(now)
                    return
                time.sleep(sleep_for)


class RiotClient:
    def __init__(self, api_key: str | None = None, platform: str = "br1"):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY", "")
        if not self.api_key:
            raise RiotError(401, "Defina RIOT_API_KEY (pegue em https://developer.riotgames.com)")
        platform = platform.lower()
        if platform not in PLATFORMS:
            raise RiotError(400, f"Servidor inválido: {platform}. Use um de {', '.join(PLATFORMS)}")
        self.platform = platform
        self.region = PLATFORMS[platform]
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key
        self.limiter = RateLimiter()
        (CACHE_DIR / "matches").mkdir(parents=True, exist_ok=True)

    # ---------- HTTP ----------
    def _get(self, host: str, path: str, params: dict | None = None, retries: int = 4):
        url = f"https://{host}.api.riotgames.com{path}"
        for attempt in range(retries + 1):
            self.limiter.wait()
            r = self.session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            msg = {401: "Chave inválida", 403: "Chave expirada/sem permissão (chaves dev expiram em 24h)",
                   404: "Não encontrado"}.get(r.status_code, r.text[:200])
            raise RiotError(r.status_code, msg)
        raise RiotError(429, "Rate limit excedido repetidamente")

    # ---------- endpoints ----------
    def account_by_riot_id(self, game_name: str, tag_line: str):
        return self._get(self.region,
                         f"/riot/account/v1/accounts/by-riot-id/{requests.utils.quote(game_name)}/{requests.utils.quote(tag_line)}")

    def account_by_puuid(self, puuid: str):
        return self._get(self.region, f"/riot/account/v1/accounts/by-puuid/{puuid}")

    def summoner_by_puuid(self, puuid: str):
        return self._get(self.platform, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    def league_entries(self, puuid: str):
        return self._get(self.platform, f"/lol/league/v4/entries/by-puuid/{puuid}")

    def masteries(self, puuid: str, count: int = 15):
        return self._get(self.platform,
                         f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
                         {"count": count})

    def apex_league(self, tier: str, queue: str = "RANKED_SOLO_5x5"):
        """tier: challenger | grandmaster | master"""
        return self._get(self.platform, f"/lol/league/v4/{tier}leagues/by-queue/{queue}")

    def league_division(self, tier: str, division: str, page: int = 1, queue: str = "RANKED_SOLO_5x5"):
        """Jogadores de um elo/divisão (ex: GOLD, II). Até 205 por página."""
        return self._get(self.platform, f"/lol/league/v4/entries/{queue}/{tier}/{division}", {"page": page})

    def match_ids(self, puuid: str, count: int = 100, queue: int | None = None):
        """Pagina de 100 em 100 até `count`."""
        ids: list[str] = []
        start = 0
        while len(ids) < count:
            n = min(100, count - len(ids))
            params = {"start": start, "count": n}
            if queue:
                params["queue"] = queue
            page = self._get(self.region, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params)
            ids.extend(page)
            if len(page) < n:
                break
            start += n
        return ids

    def match(self, match_id: str):
        """Partidas não mudam -> cache permanente em disco."""
        f = CACHE_DIR / "matches" / f"{match_id}.json"
        if f.exists():
            return json.loads(f.read_text())
        data = self._get(self.region, f"/lol/match/v5/matches/{match_id}")
        f.write_text(json.dumps(data))
        return data

    def timeline_raw(self, match_id: str) -> dict:
        """Linha do tempo completa (~1 MB, sem cache): usada na análise de uma partida só."""
        return self._get(self.region, f"/lol/match/v5/matches/{match_id}/timeline")

    def timeline(self, match_id: str, match: dict) -> dict:
        """Resumo da linha do tempo (ver analysis.timeline_summary). O JSON bruto tem ~1 MB,
        então só o resumo vai para o cache."""
        from analysis import timeline_summary
        f = CACHE_DIR / "timelines" / f"{match_id}.json"
        if f.exists():
            return json.loads(f.read_text())
        raw = self._get(self.region, f"/lol/match/v5/matches/{match_id}/timeline")
        data = timeline_summary(match, raw, completed_items())
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data))
        return data

    def is_timeline_cached(self, match_id: str) -> bool:
        return (CACHE_DIR / "timelines" / f"{match_id}.json").exists()

    def is_cached(self, match_id: str) -> bool:
        return (CACHE_DIR / "matches" / f"{match_id}.json").exists()


_ITEMS_LOCK = threading.Lock()
_ITEMS: set[int] | None = None


def completed_items() -> set[int]:
    """IDs de itens completos (lendários) de Summoner's Rift, via Data Dragon.
    Critério: custo total >= 2000, sem upgrade ('into'), à venda na loja, sem botas."""
    global _ITEMS
    with _ITEMS_LOCK:
        if _ITEMS is not None:
            return _ITEMS
        f = CACHE_DIR / "items_completed.json"
        try:
            ver = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10).json()[0]
            data = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/item.json", timeout=20).json()["data"]
            _ITEMS = {int(k) for k, it in data.items()
                      if it.get("gold", {}).get("total", 0) >= 2000 and not it.get("into")
                      and it.get("gold", {}).get("purchasable") and it.get("inStore", True)
                      and it.get("maps", {}).get("11") and "Boots" not in it.get("tags", [])
                      and not it.get("requiredAlly")}
            f.write_text(json.dumps(sorted(_ITEMS)))
        except Exception:  # noqa: BLE001 - sem internet: usa a última lista salva
            _ITEMS = set(json.loads(f.read_text())) if f.exists() else set()
        return _ITEMS
