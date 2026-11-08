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

    def summoner_by_puuid(self, puuid: str):
        return self._get(self.platform, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    def league_entries(self, puuid: str):
        return self._get(self.platform, f"/lol/league/v4/entries/by-puuid/{puuid}")

    def masteries(self, puuid: str, count: int = 15):
        return self._get(self.platform,
                         f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
                         {"count": count})

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

    def is_cached(self, match_id: str) -> bool:
        return (CACHE_DIR / "matches" / f"{match_id}.json").exists()
