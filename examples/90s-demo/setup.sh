#!/usr/bin/env bash
# Make the folder the 90-second demo runs in: a few documents and a small project
# with a bug in it. Run it again to start over.
#
#   examples/90s-demo/setup.sh            # makes ~/praxis-demo
#   examples/90s-demo/setup.sh /some/dir  # or somewhere else
set -euo pipefail

DEMO=${1:-"$HOME/praxis-demo"}
rm -rf "$DEMO"
mkdir -p "$DEMO/Documents" "$DEMO/projects/tally"

cat >"$DEMO/Documents/q3-report.md" <<'EOF'
# Q3 report

Revenue is up 12% on Q2, driven by the new team plan. Churn fell to 3.1%.

## What went well
- The team plan sold faster than forecast: 340 teams in eight weeks.
- Support response times halved after the help-centre rewrite.

## What did not
- The Android release slipped by five weeks.
- Two enterprise deals moved to Q4.

## Next quarter
- Ship the Android release.
- Hire a designer and a second support engineer.
EOF

cat >"$DEMO/Documents/ideas.md" <<'EOF'
# Ideas

- A weekly digest of what changed in the shared folders
- Offline mode for the mobile app
- Let teams export their data as CSV
EOF

cat >"$DEMO/projects/tally/expenses.csv" <<'EOF'
date,item,amount
2026-10-02,train tickets,48.50
2026-10-05,team lunch,126.80
2026-10-09,books,35.00
2026-10-14,conference pass,299.00
2026-10-21,taxi,22.40
EOF

# The bug: the amounts have decimals, and int() cannot read "48.50".
cat >"$DEMO/projects/tally/tally.py" <<'EOF'
import csv

total = 0
with open("expenses.csv", newline="") as file:
    for row in csv.DictReader(file):
        total += int(row["amount"])

print(f"Spent in October: {total:.2f}")
EOF

# Something changed today, something this week, something long ago.
touch -d "3 days ago" "$DEMO/Documents/q3-report.md"
touch -d "40 days ago" "$DEMO/Documents/ideas.md"
touch "$DEMO/projects/tally/tally.py" "$DEMO/projects/tally/expenses.csv"

echo "Demo folder ready: $DEMO"
echo "Start Praxis on it:  PRAXIS_FS_ROOTS=$DEMO ./Praxis*.AppImage"
