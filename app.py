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

from analysis import flatten_match, to_csv
from riot import PLATFORMS, RiotClient, RiotError

app = Flask(__name__)
JOBS: dict[str, dict] = {}


def run_job(job_id: str, riot_id: str, platform: str, count: int, queue: int | None):
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
        cached = [i for i in ids if client.is_cached(i)]
        missing = [i for i in ids if not client.is_cached(i)]
        job["message"] = f"Baixando partidas ({len(cached)} já em cache)..."

        def fetch(mid):
            try:
                return flatten_match(client.match(mid), puuid)
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


@app.post("/api/fetch")
def api_fetch():
    body = request.get_json(force=True)
    count = max(1, min(int(body.get("count", 100)), 1000))
    queue = int(body["queue"]) if body.get("queue") else None
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "running", "progress": 0, "total": 0, "message": "Iniciando..."}
    threading.Thread(target=run_job, daemon=True,
                     args=(job_id, body.get("riotId", ""), body.get("platform", "br1"), count, queue)).start()
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


@app.get("/api/job/<job_id>/csv")
def api_csv(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "não disponível", 404
    name = job["result"]["account"]["gameName"]
    return Response(to_csv(job["result"]["matches"]), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_partidas.csv"'})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
