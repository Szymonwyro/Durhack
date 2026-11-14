"""Print this machine's LAN IP (for iPhones / the Quest / other devices to connect to)."""
import socket

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("10.255.255.255", 1))  # no packets sent; just picks the outbound interface
    ip = s.getsockname()[0]
except OSError:
    ip = "127.0.0.1"
finally:
    s.close()
print(ip)
print(f"other devices: https://{ip}:8000/")
