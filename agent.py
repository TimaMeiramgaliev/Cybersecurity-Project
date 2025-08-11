# agent.py
import os
import io
import time
import base64
import uuid
import socket
import ctypes
import tempfile
import subprocess
import threading
from pathlib import Path

import requests
import psutil
import ipaddress
from PIL import ImageGrab
import platform

# ----------------- Конфиг -----------------
SERVER_BASE   = "http://127.0.0.1:5000"
HEARTBEAT_URL = f"{SERVER_BASE}/api/heartbeat"
API_BASE      = f"{SERVER_BASE}/api/agent"

YARA_EXE = r"C:\Users\Larin\Desktop\Dissertation\yara-master-v4.5.4-win64\yara64.exe"
DEFAULT_SCAN_PATH = r"C:\Users\Larin\Desktop\Dissertation\yara-master-v4.5.4-win64"

HEARTBEAT_INTERVAL_SEC = 1
CONNS_PUSH_INTERVAL_SEC = 15
HTTP_TIMEOUT = 5

# ----------------- Устойчивый ID -----------------
def load_or_create_agent_id() -> str:
    base = Path(os.getenv("PROGRAMDATA") or Path.home()) / ".myagent"
    base.mkdir(parents=True, exist_ok=True)
    id_file = base / "agent_id.txt"

    try:
        if id_file.exists():
            aid = id_file.read_text(encoding="utf-8").strip()
            if aid:
                return aid
    except Exception:
        pass

    aid = str(uuid.uuid4())
    try:
        id_file.write_text(aid, encoding="utf-8")
    except Exception:
        # не критично — продолжим с несохранённым ID
        pass
    return aid

AGENT_ID = load_or_create_agent_id()

# ----------------- Утилиты -----------------
def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def get_mac() -> str:
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

def send_file_to_server(filename: str, raw_bytes: bytes):
    """Отправка файла на сервер (base64) на /api/agent/<id>/file"""
    b64 = base64.b64encode(raw_bytes).decode("utf-8")
    requests.post(
        f"{API_BASE}/{AGENT_ID}/file",
        json={"name": filename, "data": b64},
        timeout=HTTP_TIMEOUT
    )

def post_output(text: str):
    try:
        requests.post(
            f"{API_BASE}/{AGENT_ID}/output",
            json={"output": text},
            timeout=HTTP_TIMEOUT
        )
    except Exception:
        pass

# ----------------- Команды сервера -----------------
def handle_command(command: str):
    command = (command or "").strip()
    if not command:
        return

    # Скриншот
    if command == "__SCREENSHOT__":
        try:
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            send_file_to_server(f"screenshot_{int(time.time())}.png", buf.getvalue())
            post_output("Screenshot captured")
        except Exception as e:
            post_output(f"Screenshot error: {e}")
        return

    # YARA: __YARA__:<rules_name>:<base64_rules>[:scan_path]
    if command.upper().startswith("__YARA__:"):
        try:
            payload = command.split("__YARA__:", 1)[1]
            parts = payload.split(":", 2)  # rules_name, rules_b64, [scan_path]
            if len(parts) < 2:
                raise ValueError("bad __YARA__ format")

            rules_name = (parts[0] or "rules.yar").strip()
            rules_b64  = parts[1].strip()
            scan_path  = parts[2].strip() if len(parts) == 3 else DEFAULT_SCAN_PATH

            rules_path = Path(tempfile.gettempdir()) / rules_name
            rules_path.write_bytes(base64.b64decode(rules_b64))

            if not Path(YARA_EXE).exists():
                raise FileNotFoundError(f"YARA not found: {YARA_EXE}")

            proc = subprocess.run(
                [YARA_EXE, str(rules_path), scan_path],
                capture_output=True, text=True, timeout=120
            )
            if proc.returncode in (0, 1):
                out = proc.stdout.strip() or "(no matches)"
            else:
                out = f"YARA error (code {proc.returncode}): {proc.stderr.strip()}"
            post_output(out)
        except Exception as e:
            post_output(f"YARA exception: {e}")
        return

    # Скачивание файла с агента
    if command.startswith("__DOWNLOAD__:"):
        filepath = command.split(":", 1)[1].strip().strip('"')
        try:
            data = Path(filepath).read_bytes()
            send_file_to_server(Path(filepath).name, data)
            post_output(f"File '{os.path.basename(filepath)}' sent to server.")
        except Exception as e:
            post_output(f"Download failed: {e}")
        return

    # Обычная команда
    try:
        run_cmd = f'chcp 65001>nul & {command}' if os.name == "nt" else command
        proc = subprocess.run(run_cmd, shell=True, capture_output=True, text=True)
        output = proc.stdout or proc.stderr or "No output"
        post_output(output)
    except Exception as e:
        post_output(f"Exec error: {e}")

# ----------------- Сбор сетевых подключений -----------------
def is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except Exception:
        return False

def collect_conns():
    seen = {}
    for c in psutil.net_connections(kind='tcp'):
        if c.raddr and c.status == psutil.CONN_ESTABLISHED:
            ip = c.raddr.ip
            if is_public_ip(ip):
                key = (ip, c.raddr.port)
                if key not in seen:
                    seen[key] = {
                        "ip": ip,
                        "port": c.raddr.port,
                        "pid": c.pid,
                        "proc": (psutil.Process(c.pid).name() if c.pid else None),
                        "ts": int(time.time())
                    }
    return list(seen.values())

def push_conns_loop():
    url = f"{API_BASE}/{AGENT_ID}/connections"
    while True:
        try:
            conns = collect_conns()
            if conns:
                requests.post(url, json={"conns": conns}, timeout=HTTP_TIMEOUT)
        except Exception as e:
            print("connections post error:", e)
        time.sleep(CONNS_PUSH_INTERVAL_SEC)

# ----------------- Main -----------------
if __name__ == "__main__":
    if not is_admin():
        print("[!] It is recommended to run this agent as Administrator.")

    # фон: отправка сетевых подключений для Threat Intel
    threading.Thread(target=push_conns_loop, daemon=True).start()

    # heartbeat + команды
    while True:
        payload = {
            "id": AGENT_ID,
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "mac": get_mac()
        }
        try:
            r = requests.post(HEARTBEAT_URL, json=payload, timeout=HTTP_TIMEOUT)
            if r.ok:
                cmd = (r.json() or {}).get("command") or ""
                if cmd.strip():
                    handle_command(cmd)
            else:
                print(f"[{time.ctime()}] Heartbeat status: {r.status_code}")
        except Exception as e:
            print(f"[{time.ctime()}] Heartbeat error: {e}")

        time.sleep(HEARTBEAT_INTERVAL_SEC)
