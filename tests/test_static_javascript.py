import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_javascript_contracts():
    result = subprocess.run(
        ["node", "--test", "tests/static_http_retry.test.cjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
