"""Servidor web: digite Nome#TAG, ele baixa tudo da Riot API e mostra gráficos.

Uso:
    export RIOT_API_KEY=RGAPI-xxxx
    python app.py            -> http://localhost:5000
"""
from __future__ import annotations

import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Carrega .env (variáveis já definidas no ambiente têm prioridade)
_env = Path(__file__).with_name(".env")
if _env.exists():
    for _line in _env.read_text(encoding="utf-8-sig").splitlines():
        _k, _sep, _v = _line.partition("=")
        if _sep and not _k.strip().startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from flask import Flask, Response, jsonify, render_template, request

import benchmark
from analysis import flatten_match, match_detail, merge_timeline, to_csv
from riot import PLATFORMS, RiotClient, RiotError, completed_items

app = Flask(__name__)
JOBS: dict[str, dict] = {}


def run_job(job_id: str, riot_id: str, platform: str, count: int, queue: int | None, timeline: bool = True):
    job = JOBS[job_id]
    try:
        if "#" not in riot_id:
            raise RiotError(400, "Use o formato Nome#TAG (ex: Faker#KR1)")
        name, tag = riot_id.rsplit("#", 1)
        client = RiotClient(platform=platform)

        job["message"] = "Buscando conta..."
        account = client.account_by_riot_id(name.strip(), tag.strip())
        puuid = account["puuid"]

        job["message"] = "Buscando perfil, elo e maestrias..."
        summoner = client.summoner_by_puuid(puuid)
        try:
            league = client.league_entries(puuid)
        except RiotError:
            league = []
        try:
            mastery = client.masteries(puuid, 20)
        except RiotError:
            mastery = []

        job["message"] = "Listando partidas..."
        ids = client.match_ids(puuid, count=count, queue=queue)
        job["total"] = len(ids)

        rows = []
        done = lambda i: client.is_cached(i) and (not timeline or client.is_timeline_cached(i))  # noqa: E731
        cached = [i for i in ids if done(i)]
        missing = [i for i in ids if not done(i)]
        job["message"] = f"Baixando partidas{' e linhas do tempo' if timeline else ''} ({len(cached)} já em cache)..."

        def fetch(mid):
            try:
                match = client.match(mid)
                row = flatten_match(match, puuid)
                if row and timeline and not row["remake"]:
                    try:
                        merge_timeline(row, client.timeline(mid, match), puuid)
                    except RiotError:
                        pass
                return row
            except RiotError:
                return None
            finally:
                job["progress"] += 1

        # cache primeiro (instantâneo), depois as que faltam (limitadas pelo rate limiter)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for r in pool.map(fetch, cached + missing):
                if r:
                    rows.append(r)

        rows.sort(key=lambda r: r["ts"] or 0, reverse=True)
        job["result"] = {
            "account": account,
            "summoner": summoner,
            "league": league,
            "mastery": mastery,
            "platform": platform,
            "matches": rows,
            "completedItems": sorted(completed_items()),
        }
        job["status"] = "done"
        job["message"] = f"{len(rows)} partidas carregadas"
    except RiotError as e:
        job["status"], job["message"] = "error", str(e)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job["status"], job["message"] = "error", f"Erro inesperado: {e}"


@app.get("/")
def index():
    return render_template("index.html", platforms=list(PLATFORMS),
                           has_key=bool(os.environ.get("RIOT_API_KEY")))


@app.get("/partida")
def partida():
    return render_template("match.html", platforms=list(PLATFORMS),
                           has_key=bool(os.environ.get("RIOT_API_KEY")))


@app.post("/api/match")
def api_match():
    """{riotId, matchId, platform} -> análise de uma partida do ponto de vista do jogador.
    matchId aceita "BR1_123..." (o prefixo define o servidor) ou só o número."""
    body = request.get_json(force=True) or {}
    riot_id, match_id = body.get("riotId", "").strip(), body.get("matchId", "").strip().upper()
    platform = body.get("platform", "br1").lower()
    try:
        if "#" not in riot_id:
            raise RiotError(400, "Use o formato Nome#TAG (ex: Faker#KR1)")
        if "_" in match_id:
            platform = match_id.split("_", 1)[0].lower()
        elif match_id.isdigit():
            match_id = f"{platform.upper()}_{match_id}"
        else:
            raise RiotError(400, "ID da partida inválido (ex: BR1_3012345678)")
        client = RiotClient(platform=platform)
        name, tag = riot_id.rsplit("#", 1)
        account = client.account_by_riot_id(name.strip(), tag.strip())
        match = client.match(match_id)
        if not any(p.get("puuid") == account["puuid"] for p in match["info"]["participants"]):
            raise RiotError(404, f"{account['gameName']}#{account['tagLine']} não jogou a partida {match_id}")
        try:
            league = client.league_entries(account["puuid"])
        except RiotError:
            league = []
        detail = match_detail(match, client.timeline_raw(match_id), account["puuid"], completed_items())
        return jsonify({"account": account, "league": league, "platform": platform, "matchId": match_id, **detail})
    except RiotError as e:
        return jsonify({"message": str(e)}), e.status if 400 <= e.status < 600 else 500
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"message": f"Erro inesperado: {e}"}), 500


@app.post("/api/fetch")
def api_fetch():
    body = request.get_json(force=True)
    count = max(1, min(int(body.get("count", 100)), 1000))
    queue = int(body["queue"]) if body.get("queue") else None
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "running", "progress": 0, "total": 0, "message": "Iniciando..."}
    threading.Thread(target=run_job, daemon=True,
                     args=(job_id, body.get("riotId", ""), body.get("platform", "br1"), count, queue,
                           bool(body.get("timeline")))).start()
    return jsonify({"jobId": job_id})


@app.get("/api/job/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Job não encontrado"}), 404
    out = {k: v for k, v in job.items() if k != "result"}
    if job["status"] == "done":
        out["result"] = job["result"]
    return jsonify(out)


def run_benchmark(job_id: str, platform: str, tiers: list[str]):
    """Gera as bases em sequência (um só rate limiter de cada vez)."""
    job = JOBS[job_id]
    try:
        result = {}
        for i, tier in enumerate(tiers, 1):
            result[tier] = benchmark.build(platform, tier, job=job, label=f"{i}/{len(tiers)} {tier}")
        job["result"], job["status"] = result, "done"
        job["message"] = "Referências prontas: " + ", ".join(f"{t} ({d['games']} jogos)" for t, d in result.items())
    except RiotError as e:
        job["status"], job["message"] = "error", str(e)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        job["status"], job["message"] = "error", f"Erro inesperado: {e}"


@app.get("/api/benchmark")
def api_benchmark():
    """?platform=br1&tiers=ELITE,GOLD,PLATINUM -> {tier: base ou null}"""
    platform = request.args.get("platform", "br1")
    tiers = [t for t in request.args.get("tiers", benchmark.ELITE).upper().split(",") if benchmark.valid_tier(t)]
    return jsonify({t: benchmark.load(platform, t) for t in tiers})


@app.post("/api/benchmark/build")
def api_benchmark_build():
    body = request.get_json(force=True) or {}
    platform = body.get("platform", "br1")
    tiers = [t.upper() for t in body.get("tiers", [benchmark.ELITE]) if benchmark.valid_tier(t.upper())]
    if not tiers:
        return jsonify({"message": "Nenhum elo válido"}), 400
    running = next((k for k, j in JOBS.items()
                    if j.get("kind") == "bench" and j.get("platform") == platform and j["status"] == "running"), None)
    if running:
        return jsonify({"jobId": running})
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "running", "progress": 0, "total": 0, "message": "Iniciando...",
                    "kind": "bench", "platform": platform}
    threading.Thread(target=run_benchmark, daemon=True, args=(job_id, platform, tiers)).start()
    return jsonify({"jobId": job_id})


@app.get("/api/job/<job_id>/csv")
def api_csv(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "não disponível", 404
    name = job["result"]["account"]["gameName"]
    return Response(to_csv(job["result"]["matches"]), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_partidas.csv"'})


if __name__ == "__main__":
    # debug: recarrega sozinho ao salvar .py e templates (só escuta em localhost)
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
