#!/usr/bin/env python3
"""Separate, read-only Tapo P110 probe. No relay, configuration or Telegram writes."""
import argparse
import asyncio
import getpass
import ipaddress
import json
import math
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

BASE = Path(os.environ.get('TAPO_DATA_DIR', str(Path.home() / '.local/share/tapo-p110-monitor'))).expanduser()
METHODS = ('get_device_info', 'get_energy_usage', 'get_current_power', 'get_emeter_data')
INFO_KEYS = ('model', 'hw_ver', 'fw_ver', 'device_on', 'rssi', 'signal_level',
             'overheated', 'on_time', 'time_usage', 'power_protection_status')


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write('\n')
    os.replace(temp, path)


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def normalize(responses):
    def measurement(unit, candidates):
        for method, field, divisor in candidates:
            raw = responses.get(method, {}).get(field)
            if number(raw):
                return {'value': raw / divisor, 'unit': unit, 'status': 'available',
                        'source': method + '.' + field}
        return {'value': None, 'unit': unit, 'status': 'not_reported', 'source': None}
    return {
        'voltage': measurement('V', [('get_emeter_data', 'voltage_mv', 1000)]),
        'current': measurement('A', [('get_emeter_data', 'current_ma', 1000)]),
        'power': measurement('W', [('get_emeter_data', 'power_mw', 1000),
                                  ('get_energy_usage', 'current_power', 1000),
                                  ('get_current_power', 'current_power', 1)]),
        'energy_today': measurement('kWh', [('get_energy_usage', 'today_energy', 1000)]),
        'energy_month': measurement('kWh', [('get_energy_usage', 'month_energy', 1000)]),
        # Only an explicitly unit-labelled field can establish measured frequency.
        # Never substitute the nominal mains frequency or guess an undocumented scale.
        'frequency': measurement('Hz', [('get_emeter_data', 'frequency_hz', 1)]),
    }


async def read_once(host, credentials):
    from kasa import (Device, DeviceConfig, DeviceConnectionParameters,
                      DeviceFamily, DeviceEncryptionType, Credentials)
    device = None
    responses, methods = {}, {}
    report = {'schema': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'host': host, 'transport': 'KLAP v2', 'reachable': False, 'read_only': True, 'device': {},
              'methods': methods, 'raw_energy': {}, 'measurements': normalize({}),
              'note': 'Unreachable does not prove a mains outage. Relay state is not mains presence.'}
    try:
        config = DeviceConfig(host=host, timeout=8,
            credentials=Credentials(credentials['username'], credentials['password']),
            connection_type=DeviceConnectionParameters(DeviceFamily.SmartTapoPlug,
                DeviceEncryptionType.Klap, login_version=2))
        # Explicit KLAP avoids TPAP-preferred discovery on this tested P110 firmware.
        device = await asyncio.wait_for(Device.connect(config=config), 30)
        if device is None:
            report['error'] = 'DiscoveryEmpty'
            return report
        for method in METHODS:
            try:
                result = await asyncio.wait_for(device.protocol.query({method: None}), 8)
                payload = result.get(method)
                if not isinstance(payload, dict):
                    methods[method] = {'status': 'unavailable', 'error': 'NoObjectResponse'}
                    continue
                responses[method] = payload
                methods[method] = {'status': 'ok', 'fields': sorted(payload.keys())}
            except Exception as exc:
                # Exception messages/tracebacks may contain sensitive device details.
                methods[method] = {'status': 'unavailable', 'error': type(exc).__name__}
        info = responses.get('get_device_info', {})
        report['device'] = {k: info[k] for k in INFO_KEYS if k in info}
        report['reachable'] = bool(responses)
        # Keep numeric energy fields for discovery without exporting account/device IDs.
        report['raw_energy'] = {
            method: {k: v for k, v in body.items() if number(v)}
            for method, body in responses.items() if method != 'get_device_info'}
        report['measurements'] = normalize(responses)
        report['complete'] = all(m.get('status') == 'ok' for m in methods.values())
        return report
    except Exception as exc:
        report['error'] = type(exc).__name__
        report['complete'] = False
        return report
    finally:
        if device is not None:
            try:
                await asyncio.wait_for(device.disconnect(), 5)
            except Exception:
                pass


def configure(host):
    if not sys.stdin.isatty():
        raise SystemExit('Run configure in an interactive Termux or SSH terminal.')
    username = input('Tapo account email: ').strip()
    password = getpass.getpass('Tapo password (hidden): ')
    if not username or not password:
        raise SystemExit('Empty credentials; nothing saved.')
    atomic_json(BASE / 'credentials.json', {'username': username, 'password': password, 'host': host})
    print('Credentials saved locally with owner-only permissions. No login attempted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['configure', 'read'], nargs='?', default='read')
    parser.add_argument('--host', help='Local IPv4 address; saved by configure')
    parser.add_argument('--samples', type=int, default=1)
    parser.add_argument('--interval', type=float, default=10)
    args = parser.parse_args()
    if args.mode == 'configure' and not args.host:
        parser.error('configure requires --host with your plug address')
    if args.host:
        ip = ipaddress.ip_address(args.host)
        if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
            parser.error('Use the local IPv4 address of the plug.')
    if not 1 <= args.samples <= 1000 or args.interval < 5:
        parser.error('samples: 1..1000; interval: at least 5 seconds')
    import fcntl
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (BASE / '.reader.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Reader already running.')
        if args.mode == 'configure':
            configure(args.host)
            return
        path = BASE / 'credentials.json'
        if not path.exists():
            raise SystemExit('First run: ./run configure --host YOUR_PLUG_IP')
        if path.stat().st_mode & 0o077:
            raise SystemExit('Credentials permissions must be 600.')
        credentials = json.loads(path.read_text())
        host = args.host or credentials.get('host')
        if not host:
            raise SystemExit('Configure the plug address first.')
        import time
        exit_code = 0
        for n in range(args.samples):
            report = asyncio.run(read_once(host, credentials))
            atomic_json(BASE / 'latest.json', report)
            if report['reachable']:
                atomic_json(BASE / 'last_success.json', report)
            else:
                exit_code = 1
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report.get('error') == 'AuthenticationError':
                break
            if n + 1 < args.samples:
                time.sleep(args.interval)
        raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
