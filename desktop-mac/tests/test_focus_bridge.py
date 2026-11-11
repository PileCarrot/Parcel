import unittest
from focus_bridge import focus_payload

class FocusPrivacyTests(unittest.TestCase):
    def test_only_live_running_work_is_blocked(self):
        self.assertTrue(focus_payload({'phase':'work','running':True},5)['focus'])
        for active,age in [({'phase':'rest','running':True},1),({'phase':'work','running':False},1),({'phase':'work','running':True},20),(None,0)]:
            self.assertFalse(focus_payload(active,age)['focus'])
    def test_payload_never_contains_task_or_token_data(self):
        payload=focus_payload({'phase':'work','running':True,'title':'private','token':'secret','task_id':'personal'},0)
        self.assertEqual(payload,{'app':'tomato-focus-v1','focus':True})

class LoopbackTests(unittest.TestCase):
    def test_http_is_read_only_and_rejects_nonlocal_origins(self):
        import json
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from focus_bridge import FocusBridge
        bridge=FocusBridge(port=0)
        self.assertIsNotNone(bridge.server)
        try:
            bridge.update({'phase':'work','running':True,'title':'private'})
            address=f'http://127.0.0.1:{bridge.port}/focus'
            with urlopen(address,timeout=2) as response:
                self.assertEqual(json.load(response),{'app':'tomato-focus-v1','focus':True})
            for headers in [{'Origin':'https://example.com'},{'Host':'evil.example'}]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(address,headers=headers),timeout=2)
                self.assertEqual(error.exception.code,403)
            with self.assertRaises(HTTPError):
                urlopen(Request(address,method='POST',data=b'{}'),timeout=2)
            bridge.update(None)
            with urlopen(address,timeout=2) as response:
                self.assertFalse(json.load(response)['focus'])
        finally:
            bridge.close()
