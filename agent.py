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


# ----------------- Configuration Input Function -----------------
def get_server_config():
    """
    Prompts the user to enter the server URL and port for the VAST platform.
    Provides clear instructions and validation.
    """
    print("=" * 60)
    print("           VAST AGENT CONFIGURATION")
    print("=" * 60)
    print("This agent needs to connect to your VAST server to:")
    print("• Send system information and heartbeats")
    print("• Receive commands from security analysts")
    print("• Upload files and screenshots for analysis")
    print("• Report network connections for threat intelligence")
    print()

    while True:
        print("Please enter the server configuration:")
        print("• If running locally: http://localhost or http://127.0.0.1")
        print("• If running on network: http://192.168.1.100 (example)")
        print("• If using HTTPS: https://your-domain.com")
        print()

        # Get server address
        server_address = input("Enter server address (without port): ").strip()
        if not server_address:
            print("❌ Server address cannot be empty. Please try again.\n")
            continue

        # Remove protocol if user included it
        if server_address.startswith(('http://', 'https://')):
            protocol = server_address.split('://')[0] + '://'
            server_address = server_address.split('://')[1]
        else:
            protocol = "http://"

        # Get port
        port_input = input("Enter server port (default: 5000): ").strip()
        if not port_input:
            port = "5000"
        else:
            try:
                port = str(int(port_input))
                if not (1 <= int(port) <= 65535):
                    raise ValueError("Port out of range")
            except ValueError:
                print("❌ Invalid port number. Port must be between 1-65535.\n")
                continue

        # Construct full URL
        server_base = f"{protocol}{server_address}:{port}"

        # Confirm configuration
        print(f"\n📋 Configuration Summary:")
        print(f"   Server: {server_base}")
        print(f"   Protocol: {protocol[:-3]}")
        print(f"   Address: {server_address}")
        print(f"   Port: {port}")
        print()

        confirm = input("Is this correct? (y/n): ").strip().lower()
        if confirm in ['y', 'yes', '']:
            print(f"✅ Server configured: {server_base}")
            print("=" * 60)
            print()
            return server_base
        else:
            print("🔄 Let's try again...\n")


def test_server_connection(server_base):
    """
    Tests the connection to the VAST server to ensure it's reachable.
    """
    print("🔍 Testing connection to VAST server...")
    try:
        # Test basic connectivity
        response = requests.get(f"{server_base}/", timeout=5)
        if response.status_code == 200:
            print("✅ Server connection successful!")
            return True
        else:
            print(f"⚠️  Server responded with status code: {response.status_code}")
            print("   This might be normal if the server doesn't have a root endpoint.")
            return True
    except requests.exceptions.ConnectionError:
        print("❌ Connection failed! Please check:")
        print("   • Is the VAST server running?")
        print("   • Is the address and port correct?")
        print("   • Are there any firewalls blocking the connection?")
        print("   • If running locally, try: http://127.0.0.1:5000")
        return False
    except requests.exceptions.Timeout:
        print("❌ Connection timed out! The server might be:")
        print("   • Overloaded")
        print("   • Behind a slow network")
        print("   • Not responding")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


# ----------------- Configuration -----------------
print("Initializing VAST Agent...")

# Get server configuration from user
while True:
    SERVER_BASE = get_server_config()

    # Test the connection
    if test_server_connection(SERVER_BASE):
        break
    else:
        print("\n🔄 Connection test failed. Would you like to:")
        retry = input("   • Try again with different settings? (y/n): ").strip().lower()
        if retry not in ['y', 'yes']:
            print("❌ Exiting. Please ensure your VAST server is running and try again.")
            exit(1)
        print()

HEARTBEAT_URL = f"{SERVER_BASE}/api/heartbeat"
API_BASE = f"{SERVER_BASE}/api/agent"

print(f"🚀 Starting VAST Agent with server: {SERVER_BASE}")
print("=" * 60)
print()

# Set default YARA executable path based on platform
if platform.system() == "Windows":
    YARA_EXE = "yara64.exe"  # Try to find in PATH
else:
    YARA_EXE = "yara"  # Try to find in PATH


def configure_yara_path():
    """
    Prompts the user to specify the path to the YARA executable.
    """
    print("🔍 YARA EXECUTABLE CONFIGURATION")
    print("=" * 40)
    print("YARA is required for malware scanning operations.")
    print("Please specify the path to your YARA executable:")
    print()
    print("Common locations:")
    if platform.system() == "Windows":
        print("• C:\\yara\\yara64.exe")
        print("• C:\\Program Files\\yara\\yara64.exe")
        print("• Download from: https://github.com/VirusTotal/yara/releases")
    else:
        print("• /usr/bin/yara")
        print("• /usr/local/bin/yara")
        print("• Install via package manager: sudo apt install yara")
    print()

    while True:
        yara_path = input("Enter YARA executable path: ").strip().strip('"')

        if not yara_path:
            print("❌ YARA path cannot be empty. Please try again.\n")
            continue

        # Check if the file exists
        if Path(yara_path).exists():
            # Test if it's executable
            try:
                result = subprocess.run([yara_path, "--version"],
                                        capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    version = result.stdout.strip()
                    print(f"✅ YARA found and working: {version}")
                    print(f"   Path: {yara_path}")
                    return yara_path
                else:
                    print("❌ YARA executable failed to run. Please check the path.\n")
            except Exception as e:
                print(f"❌ Error testing YARA: {e}")
                print("   Please ensure the path is correct and executable.\n")
        else:
            print(f"❌ File not found: {yara_path}")
            print("   Please check the path and try again.\n")


# Ask user about YARA configuration
print("YARA executable path configuration:")
yara_choice = input("Configure YARA path? (y/n, default: y): ").strip().lower()
if yara_choice not in ['n', 'no']:
    YARA_EXE = configure_yara_path()
    print()
else:
    print(f"🔍 Using default YARA path: {YARA_EXE}")
    print("   (Make sure YARA is in your system PATH)")
    print()


# Validate YARA availability
def check_yara_available():
    try:
        result = subprocess.run([YARA_EXE, "--version"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"✅ YARA found: {result.stdout.strip()}")
            return True
        else:
            print(f"⚠️  YARA executable failed to run")
            return False
    except FileNotFoundError:
        print(f"⚠️  YARA executable not found: {YARA_EXE}")
        print("   YARA scanning will be disabled. Install YARA to enable scanning.")
        return False
    except Exception as e:
        print(f"⚠️  YARA validation error: {e}")
        return False


# Validate YARA setup
YARA_AVAILABLE = check_yara_available()
if not YARA_AVAILABLE:
    print("   💡 To install YARA:")
    if platform.system() == "Windows":
        print("   • Download from: https://github.com/VirusTotal/yara/releases")
        print("   • Extract to C:\\yara\\ and add to PATH")
    else:
        print("   • Ubuntu/Debian: sudo apt install yara")
        print("   • macOS: brew install yara")
print()


def get_scan_path_info(scan_path):
    """
    Provides information about the scan path and estimated scanning time.
    """
    if platform.system() == "Windows":
        if scan_path.endswith(":\\"):
            return f"Full system drive scan ({scan_path}) - This may take 30+ minutes depending on drive size"
        elif scan_path == "C:\\":
            return f"Full C: drive scan - This may take 30+ minutes depending on drive size"
        elif scan_path == ".":
            return "Current directory scan - Quick scan of current working directory"
        else:
            return f"Directory scan: {scan_path}"
    else:
        if scan_path == "/":
            return "Full filesystem scan (/) - This may take 30+ minutes depending on system size"
        elif scan_path == ".":
            return "Current directory scan - Quick scan of current working directory"
        else:
            return f"Directory scan: {scan_path}"


def test_scan_permissions(scan_path):
    """
    Tests if the agent has proper permissions to scan the target path.
    Returns True if permissions are adequate, False otherwise.
    """
    try:
        # Test basic directory access
        if not Path(scan_path).exists():
            post_output(f"⚠️  Warning: Scan path does not exist: {scan_path}")
            return False

        # Test read permissions on a few key directories
        test_dirs = []
        if platform.system() == "Windows":
            test_dirs = [
                scan_path,
                Path(scan_path) / "Windows" / "System32",
                Path(scan_path) / "Users",
                Path(scan_path) / "Program Files"
            ]
        else:
            test_dirs = [
                scan_path,
                Path(scan_path) / "etc",
                Path(scan_path) / "usr",
                Path(scan_path) / "var"
            ]

        accessible_count = 0
        for test_dir in test_dirs:
            try:
                if test_dir.exists():
                    # Try to list a few files
                    files = list(test_dir.iterdir())
                    if len(files) > 0:
                        accessible_count += 1
                        post_output(f"   ✅ {test_dir} - accessible")
                    else:
                        post_output(f"   ⚠️  {test_dir} - accessible but empty")
                else:
                    post_output(f"   ⚠️  {test_dir} - does not exist")
            except PermissionError:
                post_output(f"   ❌ {test_dir} - permission denied")
            except Exception as e:
                post_output(f"   ❌ {test_dir} - error: {e}")

        # Consider permissions adequate if we can access at least 50% of test directories
        if accessible_count >= len(test_dirs) * 0.5:
            post_output(f"   📊 Permission test: {accessible_count}/{len(test_dirs)} directories accessible")
            return True
        else:
            post_output(f"   📊 Permission test: {accessible_count}/{len(test_dirs)} directories accessible")
            post_output("   ⚠️  Limited access detected - scan may be incomplete")
            return False

    except Exception as e:
        post_output(f"   ❌ Permission test error: {e}")
        return False


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
    return ':'.join(("%012X" % mac)[i:i + 2] for i in range(0, 12, 2))


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
            # Check if YARA is available
            if not YARA_AVAILABLE:
                post_output(f"❌ YARA is not available: {YARA_EXE}")
                post_output("   Please install YARA to enable scanning.")
                post_output("   Windows: Download from https://github.com/VirusTotal/yara/releases")
                post_output("   Linux: sudo apt install yara")
                post_output("   macOS: brew install yara")
                return

            payload = command.split("__YARA__:", 1)[1]
            parts = payload.split(":", 2)  # rules_name, rules_b64, [scan_path]
            if len(parts) < 2:
                raise ValueError("bad __YARA__ format")

            rules_name = (parts[0] or "rules.yar").strip()
            rules_b64 = parts[1].strip()
            scan_path_encoded = parts[2].strip() if len(parts) == 3 else ""

            # Decode scan_path if it was encoded
            if scan_path_encoded:
                try:
                    scan_path = base64.b64decode(scan_path_encoded.encode()).decode()
                except:
                    # Fallback to original if decoding fails
                    scan_path = scan_path_encoded
            else:
                scan_path = "."

            # Provide scan scope information
            scan_info = get_scan_path_info(scan_path)
            post_output(f"🔍 Starting YARA scan: {scan_info}")
            post_output(f"📁 Rules file: {rules_name}")
            post_output(f"📂 Target path: {scan_path}")
            post_output(f"🔧 Using YARA: {YARA_EXE}")

            # Warn about long scan times for system-wide scans
            if scan_path in ["C:\\", "/"]:
                post_output("⏰ Warning: Full system scan detected - this may take 30+ minutes")
                post_output("💡 Consider scanning specific directories for faster results")
                post_output("🔄 Starting comprehensive system scan...")

            # Create rules file
            rules_path = Path(tempfile.gettempdir()) / rules_name
            rules_content = base64.b64decode(rules_b64)
            rules_path.write_bytes(rules_content)

            # Validate rules file was created
            if not rules_path.exists():
                post_output("❌ Failed to create rules file")
                return

            post_output(f"📝 Rules file created: {rules_path}")
            post_output(f"📝 Rules file size: {len(rules_content)} bytes")
            # Show first few lines of rules file for debugging
            try:
                rules_text = rules_content.decode('utf-8', errors='ignore')
                rules_lines = rules_text.split('\n')[:5]
                post_output(f"📝 Rules file preview: {rules_lines}")
            except:
                post_output("📝 Rules file preview: (binary or encoding issue)")
            post_output("🚀 Executing YARA scan...")

            # Test scan permissions before starting full scan
            if scan_path in ["C:\\", "/"]:
                post_output("🔍 Testing scan permissions...")
                test_result = test_scan_permissions(scan_path)
                if not test_result:
                    post_output("❌ Permission test failed - scan may not work properly")
                    post_output("💡 Consider running agent as Administrator or using specific directories")
                else:
                    post_output("✅ Permission test passed - proceeding with scan")

            # Set appropriate timeout based on scan scope
            if scan_path in ["C:\\", "/"]:
                timeout = 3600  # 1 hour for full system scans
                post_output("⏱️  Scan timeout set to 1 hour for full system scan")
            elif scan_path == ".":
                timeout = 60  # 1 minute for current directory scans
                post_output("⏱️  Scan timeout set to 1 minute for current directory scan")
            else:
                timeout = 300  # 5 minutes for directory scans
                post_output("⏱️  Scan timeout set to 5 minutes for directory scan")

            # Start YARA scan with progress monitoring
            start_time = time.time()

            # Use subprocess.Popen for better control and progress monitoring
            cmd = [YARA_EXE, str(rules_path), scan_path]
            post_output(f"🔧 Command: {' '.join(cmd)}")
            post_output(f"🔧 Rules file path: {rules_path}")
            post_output(f"🔧 Scan path: {scan_path}")

            # Verify scan path exists
            if not Path(scan_path).exists():
                post_output(f"❌ Warning: Scan path does not exist: {scan_path}")
                post_output("   The scan may fail or return no results")
            else:
                post_output(f"✅ Scan path exists: {scan_path}")
                # Show what's in the directory
                try:
                    items = list(Path(scan_path).iterdir())
                    post_output(f"📁 Directory contains {len(items)} items")
                    if items:
                        post_output(f"📁 Sample items: {[item.name for item in items[:3]]}")
                except Exception as e:
                    post_output(f"⚠️  Could not list directory contents: {e}")

            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )

                stdout_text = completed.stdout or ""
                stderr_text = completed.stderr or ""

                output_lines = [line for line in stdout_text.split('\n') if line.strip()]
                error_lines = [line for line in stderr_text.split('\n') if line.strip()]

                elapsed_time = time.time() - start_time

                post_output(f"🔍 Debug: Captured {len(output_lines)} stdout lines")
                post_output(f"🔍 Debug: Captured {len(error_lines)} stderr lines")
                if output_lines:
                    post_output(f"🔍 Debug: First few stdout lines: {output_lines[:3]}")
                if error_lines:
                    post_output(f"🔍 Debug: First few stderr lines: {error_lines[:3]}")

                return_code = completed.returncode

                if return_code in (0, 1):
                    all_output = output_lines + error_lines
                    if all_output:
                        results = [line for line in all_output if line.strip()]
                        post_output(f"🔍 Debug: Filtered to {len(results)} non-empty results")
                        if results:
                            post_output(f"✅ YARA scan completed successfully in {elapsed_time:.1f}s")
                            post_output(f"📊 Found {len(results)} matches:")
                            chunk_size = 10
                            for i in range(0, len(results), chunk_size):
                                chunk = results[i:i + chunk_size]
                                post_output(f"   Results {i + 1}-{min(i + chunk_size, len(results))}: {chunk}")
                        else:
                            post_output(f"✅ YARA scan completed successfully in {elapsed_time:.1f}s")
                            post_output("📊 No matches found")
                    else:
                        post_output(f"✅ YARA scan completed successfully in {elapsed_time:.1f}s")
                        post_output("📊 No output generated")
                else:
                    post_output(f"❌ YARA scan failed with return code {return_code}")
                    if error_lines:
                        post_output(f"🔍 Error details: {error_lines}")
                    if output_lines:
                        post_output(f"📄 Partial output: {output_lines}")
            except Exception as e:
                post_output(f"❌ Error during scan execution: {e}")
            finally:
                try:
                    rules_path.unlink()
                    # Не шлём отдельное сообщение, чтобы не перетирать итог скана
                except Exception as e:
                    post_output(f"⚠️  Warning: Could not clean up rules file: {e}")


        except Exception as e:
            post_output(f"❌ YARA command exception: {e}")
            # Clean up on any error
            try:
                if 'rules_path' in locals() and rules_path.exists():
                    rules_path.unlink()
            except:
                pass
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

    print(f"🔄 VAST Agent is now running and connecting to: {SERVER_BASE}")
    print("   • Sending heartbeats every 1 second")
    print("   • Monitoring network connections every 15 seconds")
    print("   • YARA scanning configured and ready")
    print("   • Ready to receive commands from security analysts")
    print("   • Press Ctrl+C to stop the agent")
    print()

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
