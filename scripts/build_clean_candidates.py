"""Pre-filter single-line candidates to the CLEAN attachable set, excluding every
skip category up front: vendor mismatch, draft PO, not-received, other item type,
amount>ROUNDOFF, tax mismatch."""
import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
OUT = os.path.join(INV, "attach_candidates_clean.csv")
ROUNDOFF = 10.0
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


itype, istat = {}, {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        i = row["Item ID"].strip()
        if i:
            itype[i] = row.get("Item Type", "").strip(); istat[i] = row.get("Status", "").strip()

po = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        r = norm(row["CF.PO Number"])
        if not r.startswith("MD"):
            continue
        d = po.setdefault(r, {"status": "", "recd": 0.0, "pids": [], "total": 0.0, "tax": []})
        d["status"] = row["Purchase Order Status"]; d["recd"] += num(row["QuantityReceived"])
        d["total"] = num(row["Total"])
        if row["Product ID"].strip():
            d["pids"].append(row["Product ID"].strip())
        d["tax"].append(num(row.get("Item Tax %")))

bill = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        bid = row["Bill ID"]
        b = bill.get(bid)
        if b is None:
            b = bill[bid] = {"total": num(row["Total"]), "tax": []}
        tp = num(row.get("Tax Percentage")); cg = num(row.get("CGST Rate %")); sg = num(row.get("SGST Rate %")); ig = num(row.get("IGST Rate %"))
        b["tax"].append(tp if tp > 0 else (cg + sg if (cg + sg) > 0 else ig))

cands = list(csv.DictReader(open(os.path.join(INV, "attach_candidates.csv"), encoding="utf-8-sig")))
kept, drop = [], defaultdict(int)
for c in cands:
    ref = c["md_ref"]; p = po.get(ref); b = bill.get(c["bill_id"])
    if not p or not b:
        drop["no export"] += 1; continue
    if c["bill_vendor"].strip() != c["po_vendor"].strip():
        drop["vendor mismatch"] += 1; continue
    if p["status"] == "Draft":
        drop["draft"] += 1; continue
    if p["recd"] <= 0:
        drop["not received"] += 1; continue
    types = {itype.get(x) for x in p["pids"]}
    if not types <= {"Inventory", "Sales and Purchases"}:
        drop["other item type"] += 1; continue
    if abs(b["total"] - p["total"]) > ROUNDOFF:
        drop["amount>10"] += 1; continue
    if sorted(round(x, 1) for x in b["tax"]) != sorted(round(x, 1) for x in p["tax"]):
        drop["tax mismatch"] += 1; continue
    kept.append(c)

with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(cands[0].keys()))
    w.writeheader(); w.writerows(kept)
print("input single-line candidates:", len(cands))
print("CLEAN kept:", len(kept), "->", OUT)
print("dropped:", dict(drop))
