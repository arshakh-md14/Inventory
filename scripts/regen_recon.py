import csv, glob, re, os
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
OUT = r"C:\Users\Jogesh Behera\Code file\Inventory\bill_po_reconciliation.csv"
mdpat = re.compile(r'^MD', re.I)
NOT_MATCHED = {"Draft", "Issued"}


def num(v):
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def norm(t):
    return re.sub(r'[^0-9A-Za-z]', '', t).upper()


def vnorm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def parse_refs(field):
    return [n for n in (norm(t) for t in field.split(',')) if n.startswith('MD')]


def close(a, b):
    return abs(a - b) <= max(5.0, 0.02 * max(abs(a), abs(b)))


# ---- Item master ----
item_cat = {}
for f in glob.glob(os.path.join(BASE, "Item_*", "Item*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            iid = row["Item ID"].strip()
            if not iid:
                continue
            t, s = row.get("Item Type", "").strip(), row.get("Status", "").strip()
            if t == "Inventory":
                item_cat[iid] = "Inventory (active)" if s == "Active" else "Inventory (inactive)"
            elif t == "Sales and Purchases":
                item_cat[iid] = "Sales & Purchase"
            elif t == "Sales":
                item_cat[iid] = "Sales"
            elif t == "Purchases":
                item_cat[iid] = "Purchase"
            else:
                item_cat[iid] = t or "Unknown"


def item_summary(pids):
    c = Counter(item_cat.get(p, "Unknown") for p in pids)
    if not c:
        return "No item link"
    order = ["Inventory (active)", "Sales & Purchase", "Inventory (inactive)", "Sales", "Purchase", "Unknown"]
    parts = [f"{k} x{c[k]}" for k in order if c.get(k)]
    parts += [f"{k} x{c[k]}" for k in c if k not in order]
    return "; ".join(parts)


# ---- POs: ref -> details ----
po_map = defaultdict(lambda: {"po_nums": set(), "qtys": [], "status": {},
                              "pids": [], "vendors": set(), "item_totals": [],
                              "po_total": {}})
for f in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = norm(row["CF.PO Number"].strip())
            if not ref.startswith("MD"):
                continue
            m = po_map[ref]
            pn = row["Purchase Order Number"]
            m["po_nums"].add(pn)
            m["status"][pn] = row["Purchase Order Status"]
            qv = num(row["QuantityOrdered"])
            if qv is not None:
                m["qtys"].append(qv)
            if row["Product ID"].strip():
                m["pids"].append(row["Product ID"].strip())
            if row["Vendor Name"].strip():
                m["vendors"].add(row["Vendor Name"].strip())
            it = num(row["Item Total"])
            if it is not None:
                m["item_totals"].append(it)
            tot = num(row["Total"])
            if tot is not None:
                m["po_total"][pn] = tot   # grand total, repeated per line

# ---- Bills: Sep 2025+ MD-ref, by Bill ID ----
bills = {}
for f in glob.glob(os.path.join(BASE, "Bill_*", "Bill*.csv")):
    with open(f, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = row["PurchaseOrder"].strip()
            if not (raw and mdpat.match(raw)) or row["Bill Date"] < "2025-09-01":
                continue
            bid = row["Bill ID"]
            b = bills.get(bid)
            if b is None:
                b = bills[bid] = {
                    "bill_num": row["Bill Number"], "date": row["Bill Date"],
                    "status": row["Bill Status"], "vendor": row["Vendor Name"],
                    "refs": parse_refs(raw), "qtys": [], "item_totals": [],
                    "grand_total": num(row["Total"]) or 0.0,
                }
            qv = num(row["Quantity"])
            if qv is not None:
                b["qtys"].append(qv)
            it = num(row["Item Total"])
            if it is not None:
                b["item_totals"].append(it)

# ref -> #bills (shared detection)
ref_bill_count = defaultdict(int)
for b in bills.values():
    for r in set(b["refs"]):
        ref_bill_count[r] += 1

# ---- attach results (bill_id -> (result, reason)) ----
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
attach = {}
if os.path.exists(RESULTS):
    for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
        attach[r["bill_id"]] = (r["result"], r.get("reason", ""))


def attach_status(bid):
    a = attach.get(bid)
    if not a:
        return "Not processed"
    res, reason = a
    return {"OK": "Attached", "CHECK": "Attached (needs check)",
            "FAIL": f"Failed: {reason}", "SKIP": f"Skipped: {reason}"}.get(res, res)


# ---- Reconcile + amount check ----
rows_out = []
for bid, b in bills.items():
    bqty = sorted(b["qtys"])
    refs = b["refs"]
    found = [r for r in refs if r in po_map]
    po_nums, pqty, statuses, pids, povendors, po_item_totals, po_totals = \
        set(), [], {}, [], set(), [], {}
    for r in found:
        m = po_map[r]
        po_nums |= m["po_nums"]
        pqty.extend(m["qtys"])
        statuses.update(m["status"])
        pids.extend(m["pids"])
        povendors |= m["vendors"]
        po_item_totals.extend(m["item_totals"])
        po_totals.update(m["po_total"])
    pqty = sorted(pqty)
    has_unmatched_po = any(s in NOT_MATCHED for s in statuses.values())
    if not found or not has_unmatched_po:
        continue

    if len(bqty) != len(pqty):
        status = "Item-count mismatch"
    elif bqty != pqty:
        status = "Qty mismatch"
    else:
        status = "Matched"

    bill_amt = b["grand_total"]
    po_amt = round(sum(po_totals.values()), 2)
    bill_sub = round(sum(b["item_totals"]), 2)
    po_sub = round(sum(po_item_totals), 2)
    if close(bill_amt, po_amt):
        agreement = "Amounts equal"
    elif close(bill_sub, po_sub):
        agreement = "Tax-only diff"
    else:
        agreement = "Rate/amount diff"

    # amount verdict (only meaningful for Qty mismatch)
    verdict = ""
    if status == "Qty mismatch":
        bt = sorted(b["item_totals"]); pt = sorted(po_item_totals)
        lines_match = len(bt) == len(pt) and all(close(x, y) for x, y in zip(bt, pt))
        total_match = close(bill_amt, po_amt)
        if lines_match and total_match:
            verdict = "Both match (likely true match)"
        elif total_match:
            verdict = "Total only"
        elif lines_match:
            verdict = "Line items only"
        else:
            verdict = "Neither"

    # vendor match
    bn = vnorm(b["vendor"])
    if not povendors:
        vmatch, povend = "No PO vendor", ""
    else:
        povend = "; ".join(sorted(povendors))
        vmatch = "Yes" if any(bn == vnorm(v) for v in povendors) else "No"

    rows_out.append({
        "Bill Number": b["bill_num"], "Bill Date": b["date"], "Status": status,
        "MD Reference(s)": "; ".join(refs), "PO Numbers": "; ".join(sorted(po_nums)),
        "PO Status(es)": "; ".join(f"{p}:{statuses[p]}" for p in sorted(statuses)),
        "Bill Item Count": len(bqty), "PO Item Count": len(pqty),
        "Bill Quantities": "; ".join(f"{x:g}" for x in bqty),
        "PO Quantities": "; ".join(f"{x:g}" for x in pqty),
        "Multi-PO Bill": "Yes" if len(refs) > 1 else "",
        "MD Ref Shared by Other Bills": "Yes" if any(ref_bill_count[r] > 1 for r in refs) else "",
        "Bill Status": b["status"], "Vendor": b["vendor"],
        "Item Type(s) (from PO)": item_summary(pids),
        "PO Vendor": povend, "Vendor Match": vmatch,
        "Bill Amount": f"{bill_amt:.2f}", "PO Amount": f"{po_amt:.2f}",
        "Amount Agreement": agreement,
        "Amount Verdict (Qty mismatch)": verdict,
        "Attach Status": attach_status(bid),
    })

order = {"Matched": 0, "Qty mismatch": 1, "Item-count mismatch": 2}
rows_out.sort(key=lambda r: (order[r["Status"]], r["Bill Date"]))
fields = ["Bill Number", "Bill Date", "Status", "MD Reference(s)", "PO Numbers",
          "PO Status(es)", "Bill Item Count", "PO Item Count", "Bill Quantities",
          "PO Quantities", "Multi-PO Bill", "MD Ref Shared by Other Bills",
          "Bill Status", "Vendor", "Item Type(s) (from PO)", "PO Vendor",
          "Vendor Match", "Bill Amount", "PO Amount", "Amount Agreement",
          "Amount Verdict (Qty mismatch)", "Attach Status"]
with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(rows_out)

print("rows:", len(rows_out))
print("\nAmount Agreement (Matched rows):")
for k, v in Counter(r["Amount Agreement"] for r in rows_out if r["Status"] == "Matched").most_common():
    print(f"  {k}: {v}")
print("\nAttach Status (all rows):")
for k, v in Counter(r["Attach Status"].split(":")[0] for r in rows_out).most_common():
    print(f"  {k}: {v}")
