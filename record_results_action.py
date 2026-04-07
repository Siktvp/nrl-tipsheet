"""
Called by the record_results GitHub Actions workflow.
Reads ROUND_ID and RESULTS_STR from environment variables,
updates history_db.json, regenerates dashboard.html.
"""

import os
import sys
sys.path.insert(0, ".")

from nrl_tracker import load_db, save_db, record_results

round_id = os.environ.get("ROUND_ID", "").strip()
results_str = os.environ.get("RESULTS_STR", "").strip()

if not round_id:
    print("ERROR: ROUND_ID environment variable is empty.")
    sys.exit(1)

if not results_str:
    print("ERROR: RESULTS_STR environment variable is empty.")
    sys.exit(1)

print(f"Recording results for {round_id}...")
print(f"Results string: {results_str}")

db = load_db()
db, summary = record_results(db, round_id, results_str)
save_db(db)

print(f"\nResult: {summary}")
print("\nNow regenerating dashboard...")
