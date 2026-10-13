"""Run both collectors independently, with a bounded timeout for each."""
import subprocess
import sys
from pathlib import Path
from market_amount import target_date


def main():
    base = Path(__file__).resolve().parent
    day = target_date().isoformat()
    failed = False
    for script, option in [('market_amount.py', '--end-date'), ('market_break_rate.py', '--date')]:
        try:
            result = subprocess.run([sys.executable, '-u', str(base / script), option, day],
                                    timeout=240, check=False)
            failed = failed or result.returncode != 0
        except subprocess.TimeoutExpired:
            print(f'{script}: 超时', file=sys.stderr, flush=True)
            failed = True
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
