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


# enrich: ref -> received?, product ids
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

rows = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
print("sheet rows:", len(rows))


def item_class(s):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in s.split(';') if t.strip())
    inv = {"Inventory (active)", "Inventory (inactive)"}
    if toks <= inv:
        return "inventory"
    if toks <= {"Sales & Purchase"}:
        return "sap"
    if toks <= inv | {"Sales & Purchase"}:
        return "mixed"
    return "other"


C = defaultdict(int)
leaves = defaultdict(lambda: {"n": 0, "calls": 0})
for r in rows:
    if r["Multi-PO Bill"] == "Yes":
        C["multi_po"] += 1
        continue
    if r["Status"] != "Matched":
        C["single_not_matched(" + r["Status"] + ")"] += 1
        continue
    C["single_matched"] += 1
    ref = r["MD Reference(s)"].strip()
    p = po.get(ref, {"recd": 0.0, "pids": []})
    draft = "Draft" in r["PO Status(es)"]
    received = p["recd"] > 0
    cls = item_class(r["Item Type(s) (from PO)"])
    n_inactive = sum(1 for x in set(p["pids"]) if istatus.get(x) == "Inactive")
    n_lines = int(r["Bill Item Count"])
    amt_issue = abs(num(r["Bill Amount"]) - num(r["PO Amount"])) > 10.0
    tax_issue = r["Amount Agreement"] == "Tax-only diff"
    vendor_issue = r["Vendor Match"] == "No"

    if draft:
        leaves["DRAFT (skip)"]["n"] += 1
    elif not received:
        leaves["issued/NOT-received (skip)"]["n"] += 1
    elif cls == "other":
        leaves["issued+received/other-itemtype (skip)"]["n"] += 1
    else:
        lk = "multi" if n_lines > 1 else "single"
        if amt_issue or tax_issue or vendor_issue:
            tags = []
            if vendor_issue: tags.append("vendor")
            if tax_issue: tags.append("tax")
            if amt_issue: tags.append("amt>10")
            leaves[f"issued+received/{cls}/{lk}/ISSUE({'+'.join(tags)})"]["n"] += 1
        else:
            lf = leaves[f"issued+received/{cls}/{lk}/CLEAN"]
            lf["n"] += 1
            lf["calls"] += 4 + 2 * n_inactive

print("L1 multi-PO rows:", C["multi_po"])
for k in sorted(C):
    if k.startswith("single_not_matched"):
        print("   ", k, C[k])
print("L1 single-PO matched rows:", C["single_matched"])
print()
clean_n = clean_calls = 0
for leaf in sorted(leaves):
    d = leaves[leaf]
    show = d["calls"] if "CLEAN" in leaf else "-"
    print(f"  {leaf:48} {d['n']:6}  {show:>8}")
    if "CLEAN" in leaf:
        clean_n += d["n"]; clean_calls += d["calls"]
print()
print(f"CLEAN auto-attachable (within 17,712): {clean_n} bills, ~{clean_calls} API calls")
