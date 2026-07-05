"""Build candidate files for 'take the bill amount' — PAID bills only, from the two
buckets (amount diff/genuine, qty mismatch amounts differ). Target (db_amount col) =
the SHEET's Bill Amount. Split single/multi line. Zoho ids from raw CSVs."""
import csv, glob, re, os
from collections import Counter

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()

poid = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        r = norm(row["CF.PO Number"])
        if r.startswith("MD"):
            poid.setdefault(r, row["Purchase Order ID"])
billid = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        for t in row["PurchaseOrder"].split(","):
            n = norm(t)
            if n.startswith("MD"):
                billid.setdefault((row["Bill Number"], n), row["Bill ID"])

rows = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def inventory_only(r):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    return bool(toks) and toks <= INVSET


BUCKETS = {
    "amountdiff": lambda r: r["Reason"].startswith("amount diff"),
    "qtymm": lambda r: r["Reason"] == "qty mismatch (amounts differ)",
}
for bkey, pred in BUCKETS.items():
    picked = [r for r in rows if r["Attach Status"] == "Not Attached" and r["Bill Status"] == "Paid"
              and inventory_only(r) and pred(r)]
    for lc, lpred in [("single", lambda r: r["Bill Item Count"] == "1"), ("multi", lambda r: r["Bill Item Count"] != "1")]:
        out_rows, miss = [], 0
        for r in [x for x in picked if lpred(x)]:
            ref = r["MD Reference(s)"].strip()
            bid = billid.get((r["Bill Number"], norm(ref)))
            pid = poid.get(norm(ref))
            if not bid or not pid:
                miss += 1; continue
            out_rows.append({"bill_id": bid, "po_id": pid, "md_ref": ref, "bill_number": r["Bill Number"],
                             "po_number": r["PO Numbers"], "db_amount": r["Bill Amount"],  # TARGET = sheet bill amount
                             "bill_amount": r["Bill Amount"], "po_amount": r["PO Amount"], "n_lines": r["Bill Item Count"]})
        out = os.path.join(INV, f"takebill_{bkey}_{lc}.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                               "db_amount", "bill_amount", "po_amount", "n_lines"])
            w.writeheader(); w.writerows(out_rows)
        print(f"{bkey}/{lc}: {len(out_rows)} paid bills (miss id {miss}) -> {out}")
