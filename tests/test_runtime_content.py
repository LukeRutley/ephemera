import unittest
from pathlib import Path

import app as ephemera_app


class RuntimeContentTests(unittest.TestCase):
    def setUp(self) -> None:
        ephemera_app.THREADS.clear()
        ephemera_app.write_runtime_content("")
        self.client = ephemera_app.app.test_client()

    def test_current_html_falls_back_to_runtime_memory(self) -> None:
        expected_html = "<html><body>runtime</body></html>"
        ephemera_app.write_runtime_content(expected_html)

        thread = {"messages": [], "html_history": [], "current_html_index": -1}

        self.assertEqual(ephemera_app.current_html(thread), expected_html)

    def test_back_and_next_keep_runtime_html_in_memory(self) -> None:
        session_id = "runtime-history"
        ephemera_app.THREADS[session_id] = {
            "messages": [],
            "html_history": ["<html><body>first</body></html>", "<html><body>second</body></html>"],
            "current_html_index": 1,
        }
        ephemera_app.write_runtime_content("<html><body>second</body></html>")

        with self.client.session_transaction() as flask_session:
            flask_session["session_id"] = session_id

        back_response = self.client.post("/back")
        self.assertEqual(back_response.status_code, 200)
        self.assertEqual(ephemera_app.read_runtime_content(), "<html><body>first</body></html>")

        next_response = self.client.post("/next")
        self.assertEqual(next_response.status_code, 200)
        self.assertEqual(ephemera_app.read_runtime_content(), "<html><body>second</body></html>")

    def test_new_topic_clears_runtime_html(self) -> None:
        session_id = "new-topic"
        ephemera_app.THREADS[session_id] = {
            "messages": ["hello"],
            "html_history": ["<html><body>existing</body></html>"],
            "current_html_index": 0,
        }
        ephemera_app.write_runtime_content("<html><body>existing</body></html>")

        with self.client.session_transaction() as flask_session:
            flask_session["session_id"] = session_id

        response = self.client.post("/new-topic")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ephemera_app.read_runtime_content(), "")
        self.assertEqual(response.get_json()["html"], "")

    def test_content_html_is_not_tracked_in_repo(self) -> None:
        content_path = Path("/home/runner/work/ephemera/ephemera/content.html")
        self.assertFalse(content_path.exists())


if __name__ == "__main__":
    unittest.main()
