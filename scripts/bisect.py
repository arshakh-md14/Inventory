import csv, glob, re, os
from collections import defaultdict

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
mdpat = re.compile(r'^MD', re.I)
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()
vnorm = lambda s: re.sub(r'[^a-z0-9]', '', (s or '').lower())


def num(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


# ---- Item master ----
itype, istatus = {}, {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        iid = row["Item ID"].strip()
        if iid:
            itype[iid] = row.get("Item Type", "").strip()
            istatus[iid] = row.get("Status", "").strip()

# ---- PO by ref ----
po = defaultdict(lambda: {"po_ids": set(), "status": "", "qty_ord": [], "qty_recd": 0.0,
                          "qty_billed": 0.0, "pids": [], "total": 0.0, "adj": 0.0,
                          "vendor": "", "tax": []})
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(row["CF.PO Number"])
        if not ref.startswith("MD"):
            continue
        m = po[ref]
        m["po_ids"].add(row["Purchase Order ID"])
        m["status"] = row["Purchase Order Status"]
        m["vendor"] = row["Vendor Name"]
        m["total"] = num(row["Total"]); m["adj"] = num(row["Adjustment"])
        m["qty_ord"].append(num(row["QuantityOrdered"]))
        m["qty_recd"] += num(row["QuantityReceived"])
        m["qty_billed"] += num(row["QuantityBilled"])
        if row["Product ID"].strip():
            m["pids"].append(row["Product ID"].strip())
        m["tax"].append(num(row.get("Item Tax %")))

# ---- Bills by Bill ID (Sep 2025+, MD ref) ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    for row in csv.DictReader(open(f, encoding="utf-8")):
        raw = row["PurchaseOrder"].strip()
        if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
            continue
        bid = row["Bill ID"]
        b = bills.get(bid)
        if b is None:
            b = bills[bid] = {"refs": [n for n in (norm(t) for t in raw.split(",")) if n.startswith("MD")],
                              "qtys": [], "vendor": row["Vendor Name"], "total": num(row["Total"]), "tax": []}
        b["qtys"].append(num(row["Quantity"]))
        tp = num(row.get("Tax Percentage")); cg = num(row.get("CGST Rate %")); sg = num(row.get("SGST Rate %")); ig = num(row.get("IGST Rate %"))
        b["tax"].append(tp if tp > 0 else (cg + sg if (cg + sg) > 0 else ig))

# ---- classify ----
C = defaultdict(int)          # counts
CALLS = defaultdict(int)      # api calls
multi_po = 0

def itemclass(pids):
    ts = {itype.get(p) for p in pids}
    if ts <= {"Inventory"}:
        return "inventory"
    if ts <= {"Sales and Purchases"}:
        return "sap"
    if ts <= {"Inventory", "Sales and Purchases"}:
        return "mixed"
    return "other"

leaves = defaultdict(lambda: {"n": 0, "calls": 0})
for bid, b in bills.items():
    pids_all = set()
    poids = set()
    for r in b["refs"]:
        if r in po:
            poids |= po[r]["po_ids"]
    # L1: multi vs single PO
    if len(b["refs"]) != 1 or len(poids) != 1:
        multi_po += 1
        continue
    ref = b["refs"][0]
    m = po.get(ref)
    if not m:
        continue
    # not-yet-billed only (matches reconciliation universe)
    if m["qty_billed"] > 0:
        continue
    # matched?
    matched = sorted(b["qtys"]) == sorted(m["qty_ord"])
    if not matched:
        C["single_po_NOT_matched"] += 1
        continue
    C["single_po_matched"] += 1
    # L2: PO status / received
    draft = (m["status"] == "Draft")
    received = m["qty_recd"] > 0
    n_lines = len(b["qtys"])
    cls = itemclass(m["pids"])
    n_inactive = sum(1 for p in set(m["pids"]) if istatus.get(p) == "Inactive")
    calls = 4 + 2 * n_inactive
    # issues
    vendor_mm = vnorm(b["vendor"]) != vnorm(m["vendor"])
    tax_mm = sorted(round(x, 1) for x in b["tax"]) != sorted(round(x, 1) for x in m["tax"])
    amt_mm = abs(b["total"] - m["total"]) > 10.0

    if draft:
        leaf = "single/DRAFT(skip)"
    elif not received:
        leaf = "single/issued/NOT-received(skip)"
    else:
        # issued + received -> by item class -> issue vs clean
        if cls == "other":
            leaf = "single/issued/received/other-itemtype(skip)"
        else:
            issue = vendor_mm or tax_mm or amt_mm
            lk = "MULTI-line" if n_lines > 1 else "single-line"
            if issue:
                reasons = []
                if vendor_mm: reasons.append("vendor")
                if tax_mm: reasons.append("tax")
                if amt_mm: reasons.append("amt>10")
                leaf = f"single/issued/received/{cls}/{lk}/ISSUE({'+'.join(reasons)})"
            else:
                leaf = f"single/issued/received/{cls}/{lk}/CLEAN"
                leaves[leaf]["calls"] += calls
    leaves[leaf]["n"] += 1
    if "CLEAN" not in leaf:
        leaves[leaf]["calls"] += 0

# ---- report ----
print("L1  multi-PO bills:", multi_po)
print("L1  single-PO matched:", C["single_po_matched"], "| single-PO NOT-matched(qty/count):", C["single_po_NOT_matched"])
print()
print("Single-PO matched breakdown (leaf : records : api_calls):")
tot_clean = tot_calls = 0
for leaf in sorted(leaves):
    d = leaves[leaf]
    print(f"  {leaf:55} {d['n']:6}  {d['calls'] if 'CLEAN' in leaf else '-':>8}")
    if "CLEAN" in leaf:
        tot_clean += d["n"]; tot_calls += d["calls"]
print()
print(f"TOTAL clean attachable: {tot_clean} bills, ~{tot_calls} API calls")
