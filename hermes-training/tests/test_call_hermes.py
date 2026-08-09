import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from call_hermes import call_hermes, HermesCallError


def test_call_hermes_returns_stdout_on_success():
    fake_result = MagicMock(returncode=0, stdout="這是 Hermes 的回答\n", stderr="")
    with patch("call_hermes.subprocess.run", return_value=fake_result) as mock_run:
        answer = call_hermes("問題")
        assert answer == "這是 Hermes 的回答"
        args, kwargs = mock_run.call_args
        assert args[0] == ["hermes", "-z", "問題"]


def test_call_hermes_raises_on_nonzero_exit():
    fake_result = MagicMock(returncode=1, stdout="", stderr="ollama 未啟動")
    with patch("call_hermes.subprocess.run", return_value=fake_result):
        try:
            call_hermes("問題")
            assert False, "should have raised"
        except HermesCallError as e:
            assert "ollama 未啟動" in str(e)
