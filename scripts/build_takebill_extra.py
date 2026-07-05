"""Candidates for the 3 extra sub-buckets (paid + inventory), target = sheet Bill Amount:
  amount>=10 vs DB, qty-multiset mismatch (multi), qty-unit mismatch (multi).
Split single / multi."""
import csv, glob, re, os
from collections import Counter

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()
INVSET = {"Inventory (active)", "Inventory (inactive)"}


def invonly(r):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    return bool(toks) and toks <= INVSET


def grp(x):
    xl = x.lower()
    if "diff >= 10" in x or ("paid" in xl and "vs db" in xl):
        return "amount10"
    if "qty multiset" in x:
        return "qtymultiset"
    if "qty unit" in x:
        return "qtyunit"
    return None


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
sel = [r for r in rows if r["Attach Status"] == "Not Attached" and r["Bill Status"] == "Paid" and invonly(r) and grp(r["Reason"])]
for lc, pred in [("single", lambda r: r["Bill Item Count"] == "1"), ("multi", lambda r: r["Bill Item Count"] != "1")]:
    out, miss = [], 0
    for r in [x for x in sel if pred(x)]:
        ref = r["MD Reference(s)"].strip()
        bid = billid.get((r["Bill Number"], norm(ref))); pid = poid.get(norm(ref))
        if not bid or not pid:
            miss += 1; continue
        out.append({"bill_id": bid, "po_id": pid, "md_ref": ref, "bill_number": r["Bill Number"],
                    "po_number": r["PO Numbers"], "db_amount": r["Bill Amount"], "bill_amount": r["Bill Amount"],
                    "po_amount": r["PO Amount"], "n_lines": r["Bill Item Count"], "grp": grp(r["Reason"])})
    outf = os.path.join(INV, f"takebill_extra_{lc}.csv")
    with open(outf, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                           "db_amount", "bill_amount", "po_amount", "n_lines", "grp"])
        w.writeheader(); w.writerows(out)
    print(f"{lc}: {len(out)} (miss {miss}) by grp {dict(Counter(x['grp'] for x in out))} -> {outf}")
