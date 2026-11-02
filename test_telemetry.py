import unittest
from unittest.mock import MagicMock, patch

from offline_chess import telemetry


class TelemetryRouteTests(unittest.TestCase):
    def test_checkin_uses_collector_api_route(self):
        response = MagicMock()
        response.status = 202
        response.__enter__.return_value = response
        with patch.object(telemetry, "USAGE_REPORT_URL", "https://collector.example"), \
             patch("urllib.request.urlopen", return_value=response) as urlopen:
            telemetry.post_usage_event("checkin", {"event_id": "check-in"})

        self.assertEqual(urlopen.call_args.args[0].full_url,
                         "https://collector.example/api/checkin")

    def test_feedback_uses_collector_api_route(self):
        response = MagicMock()
        response.status = 201
        response.__enter__.return_value = response
        with patch.object(telemetry, "USAGE_REPORT_URL", "https://collector.example/"), \
             patch("urllib.request.urlopen", return_value=response) as urlopen:
            telemetry.post_usage_event("feedback", {"event_id": "feedback"})

        self.assertEqual(urlopen.call_args.args[0].full_url,
                         "https://collector.example/api/feedback")

    def test_unknown_event_path_is_rejected(self):
        with patch.object(telemetry, "USAGE_REPORT_URL", "https://collector.example"):
            with self.assertRaises(ValueError):
                telemetry.post_usage_event("other", {})


if __name__ == "__main__":
    unittest.main()
