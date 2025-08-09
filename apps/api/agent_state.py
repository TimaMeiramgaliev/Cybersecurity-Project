from threading import Lock
from datetime import datetime

agents_cache = {}
lock = Lock()

def update_agent(agent_id, data, remote_ip):
    with lock:
        agents_cache[agent_id] = {
            'id': agent_id,
            'hostname': data.get('hostname', ''),
            'ip': remote_ip,
            'os': data.get('os', ''),
            'mac': data.get('mac', ''),
            'last_seen': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            'online': True
        }

def get_all_agents():
    with lock:
        return list(agents_cache.values())

def get_agent(agent_id):
    with lock:
        return agents_cache.get(agent_id)
