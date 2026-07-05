"""Verify a line+total Amount-Verdict / Amount-Agreement logic against the existing
filled values before applying it to all rows. READ-ONLY (no sheet write)."""
import csv, glob, re, os
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()


def num(v):
    try:
        return float(str(v).replace(",", "") or 0)
    except (ValueError, TypeError):
        return 0.0


# bill lines by bill number
bill_lines = defaultdict(list); bill_total = {}; bill_sub = {}
for f in glob.glob(os.path.join(INV, "Raw Files", "Bill_*", "Bill*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        bn = r["Bill Number"]
        bill_lines[bn].append(round(num(r.get("Item Total")), 2))
        bill_total[bn] = round(num(r.get("Total")), 2)
        bill_sub[bn] = round(num(r.get("SubTotal")), 2)
# PO lines by normalized ref
po_lines = defaultdict(list); po_sub = defaultdict(float)
for f in glob.glob(os.path.join(INV, "Raw Files", "Purchase Order_*", "Purchase_Order*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        ref = norm(r.get("CF.PO Number"))
        if ref.startswith("MD"):
            po_lines[ref].append(round(num(r.get("Item Total")), 2))
            po_sub[ref] += num(r.get("Item Total"))


def close(a, b, tol=1.0, pct=0.005):
    return abs(a - b) <= max(tol, pct * max(abs(a), abs(b)))


def _contained(short, long):
    pool = list(long)
    for x in short:
        hit = next((y for y in pool if close(x, y, 1.0, 0.01)), None)
        if hit is None:
            return False
        pool.remove(hit)
    return True


def line_match(bl, pl):
    if not bl or not pl:
        return False
    # tolerate extra (e.g. charge) lines on either side: the shorter set is contained in the longer
    short, long = (bl, pl) if len(bl) <= len(pl) else (pl, bl)
    return _contained(short, long)


def agreement(bt, pt, bsub, psub):
    if close(bt, pt):
        return "Amounts equal"
    if close(bsub, psub):
        return "Tax-only diff"
    return "Rate/amount diff"


def verdict(bn, ref, bt, pt):
    bl = bill_lines.get(bn, []); pl = po_lines.get(ref, [])
    lm = line_match(bl, pl); tm = close(bt, pt)
    if lm and tm:
        return "Both match (likely true match)"
    if tm:
        return "Total only"
    if lm:
        return "Line items only"
    return "Neither"


rows = list(csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig")))
agree_cmp = Counter(); verdict_cmp = Counter()
for r in rows:
    bn = r["Bill Number"]; ref = norm(r["MD Reference(s)"].split(";")[0].strip())
    bt = num(r["Bill Amount"]); pt = num(r["PO Amount"])   # sheet totals (reconciliation-time)
    my_agree = agreement(bt, pt, bill_sub.get(bn, 0), po_sub.get(ref, 0))
    agree_cmp[(r["Amount Agreement"], my_agree)] += 1
    if r["Amount Verdict (Qty mismatch)"]:   # only compare where existing is filled
        my_v = verdict(bn, ref, bt, pt)
        verdict_cmp[(r["Amount Verdict (Qty mismatch)"], my_v)] += 1

print("AMOUNT AGREEMENT (existing -> mine):  [match = same]")
same = sum(v for (a, b), v in agree_cmp.items() if a == b); tot = sum(agree_cmp.values())
print(f"  agreement match rate: {same}/{tot} = {same/tot*100:.1f}%")
for (a, b), v in sorted(agree_cmp.items(), key=lambda x: -x[1])[:10]:
    print(f"    {v:6}  existing='{a}'  mine='{b}'")
print("\nAMOUNT VERDICT (existing -> mine), on currently-filled rows:")
same = sum(v for (a, b), v in verdict_cmp.items() if a == b); tot = sum(verdict_cmp.values())
print(f"  verdict match rate: {same}/{tot} = {same/tot*100:.1f}%")
for (a, b), v in sorted(verdict_cmp.items(), key=lambda x: -x[1])[:12]:
    print(f"    {v:6}  existing='{a}'  mine='{b}'")
