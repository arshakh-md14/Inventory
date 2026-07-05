"""Single-line Qty-mismatch / Both-match (value-match) candidates, clean, with Zoho IDs."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
OUT = os.path.join(INV, "attach_candidates_qtymatch.csv")
mdpat = re.compile(r'^MD', re.I)
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


# PO export: ref -> po_id, status, received
po = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if ref.startswith("MD"):
            d = po.setdefault(ref, {"po_id": row["Purchase Order ID"], "recd": 0.0})
            d["recd"] += num(row["QuantityReceived"])
# bill export: (bill_number, ref) -> bill_id (Sep 2025+ MD ref)
bmap = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
            continue
        for t in raw.split(","):
            n = norm(t)
            if n.startswith("MD"):
                bmap.setdefault((row["Bill Number"], n), row["Bill ID"])

recon = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
rows, cls = [], defaultdict(int)
for r in recon:
    if r["Status"] != "Qty mismatch" or not r["Amount Verdict (Qty mismatch)"].startswith("Both match"):
        continue
    if r["Multi-PO Bill"] == "Yes" or r["Bill Item Count"] != "1":
        continue
    if "Draft" in r["PO Status(es)"] or r["Vendor Match"] == "No":
        continue
    ref = r["MD Reference(s)"].strip()
    p = po.get(ref)
    if not p or p["recd"] <= 0:
        continue
    # item type inventory/sap only
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    if toks <= {"Inventory (active)", "Inventory (inactive)"}:
        item_class = "inventory"
    elif toks <= {"Sales & Purchase"}:
        item_class = "sales_purchase"
    else:
        continue
    bid = bmap.get((r["Bill Number"], ref))
    if not bid:
        continue
    cls[item_class] += 1
    rows.append({"bill_id": bid, "po_id": p["po_id"], "md_ref": ref, "bill_number": r["Bill Number"],
                 "po_number": r["PO Numbers"], "bill_vendor": r["Vendor"], "po_vendor": r["PO Vendor"],
                 "item_class": item_class})

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "md_ref", "bill_number", "po_number",
                                       "bill_vendor", "po_vendor", "item_class"])
    w.writeheader(); w.writerows(rows)
print("single-line qty-both-match clean candidates:", len(rows), "->", OUT)
print("  by class:", dict(cls))
