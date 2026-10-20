import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import read_tapo as reader


class ReaderTests(unittest.TestCase):
    def test_units_and_zero(self):
        data = reader.normalize({'get_emeter_data': {'voltage_mv': 231450, 'current_ma': 0,
                                                     'power_mw': 1570},
                                 'get_energy_usage': {'today_energy': 125, 'month_energy': 3456}})
        self.assertEqual(data['voltage']['value'], 231.45)
        self.assertEqual(data['current']['value'], 0)
        self.assertEqual(data['power']['value'], 1.57)
        self.assertEqual(data['energy_today']['value'], .125)
        self.assertEqual(data['energy_month']['value'], 3.456)
        self.assertIsNone(data['frequency']['value'])

    def test_no_frequency_invention(self):
        data = reader.normalize({'get_emeter_data': {'frequency': 5000, 'voltage_mv': True}})
        self.assertIsNone(data['frequency']['value'])
        self.assertIsNone(data['voltage']['value'])
        self.assertEqual(reader.normalize({'get_current_power': {'current_power': 7}})['power']['value'], 7)
        self.assertEqual(reader.normalize({'get_energy_usage': {'current_power': 700}})['power']['value'], .7)

    def test_partial_response_and_read_only(self):
        class Protocol:
            calls = []
            async def query(self, payload):
                method = next(iter(payload))
                self.calls.append(method)
                if method not in reader.METHODS:
                    raise AssertionError('Non-read operation')
                if method == 'get_emeter_data':
                    raise RuntimeError('unsupported')
                values = {'get_device_info': {'model': 'P110', 'device_on': True, 'device_id': 'private'},
                          'get_energy_usage': {'current_power': 1250, 'today_energy': 0},
                          'get_current_power': {'current_power': 1}}
                return {method: values[method]}
        class Device:
            protocol = Protocol()
            disconnect = AsyncMock()
        device = Device()
        with patch('kasa.Device.connect', new=AsyncMock(return_value=device)):
            result = asyncio.run(reader.read_once('192.0.2.1', {'username': 'test', 'password': 'secret'}))
        self.assertEqual(device.protocol.calls, list(reader.METHODS))
        self.assertTrue(result['reachable'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['measurements']['power']['value'], 1.25)
        self.assertIsNone(result['measurements']['voltage']['value'])
        self.assertNotIn('device_id', result['device'])
        self.assertNotIn('secret', json.dumps(result))
        device.disconnect.assert_awaited_once()

    def test_discovery_failure_is_not_grid_off(self):
        with patch('kasa.Device.connect', new=AsyncMock(side_effect=RuntimeError('private details'))):
            result = asyncio.run(reader.read_once('192.0.2.1', {'username': 'test', 'password': 'secret'}))
        self.assertFalse(result['reachable'])
        self.assertEqual(result['error'], 'RuntimeError')
        self.assertNotIn('private details', json.dumps(result))
        self.assertIsNone(result['measurements']['power']['value'])

    def test_private_atomic_output(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'result.json'
            reader.atomic_json(path, {'a': 1})
            self.assertEqual(json.loads(path.read_text()), {'a': 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_name(path.name + '.tmp').exists())


if __name__ == '__main__':
    unittest.main()
