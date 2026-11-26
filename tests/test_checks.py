"""Runs the scripted checks in tests/checks (ayanamsas, progressions, app links, email
delivery), each in its own process with its own throwaway database."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, 'checks')
REPO = os.path.dirname(HERE)


@pytest.mark.parametrize('script', ['route_check.py', 'check_progressions.py', 'check_signup.py', 'check_delivery.py'])
def test_check(script):
    env = {k: v for k, v in os.environ.items() if k not in ('DATABASE_PATH', 'EPHEMERAL_NO_ENV_FILE')}
    done = subprocess.run(
        [sys.executable, script, REPO], cwd=CHECKS, env=env, capture_output=True, text=True, timeout=600
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
