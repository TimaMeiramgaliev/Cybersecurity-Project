import subprocess
import requests
import socket
import platform
import uuid
import time
import ctypes
import os
import base64
import io
import tempfile
from PIL import ImageGrab

# ==== Константы ====
YARA_EXE = r"C:\Users\Larin\Desktop\Dissertation\yara-master-v4.5.4-win64\yara64.exe"
DEFAULT_SCAN_PATH = r"C:\Users\Larin\Desktop\Dissertation\yara-master-v4.5.4-win64"

SERVER_URL = "http://127.0.0.1:5000/api/heartbeat"
API_BASE = "http://127.0.0.1:5000/api/agent"
AGENT_ID = str(uuid.uuid4())  # уникальный ID при каждом запуске

# ==== Проверка прав ====
try:
    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
except:
    is_admin = False

if not is_admin:
    print("[!] It is recommended to run this agent as Administrator, otherwise some commands may not work.")

# ==== Утилиты ====
def send_file_to_server(agent_id: str, filename: str, raw_bytes: bytes):
    """Отправка файла на сервер в base64."""
    b64 = base64.b64encode(raw_bytes).decode('utf-8')
    try:
        requests.post(f"{API_BASE}/{agent_id}/file", json={"name": filename, "data": b64}, timeout=10)
    except Exception as e:
        print("[!] send_file_to_server error:", e)

def get_mac():
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

# ==== Основной цикл ====
def handle_command(command: str):
    command = (command or "").strip()
    if not command:
        return

    # ==== Скриншот ====
    if command == "__SCREENSHOT__":
        try:
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            send_file_to_server(AGENT_ID, f"screenshot_{int(time.time())}.png", buf.getvalue())
            requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": "Screenshot captured"})
        except Exception as e:
            requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": f"Screenshot error: {e}"})
        return

    # ==== YARA ====
    if command.upper().startswith("__YARA__:"):
        try:
            payload_parts = command[len("__YARA__:"):]
            parts = payload_parts.split(":", 2)  # name, b64, [scan_path]
            if len(parts) < 2:
                raise ValueError("bad __YARA__ format")

            rules_name = (parts[0] or "rules.yar").strip()
            rules_b64 = parts[1].strip()
            scan_path = parts[2].strip() if len(parts) == 3 else DEFAULT_SCAN_PATH

            rules_path = os.path.join(tempfile.gettempdir(), rules_name)
            with open(rules_path, "wb") as f:
                f.write(base64.b64decode(rules_b64))

            if not os.path.exists(YARA_EXE):
                raise FileNotFoundError(f"YARA not found: {YARA_EXE}")

            proc = subprocess.run([YARA_EXE, rules_path, scan_path],
                                  capture_output=True, text=True, timeout=120)
            out = proc.stdout.strip() or "(no matches)" if proc.returncode in (0, 1) \
                  else f"YARA error (code {proc.returncode}): {proc.stderr.strip()}"
            requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": out})
        except Exception as e:
            requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": f"YARA exception: {e}"})
        return

    # ==== Загрузка файла ====
    if command.startswith("__DOWNLOAD__:"):
        filepath = command.split(":", 1)[1].strip().strip('"')
        try:
            with open(filepath, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode("ascii")
            filename = os.path.basename(filepath)
            requests.post(f"{API_BASE}/{AGENT_ID}/file",
                          json={"name": filename, "data": data_b64}, timeout=10)
            requests.post(f"{API_BASE}/{AGENT_ID}/output",
                          json={"output": f"File '{filename}' sent to server."})
        except Exception as e:
            requests.post(f"{API_BASE}/{AGENT_ID}/output",
                          json={"output": f"Download failed: {e}"})
        return

    # ==== Обычная команда ====
    try:
        run_cmd = f'chcp 65001>nul & {command}' if os.name == "nt" else command
        result = subprocess.run(run_cmd, shell=True, capture_output=True, text=True)
        output = result.stdout or result.stderr or "No output"
        requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": output})
    except Exception as e:
        requests.post(f"{API_BASE}/{AGENT_ID}/output", json={"output": f"Exec error: {e}"})


# ==== Основной цикл ====
while True:
    payload = {
        "id": AGENT_ID,
        "hostname": socket.gethostname(),
        "os": platform.system() + " " + platform.release(),
        "mac": get_mac()
    }

    try:
        resp = requests.post(SERVER_URL, json=payload, timeout=5)
        if resp.ok:
            data = resp.json() or {}
            cmd = (data.get("command") or "").strip()
            if cmd:
                handle_command(cmd)
        else:
            print(f"[{time.ctime()}] Heartbeat status: {resp.status_code}")
    except Exception as e:
        print(f"[{time.ctime()}] Heartbeat error: {e}")

    time.sleep(1)  # можно 1-2 сек
