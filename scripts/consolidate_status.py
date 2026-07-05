"""
Consolidate the .bak snapshots into a single bill_po_reconciliation.csv by rebuilding the
'Attach Status' column from the authoritative ledgers (attach_results + attach_reverted),
which reflect the true current state. Keyed by (Bill Number, PO Numbers).
"""
import csv, os

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
RECON = os.path.join(INV, "bill_po_reconciliation.csv")


def load(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig")))


def fmt_result(res, reason):
    return {"OK": "Attached", "CHECK": "Attached (needs check)",
            "FAIL": f"Failed: {reason}", "SKIP": f"Skipped: {reason}"}.get(res, res)


# authoritative current state (results) takes precedence; reverted is historical
results = {}
for r in load(os.path.join(INV, "attach_results.csv")):
    results[(r["bill_number"], r["po_number"])] = fmt_result(r["result"], r.get("reason", ""))
reverted = {}
for r in load(os.path.join(INV, "attach_reverted.csv")):
    reverted[(r["bill_number"], r["po_number"])] = f"Reverted: {r.get('reason','')}"


def status_for(bill_no, po_nums):
    key = (bill_no, po_nums)
    if key in results:
        return results[key]
    if key in reverted:
        return reverted[key]
    return "Not processed"


rows = load(RECON)
changed = 0
from collections import Counter
dist = Counter()
for r in rows:
    new = status_for(r["Bill Number"], r["PO Numbers"])
    if r.get("Attach Status") != new:
        changed += 1
    r["Attach Status"] = new
    dist[new.split(":")[0]] += 1

with open(RECON, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

print(f"rows={len(rows)} Attach Status updated on {changed} rows")
print("Attach Status distribution:")
for k, v in dist.most_common():
    print(f"  {k}: {v}")
