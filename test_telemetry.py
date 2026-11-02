import unittest
from unittest.mock import MagicMock, patch

import app_updater
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

    def test_launch_checkin_runs_before_update_request(self):
        events = []
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"draft":true}'

        def post(path, payload):
            events.append((path, payload))

        def request(url):
            events.append(("update", url))
            return response

        payload = {"install_id": "a-real-payload"}
        worker = app_updater.UpdateCheckWorker("0.10.4", payload)
        with patch.object(app_updater, "post_usage_event", side_effect=post), \
             patch.object(app_updater, "_request", side_effect=request):
            worker.run()

        self.assertEqual(events, [
            ("checkin", payload), ("update", app_updater.LATEST_RELEASE_URL),
        ])

    def test_failed_launch_checkin_does_not_skip_update_request(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"draft":true}'
        worker = app_updater.UpdateCheckWorker("0.10.4", {"install_id": "a-real-payload"})
        with patch.object(app_updater, "post_usage_event", side_effect=OSError("offline")), \
             patch.object(app_updater, "_request", return_value=response) as request:
            worker.run()

        request.assert_called_once_with(app_updater.LATEST_RELEASE_URL)


if __name__ == "__main__":
    unittest.main()
