"""Candidates for multi-PO attach: one bill -> multiple MD PO numbers. Inventory-only + paid.
Resolves bill_id and the list of (ref, po_id) for each PO. -> multipo_candidates.csv"""
import csv, glob, re, os
from collections import Counter

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def invonly(r):
    # EXPANDED scope: any bill whose PO has at least one inventory item (alone or mixed with others)
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    return bool(toks & INVSET)


poid = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            poid.setdefault(ref, row["Purchase Order ID"])
billid = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        billid.setdefault(row["Bill Number"], row["Bill ID"])

rows = [r for r in csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig"))
        if r["Attach Status"] == "Not Attached" and r["Multi-PO Bill"] == "Yes"
        and r["Bill Status"] == "Paid" and invonly(r)]
out, miss = [], 0
for r in rows:
    refs = [t.strip() for t in r["MD Reference(s)"].split(";") if t.strip()]
    bid = billid.get(r["Bill Number"])
    pids = [poid.get(norm(x)) for x in refs]
    if not bid or not all(pids):
        miss += 1
        continue
    out.append({"bill_id": bid, "bill_number": r["Bill Number"], "n_pos": len(refs),
                "md_refs": ";".join(refs), "po_ids": ";".join(pids),
                "po_numbers": r["PO Numbers"], "bill_amount": r["Bill Amount"]})

with open(os.path.join(INV, "multipo_candidates.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "bill_number", "n_pos", "md_refs", "po_ids", "po_numbers", "bill_amount"])
    w.writeheader(); w.writerows(out)
print("multi-PO inventory+paid candidates:", len(out), "| missing id:", miss)
print("POs per bill:", dict(sorted(Counter(c["n_pos"] for c in out).items())))
