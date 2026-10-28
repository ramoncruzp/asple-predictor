import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "reetiquetar_meta.py", "estudios_meta.py", "medir_orden_intravela.py",
    "verificar_s1_meta.py", "mantenimiento_bd_meta.py", "meta_active_study.py",
)


def test_repo_scripts_show_help_from_repo_and_external_working_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    script_dir = ROOT / "scripts"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["ASPLE_OFFLINE"] = "1"
    for script in SCRIPTS:
        path = script_dir / script
        for cwd in (ROOT, outside):
            result = subprocess.run([sys.executable, str(path), "--help"], cwd=cwd,
                env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, f"{script} desde {cwd}: {result.stderr}"
            assert "usage:" in result.stdout.lower(), script
