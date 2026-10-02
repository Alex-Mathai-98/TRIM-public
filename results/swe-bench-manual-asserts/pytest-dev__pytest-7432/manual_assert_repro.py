#!/usr/bin/env python3
# In-framing: promotes the agent's own test_reproduce_issue.py check to a hard
# assert. The agent ran `pytest -rs --runxfail` on a @pytest.mark.skip test and
# observed the BUG: the SKIPPED summary location points at src/_pytest/skipping.py
# (the skip() call site) instead of the test file. The fix restructures
# pytest_runtest_makereport so the skip-location fixing still runs under
# --runxfail. We assert the SKIPPED location is the TEST FILE, not skipping.py.
import os
import re
import subprocess
import sys
import tempfile

TEST_SRC = (
    "import pytest\n"
    "\n"
    "@pytest.mark.skip\n"
    "def test_skip_location():\n"
    "    assert 0\n"
)


def run_pytest(extra_args):
    fd, path = tempfile.mkstemp(suffix='.py')
    with os.fdopen(fd, 'w') as f:
        f.write(TEST_SRC)
    try:
        res = subprocess.run(
            [sys.executable, '-m', 'pytest', '-rs', *extra_args, path],
            capture_output=True, text=True, cwd='/testbed',
        )
        return res.stdout + res.stderr, os.path.basename(path)
    finally:
        os.unlink(path)


# With --runxfail, the skip location must still be the test file (not skipping.py).
out, base = run_pytest(['--runxfail'])
m = re.search(r'SKIPPED \[1\] (.+?): unconditional skip', out)
assert m, "no SKIPPED summary line found in --runxfail output:\n%s" % out
loc = m.group(1)
assert 'skipping.py' not in loc, \
    "--runxfail broke skip-location reporting (points at skipping.py): %r" % loc
assert base in loc, "skip location is not the test file %r: %r" % (base, loc)

print("ASSERT-OK", loc)
