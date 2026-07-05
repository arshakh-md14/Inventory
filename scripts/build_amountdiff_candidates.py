"""Build candidate files for Bucket A (Zoho PO != DB, Bill = DB) amount-diff bills,
segmented by line-count and qty-match. Carries the DB target amount. IDs from raw CSVs."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()


def load(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig"))) if os.path.exists(f) else []


# recon for line-count + status
recon = {(r["Bill Number"], r["PO Numbers"]): r for r in load(os.path.join(INV, "bill_po_reconciliation.csv"))}
# PO id by normalized CF.PO Number
poid = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            poid.setdefault(ref, row["Purchase Order ID"])
# bill id by (bill number, normalized PO ref)
billid = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        for t in raw.split(","):
            n = norm(t)
            if n.startswith("MD"):
                billid.setdefault((row["Bill Number"], n), row["Bill ID"])

rows = load(os.path.join(INV, "amount_diff_po_vs_db.csv"))
A = [r for r in rows if r["PO vs DB"] == "PO != DB" and r["Bill vs DB"] == "Bill = DB"]

buckets = defaultdict(list)
missing = 0
for r in A:
    key = (r["Bill Number"], r["PO Number"])
    rr = recon.get(key)
    if not rr:
        continue
    lines = int(rr["Bill Item Count"] or 0)
    lc = "single" if lines == 1 else "multi"
    qty = "qtymatch" if rr["Status"] == "Matched" else "qtymismatch"
    ref = r["MD Reference"].strip()
    bid = billid.get((r["Bill Number"], ref))
    pid = poid.get(norm(ref))
    if not bid or not pid:
        missing += 1
        continue
    buckets[(lc, qty)].append({"bill_id": bid, "po_id": pid, "md_ref": ref,
                               "bill_number": r["Bill Number"], "po_number": r["PO Number"],
                               "db_amount": r["DB PO Amount"], "bill_amount": r["Bill Amount"],
                               "zoho_po_amount": r["Zoho PO Amount"], "n_lines": lines})

for (lc, qty), rws in sorted(buckets.items()):
    out = os.path.join(INV, f"amountdiff_{lc}_{qty}.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                           "db_amount", "bill_amount", "zoho_po_amount", "n_lines"])
        w.writeheader(); w.writerows(rws)
    print(f"{lc}-line/{qty}: {len(rws)} -> {out}")
print("missing id (skipped):", missing)
