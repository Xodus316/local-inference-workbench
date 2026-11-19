"""Generated DOM suites must not mask the application's rejected requests."""
import unittest
from types import SimpleNamespace

from orchestrator.project_test_lint import defects


def lint_script(script):
    source = (
        "import unittest\nclass UI(unittest.TestCase):\n"
        "    def test_auth(self):\n        script = r'''\n" + script + "\n'''\n"
        "        self.assertGreater(len(script), 0)\n"
    )
    return defects(SimpleNamespace(files=[SimpleNamespace(path="tests/test_ui.py", content=source)]))


class RejectionSuppressionLintTests(unittest.TestCase):
    def test_embedded_auth_repair_must_not_suppress_401_or_403(self):
        found = lint_script("""
window.addEventListener('unhandledrejection', event => {
  if (event.reason?.response?.status === 401 || event.reason?.response?.status === 403) {
    event.preventDefault();
  }
});
""")
        self.assertEqual([(item["code"], item["names"]) for item in found],
                         [("suppressed_unhandled_rejection", ["test_auth"])])

    def test_parenthesized_and_function_callbacks_are_detected(self):
        for callback in ("(error) =>", "function (error)"):
            with self.subTest(callback=callback):
                found = lint_script('window.addEventListener("unhandledrejection", ' + callback
                                    + ' { const detail = "}"; error.preventDefault(); });')
                self.assertEqual([item["code"] for item in found], ["suppressed_unhandled_rejection"])

    def test_forms_and_observation_only_rejection_handlers_are_allowed(self):
        self.assertEqual(lint_script("""
window.addEventListener('unhandledrejection', event => {
  console.log(event.reason);
});
document.addEventListener('submit', event => {
  event.preventDefault();
});
"""), [])

    def test_comment_or_string_mentions_do_not_hide_errors(self):
        self.assertEqual(lint_script("""
// window.addEventListener('unhandledrejection', event => { event.preventDefault(); });
window.addEventListener('unhandledrejection', event => {
  // event.preventDefault();
  console.log('event.preventDefault() would hide this error', event.reason);
});
"""), [])


if __name__ == "__main__":
    unittest.main()
