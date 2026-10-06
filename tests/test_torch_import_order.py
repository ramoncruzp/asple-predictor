import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def test_torch_available_after_api_main_import_in_clean_subprocess():
    if importlib.util.find_spec("torch") is None:
        pytest.skip("PyTorch no esta instalado")

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import api.main; from models.model_b_gru import TORCH_AVAILABLE; assert TORCH_AVAILABLE",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
