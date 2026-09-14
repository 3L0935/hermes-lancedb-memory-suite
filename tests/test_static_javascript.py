import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_static_javascript_contracts():
    test_files = sorted(str(path.relative_to(ROOT)) for path in ROOT.glob("tests/static_*.test.cjs"))
    result = subprocess.run(
        ["node", "--test", *test_files],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
