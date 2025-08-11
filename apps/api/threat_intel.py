# -*- coding: utf-8 -*-
# apps/api/threat_intel.py
import os, time, requests, threading, collections
from dotenv import load_dotenv
from flask import Blueprint, request, jsonify

load_dotenv()
ti = Blueprint("ti", __name__)

ABUSE_KEY = os.getenv("ABUSEIPDB_KEY")
CACHE = {}  # ip -> {"score": int, "reports": int, "ts": float, "country": str, "categories": list}
CACHE_TTL = 24 * 3600
STORE_LOCK = threading.Lock()

# Храним последние N записей по агенту (in-memory)
MAX_PER_AGENT = 200
CONN_STORE = collections.defaultdict(lambda: collections.deque(maxlen=MAX_PER_AGENT))

def abuse_check(ip, max_age_days=30, cache_ttl=CACHE_TTL):
    now = time.time()
    hit = CACHE.get(ip)
    if hit and (now - hit["ts"] < cache_ttl):
        return hit

    rec = {"score": 0, "reports": 0, "country": None, "categories": [], "ts": now}
    if not ABUSE_KEY:
        CACHE[ip] = rec
        return rec

    try:
        r = requests.get(
            "https://api.abuseipdb.com/api/v2/check",
            params={"ipAddress": ip, "maxAgeInDays": max_age_days},
            headers={"Key": ABUSE_KEY, "Accept": "application/json"},
            timeout=5
        )
        if r.ok:
            j = (r.json() or {}).get("data", {}) or {}
            rec.update({
                "score": j.get("abuseConfidenceScore", 0),
                "reports": j.get("totalReports", 0),
                "country": j.get("countryCode"),
                "categories": (j.get("reports") or [{}])[-1].get("categories", []) if j.get("reports") else [],
                "ts": now,
            })
    except Exception:
        pass

    CACHE[ip] = rec
    return rec

@ti.route("/api/agent/<agent_id>/connections", methods=["POST"])
def receive_conns(agent_id):
    data = request.json or {}
    conns = data.get("conns") or []
    enriched = []
    for c in conns:
        v = abuse_check(c.get("ip"))
        enriched.append({
            **c,
            "abuse_score": v["score"],
            "abuse_reports": v["reports"],
            "country": v["country"],
            "categories": v["categories"],
            "received_ts": int(time.time()),
        })

    with STORE_LOCK:
        dq = CONN_STORE[agent_id]
        for item in enriched:
            dq.appendleft(item)  # новые сверху

    return jsonify({"ok": True, "count": len(enriched)})

def get_recent_conns(agent_id: str, limit: int = 50):
    with STORE_LOCK:
        return list(list(CONN_STORE.get(agent_id, []))[:limit])

@ti.route("/api/agent/<agent_id>/connections/recent", methods=["GET"])
def recent_conns_api(agent_id):
    limit = int(request.args.get("limit", 50))
    return jsonify({"items": get_recent_conns(agent_id, limit)})
