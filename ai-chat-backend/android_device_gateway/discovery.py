"""Discover one paired wireless device; endpoints stay inside the transport."""
import asyncio
import ipaddress
import re
import subprocess
import time


def device_rows(output):
    return [tuple(parts[:2]) for line in output.splitlines()
            if len(parts := line.split()) >= 2 and parts[0] != 'List']


def is_wireless_route(value):
    match = re.fullmatch(r'(\d+\.\d+\.\d+\.\d+):(\d+)', value)
    if match:
        return ipaddress.ip_address(match[1]).is_private and 0 < int(match[2]) < 65536
    return re.fullmatch(r'adb-[A-Za-z0-9_-]+\._adb-tls-connect\._tcp\.?', value) is not None


def discovery_allowed(rows):
    # A stale TLS route can outlive its random port. Never switch away from a
    # ready phone, an authorization prompt, a USB route, or an explicit serial.
    return all(state == 'offline' and is_wireless_route(serial) for serial, state in rows)

def reconnect_once():
    deadline = time.monotonic() + 6
    def run(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('adb_discovery', 6)
        return subprocess.run(['adb', *args], capture_output=True,
                              timeout=min(2, remaining), shell=False)
    def rows():
        result = run('devices')
        if result.returncode != 0:
            raise ValueError('devices_unavailable')
        return device_rows(result.stdout.decode('utf-8', 'replace'))
    try:
        if not discovery_allowed(rows()):
            return False
        endpoints = {}
        # Cold-start mDNS can need one short discovery interval.
        for attempt in range(3):
            result = run('mdns', 'services')
            if result.returncode != 0:
                return False
            for line in result.stdout.decode('utf-8', 'replace').splitlines():
                parts = line.split()
                if len(parts) != 3 or parts[1].rstrip('.') != '_adb-tls-connect._tcp':
                    continue
                if is_wireless_route(parts[2]) and re.fullmatch(r'\d+\.\d+\.\d+\.\d+:\d+', parts[2]):
                    endpoints.setdefault(parts[2], set()).add(parts[0] + '.' + parts[1].rstrip('.'))
            if len(endpoints) > 1:
                return False
            if endpoints:
                break
            if attempt < 2:
                time.sleep(.2)
        if not endpoints:
            return False
        endpoint, aliases = next(iter(endpoints.items()))
        # Discovery may have yielded while another phone/permission appeared.
        current = rows()
        ready = [serial for serial, state in current if state == 'device']
        if ready:
            return len(ready) == 1 and ready[0].rstrip('.') in ({endpoint} | aliases)
        if not discovery_allowed(current):
            return False
        result = run('connect', endpoint)
        if result.returncode != 0 or b'connected to' not in result.stdout:
            return False
        while time.monotonic() < deadline:
            current = rows()
            if any(state == 'unauthorized' for _, state in current):
                return False
            ready = [serial for serial, state in current if state == 'device']
            if ready:
                return len(ready) == 1 and ready[0].rstrip('.') in ({endpoint} | aliases)
            time.sleep(.15)
        return False
    except (OSError, subprocess.SubprocessError, ValueError):
        return False

async def reconnect_wireless():
    return await asyncio.to_thread(reconnect_once)
