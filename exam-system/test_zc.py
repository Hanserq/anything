import socket
import time
from zeroconf import ServiceInfo, Zeroconf

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()

local_ip = get_ip()
info = ServiceInfo(
    "_http._tcp.local.",
    "ExamLAN._http._tcp.local.",
    addresses=[socket.inet_aton(local_ip)],
    port=8000,
    properties={"path": "/"},
    server="examlan.local.",
)
zc = Zeroconf()
zc.register_service(info)
print(f"Registered examlan.local at {local_ip}")
try:
    time.sleep(5)
finally:
    zc.unregister_service(info)
    zc.close()
