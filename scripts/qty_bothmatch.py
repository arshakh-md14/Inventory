import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
BASE = os.path.join(INV, "Raw Files")
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


po = {}
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if not ref.startswith("MD"):
            continue
        d = po.setdefault(ref, {"recd": 0.0, "pids": []})
        d["recd"] += num(row["QuantityReceived"])
        if row["Product ID"].strip():
            d["pids"].append(row["Product ID"].strip())
istatus = {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        iid = row["Item ID"].strip()
        if iid:
            istatus[iid] = row.get("Status", "").strip()


def item_class(s):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in s.split(';') if t.strip())
    inv = {"Inventory (active)", "Inventory (inactive)"}
    if toks <= inv: return "inventory"
    if toks <= {"Sales & Purchase"}: return "sap"
    if toks <= inv | {"Sales & Purchase"}: return "mixed"
    return "other"


rows = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
qm = [r for r in rows if r["Status"] == "Qty mismatch"]
both = [r for r in qm if r["Amount Verdict (Qty mismatch)"].startswith("Both match")]
print(f"Qty mismatch rows: {len(qm)} | of which 'Both match': {len(both)}")

leaves = defaultdict(lambda: {"n": 0, "calls": 0})
skipped = defaultdict(int)
for r in both:
    if r["Multi-PO Bill"] == "Yes":
        skipped["multi-PO"] += 1; continue
    ref = r["MD Reference(s)"].strip()
    p = po.get(ref, {"recd": 0.0, "pids": []})
    if "Draft" in r["PO Status(es)"]:
        skipped["draft"] += 1; continue
    if p["recd"] <= 0:
        skipped["not received"] += 1; continue
    cls = item_class(r["Item Type(s) (from PO)"])
    if cls == "other":
        skipped["other item type"] += 1; continue
    if r["Vendor Match"] == "No":
        skipped["vendor mismatch"] += 1; continue
    n_inactive = sum(1 for x in set(p["pids"]) if istatus.get(x) == "Inactive")
    lk = "multi" if int(r["Bill Item Count"]) > 1 else "single"
    lf = leaves[f"{cls}/{lk}"]
    lf["n"] += 1
    lf["calls"] += 4 + 2 * n_inactive

print("skipped (not clean):", dict(skipped))
print("\nADDITIONAL clean attachable (Qty mismatch + Both match):")
tn = tc = 0
for k in sorted(leaves):
    print(f"  {k:18} {leaves[k]['n']:5}  {leaves[k]['calls']:>7}")
    tn += leaves[k]["n"]; tc += leaves[k]["calls"]
print(f"\n  TOTAL additional: {tn} bills, ~{tc} API calls")
print(f"\n  New auto-attachable total: 10,597 + {tn} = {10597 + tn} bills")
print(f"  New API-call total: ~49,302 + {tc} = ~{49302 + tc}")
