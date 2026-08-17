from pathlib import Path
import github_backtest_runner as bt

bt.MONTHS = ["2025-02","2025-05","2025-08","2025-11","2026-02","2026-05","2026-07"]
bt.STOP_GRID = [5]
bt.OUT = Path("quick_output")
bt.OUT.mkdir(exist_ok=True)

if __name__ == "__main__":
    bt.main()
