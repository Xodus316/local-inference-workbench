import unittest
from unittest.mock import Mock
from orchestrator.config import Config
from orchestrator.provider import NativeProvider


class AdmissionTests(unittest.TestCase):
    def test_cloud_tag_or_remote_metadata_is_rejected_before_dispatch(self):
        provider=NativeProvider(Config())
        provider.ollama=Mock()
        for name,tag,show in [
            ("example:cloud", {}, {}),
            ("example:local", {"remote_model":"other"}, {}),
            ("example:local", {}, {"remote_host":"cloud"}),
            ("example:local", {}, {"capabilities":["embedding"]}),
        ]:
            with self.subTest(tag=tag,show=show):
                provider.ollama.models.return_value=[dict(name=name, **tag)]
                provider.ollama.show.return_value=show
                with self.assertRaises(ValueError):
                    provider.admit(name,{},{"messages":[]},"primary")
        provider.ollama.stream_chat.assert_not_called()
