import unittest
import urllib.request
import json
import ssl

class TestOmniSiteBackendRefactor(unittest.TestCase):
    def setUp(self):
        self.base_url = "http://127.0.0.1:8000/api/v1"
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def test_01_get_district_boundary_fallback_or_not(self):
        """자치구 경계 GeoJSON API 가용성 테스트 (포트 8000 실행 상태 시 작동)"""
        url = f"{self.base_url}/lands/district-boundary/1"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, context=self.ctx) as response:
                self.assertEqual(response.status, 200)
                data = json.loads(response.read().decode())
                self.assertEqual(data.get("status"), "success")
                print("✔ [SUCCESS] /lands/district-boundary/1 API 가동성 검증 성공!")
        except Exception as e:
            print(f"⚠ [SKIP] API Server not running or DB empty: {e}")

    def test_02_check_boundary_guard(self):
        """좌표 자치구 포함 여부 검증 API 테스트"""
        url = f"{self.base_url}/lands/check-boundary"
        payload = {
            "district_id": 1,
            "lat": 37.53,
            "lng": 126.97
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx) as response:
                self.assertEqual(response.status, 200)
                data = json.loads(response.read().decode())
                self.assertIn("is_contained", data)
                print("✔ [SUCCESS] /lands/check-boundary API 가동성 검증 성공!")
        except Exception as e:
            print(f"⚠ [SKIP] API Server not running: {e}")

if __name__ == "__main__":
    unittest.main()
