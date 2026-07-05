"""Rebuild the reconciliation sheet, thoroughly and from the latest data:
  - Status column  -> "Matched" (bill attached to its PO, per the LATEST result logs) / "Not Matched"
  - Reason column  -> single consolidated reason for every Not Matched row (blank for Matched)
  - Amount Agreement + Amount Verdict (Qty mismatch) -> recomputed for ALL rows (line + total test)
  - the old separate "Attach Status" column is dropped (Status now conveys it)
Match-quality (item-count / qty mismatch) is derived from the count/quantity columns, so it no
longer depends on the (now-repurposed) Status column and stays correct on re-runs."""
import csv, os, re, glob
from collections import defaultdict, Counter

csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
RECON = os.path.join(INV, "bill_po_reconciliation.csv")
_norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s or '').upper()


def load(f):
    return list(csv.DictReader(open(f, encoding="utf-8-sig"))) if os.path.exists(f) else []


def num(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def qmset(s):
    out = []
    for t in re.split(r'[;,]', s or ""):
        t = t.strip()
        if t:
            try:
                out.append(round(float(t), 2))
            except ValueError:
                pass
    return sorted(out)


# ---- raw line data (reconciliation-time) for Amount Agreement / Verdict ----
bill_total, bill_sub = {}, {}
bill_lines = defaultdict(list)
for f in glob.glob(os.path.join(INV, "Raw Files", "Bill_*", "Bill*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        bn = r["Bill Number"]
        bill_lines[bn].append(round(num(r.get("Item Total")), 2))
        bill_total[bn] = round(num(r.get("Total")), 2)
        bill_sub[bn] = round(num(r.get("SubTotal")), 2)
po_lines = defaultdict(list)
po_sub = defaultdict(float)
_po_ids = defaultdict(set)
for f in glob.glob(os.path.join(INV, "Raw Files", "Purchase Order_*", "Purchase_Order*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        ref = _norm(r.get("CF.PO Number"))
        if ref.startswith("MD"):
            po_lines[ref].append(round(num(r.get("Item Total")), 2))
            po_sub[ref] += num(r.get("Item Total"))
            _po_ids[ref].add(r["Purchase Order ID"])
DUPREFS = {r for r, ids in _po_ids.items() if len(ids) > 1}


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
    short, long = (bl, pl) if len(bl) <= len(pl) else (pl, bl)
    return _contained(short, long)


def amount_agreement(bt, pt, bsub, psub):
    if close(bt, pt):
        return "Amounts equal"
    if psub and close(bsub, psub):
        return "Tax-only diff"
    return "Rate/amount diff"


def amount_verdict(bn, ref, bt, pt):
    lm = line_match(bill_lines.get(bn, []), po_lines.get(ref, []))
    tm = close(bt, pt)
    if lm and tm:
        return "Both match (likely true match)"
    if tm:
        return "Total only"
    if lm:
        return "Line items only"
    return "Neither"


# ---- attach results (latest wins) ----
res = defaultdict(list)
for f in ["attach_results.csv", "attach_results_multiline.csv", "attach_results_qtymatch.csv",
          "attach_results_qtymatch_multi.csv", "attach_results_mixed.csv",
          "attach_results_amountdiff.csv", "attach_results_amountdiff_multi.csv",
          "attach_results_amountdiff_qtymm.csv",
          "attach_results_draft_inv.csv", "attach_results_draft_inv_multi.csv",
          "attach_results_takebill_amountdiff_single.csv", "attach_results_takebill_amountdiff_multi.csv",
          "attach_results_takebill_qtymm_single.csv", "attach_results_takebill_qtymm_multi.csv",
          "attach_results_takebill_extra_single.csv", "attach_results_takebill_extra_multi.csv",
          "attach_results_charge_itemcount.csv", "attach_results_itemcount_subset.csv", "attach_results_vendorfix.csv",
          "attach_results_vendorfixed_tb_single.csv", "attach_results_vendorfixed_tb_multi.csv"]:
    for r in load(os.path.join(INV, f)):
        res[(r["bill_number"], r["po_number"])].append((r["result"], r.get("reason", "")))

MULTI_QTYMM = {(r["bill_number"], r["po_number"])
               for r in load(os.path.join(INV, "amountdiff_multi_qtymismatch.csv"))}
_IC_MAP = {"genuine extra product on bill": "item-count: extra product(s) on bill (not charges)",
           "charge-explained (bill extra = charges)": "item-count: freight/transport charge on bill (attachable)",
           "PO has extra lines (bill covers subset)": "item-count: PO has extra lines (bill subset)",
           "lines match by amount (false mismatch)": "item-count: lines match by amount (recheck)",
           "other": "item-count: mixed extra product + charge (recheck)"}
ITEMCOUNT_CLASS = {(r["Bill Number"], r["PO Numbers"]): _IC_MAP.get(r["class"], r["class"])
                   for r in load(os.path.join(INV, "itemcount_classify.csv"))}
# multi-PO bills are attached by bill number (they span several POs, so no single PO-number key)
MULTIPO_ATTACHED = {r["bill_number"] for r in load(os.path.join(INV, "attach_results_multipo.csv"))
                    if r["result"] == "OK"}


def _mp_norm(x):
    xl = x.lower()
    if "already linked" in xl:
        return None                                   # actually attached
    if "vendor mismatch" in xl:
        return "multi-PO: vendor mismatch"
    if "gap" in xl and "> 10" in xl:
        return "multi-PO: gap > Rs.10 (bill != sum of POs)"
    if "already billed" in xl:
        return "multi-PO: a PO already billed"
    if "partially billed" in xl:
        return "multi-PO: a PO partially billed"
    if "not db-delivered" in xl or "not open+grn" in xl:
        return "multi-PO: a PO not delivered"
    if "fetch fail" in xl:
        return "multi-PO: PO fetch error"
    if "unable to delete" in xl:
        return "multi-PO: line-locked"
    return "multi-PO: " + x[:40]


MULTIPO_REASON = {}
for r in load(os.path.join(INV, "attach_results_multipo.csv")):
    if r["result"] in ("SKIP", "FAIL", "CHECK"):
        mp = _mp_norm(r.get("reason", ""))
        if mp:
            MULTIPO_REASON[r["bill_number"]] = mp


def clean(reason):
    # keep the EXACT reason; only strip our own wrapper prefixes so the true Zoho/skip message shows
    r = reason.replace("MANUAL: ", "").strip()
    r = re.sub(r'^(bill update|bill PUT|attach PUT|PO sync PUT|PO adj PUT|PO scale PUT|repoint PUT|issue|GRN)\s*\d*\s*:\s*', '', r).strip()
    return r


def logged_status(key):
    entries = res.get(key)
    if not entries:
        return None
    if "OK" in [e[0] for e in entries]:
        return "Attached"
    for rr, rs in entries:
        if rr == "SKIP" and "already linked" in rs.lower():
            return "Attached"
    for res_kind in ("FAIL", "CHECK", "SKIP"):        # most-recent entry of each kind wins
        for rr, rs in reversed(entries):
            if rr == res_kind:
                return f"Not attached — {clean(rs) or res_kind.lower()}"
    return None


def norm_reason(x):
    """collapse raw log strings + derived labels into one clean canonical reason set."""
    xl = x.lower()
    if xl.startswith("item-count"):
        return x                                   # already-clean item-count sub-reasons
    if "duplicate po" in xl:
        return "duplicate PO (2+ Zoho POs for same ref)"
    if "multi-po" in xl:
        return "multi-PO (separate pass)"
    if "draft" in xl:
        return "draft PO"
    if "vendor mismatch" in xl:
        return "vendor mismatch"
    if "mixed/other item type" in xl:
        return "mixed/other item type"
    if "qty unit mismatch" in xl:
        return "qty unit mismatch (manual)"
    if "qty multiset" in xl:
        return "qty-multiset mismatch (multi-line)"
    if "qty mismatch (amounts differ)" in xl:
        return "qty mismatch (amounts differ)"
    if "qty value-match" in xl:
        return "qty value-match (recheck)"
    if "already billed" in xl or "po billed" in xl or "po status billed" in xl:
        return "PO billed elsewhere"
    if "vs db" in xl and "diff >= 10" in xl:
        return "amount >= Rs.10 vs DB (manual)"
    if "amount diff" in xl:
        return "amount diff > Rs.10 (genuine bill != PO)"
    if "igst" in xl:
        return "IGST / intrastate tax"
    if "hsn" in xl:
        return "bad HSN"
    if "unable to delete" in xl:
        return "line-locked (bill line used elsewhere)"
    if "exceeds the remaining" in xl or "quantity recorded cannot be more" in xl:
        return "partial-receive / GRN qty"
    if "branch cannot be changed" in xl:
        return "branch locked"
    if "payment or credits" in xl or "payment > total" in xl:
        return "payment > total"
    if "tax mismatch" in xl or "tax-only" in xl:
        return "tax mismatch"
    if "pending" in xl:
        return "pending (SAP multi-line paused)"
    if "no charge line found" in xl:
        return "item-count: charge not detected (recheck)"
    if "off by" in xl or ("po total" in xl and "vs bill" in xl):
        return "amount off after charge-add (recheck)"
    if "po already == db" in xl or "fetch" in xl or "verify" in xl:
        return "attach edge (recheck)"
    return x


def icount(x):
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return -1


def derived_reason(r):
    if r["Multi-PO Bill"] == "Yes":
        return "multi-PO (separate pass)"
    if icount(r["Bill Item Count"]) != icount(r["PO Item Count"]):
        return "item-count mismatch"
    if "Draft" in r["PO Status(es)"]:
        return "draft PO"
    if r["Vendor Match"] == "No":
        return "vendor mismatch"
    toks = set(re.sub(r' x\d+', '', t).strip() for t in r["Item Type(s) (from PO)"].split(";") if t.strip())
    if not (toks <= {"Inventory (active)", "Inventory (inactive)"} or toks <= {"Sales & Purchase"}):
        return "mixed/other item type"
    if r["Amount Agreement"] == "Tax-only diff":
        return "tax mismatch"
    amt = abs(num(r["Bill Amount"]) - num(r["PO Amount"]))
    qty_mismatch = qmset(r["Bill Quantities"]) != qmset(r["PO Quantities"])
    if qty_mismatch:
        if not r["Amount Verdict (Qty mismatch)"].startswith("Both match"):
            return "qty mismatch (amounts differ)"
        if amt > 10:
            return f"amount diff {round(amt, 2)} > 10 (runtime)"
        return "qty value-match (pending)"
    if amt > 10:
        return "amount diff > 10"
    return "pending (not yet run / SAP multi-line paused)"


rows = load(RECON)
statusdist, attachdist, reasondist = Counter(), Counter(), Counter()
for r in rows:
    bn = r["Bill Number"]
    ref = _norm(r["MD Reference(s)"].split(";")[0].strip())
    bt, pt = num(r["Bill Amount"]), num(r["PO Amount"])
    # (1) amounts for ALL rows
    r["Amount Agreement"] = amount_agreement(bt, pt, bill_sub.get(bn, 0.0), po_sub.get(ref, 0.0))
    r["Amount Verdict (Qty mismatch)"] = amount_verdict(bn, ref, bt, pt)
    # (2) status + reason (latest logs, then overrides)
    key = (bn, r["PO Numbers"])
    st = logged_status(key)
    if st is None:
        st = "Not attached — " + derived_reason(r)
    if key in MULTI_QTYMM and st != "Attached":
        st = "Not attached — qty unit mismatch (bill/PO qty differ; amounts match) — manual"
    if st != "Attached" and r["Multi-PO Bill"] != "Yes" and _norm(r["MD Reference(s)"].strip()) in DUPREFS:
        st = "Not attached — duplicate PO (2+ Zoho POs for same ref)"
    if st != "Attached" and key in ITEMCOUNT_CLASS and "item-count" in st:
        st = "Not attached — " + ITEMCOUNT_CLASS[key]
    if r["Multi-PO Bill"] == "Yes" and r["Bill Number"] in MULTIPO_ATTACHED:
        st = "Attached"
    # Status = reconciliation MATCH (MD ref [always true here] + item-count + per-item qty),
    # independent of whether we attached it.
    im = icount(r["Bill Item Count"]) == icount(r["PO Item Count"])
    qm = qmset(r["Bill Quantities"]) == qmset(r["PO Quantities"])
    r["Status"] = "Matched" if (im and qm) else "Not Matched"
    statusdist[r["Status"]] += 1
    # Attach Status = the ones we successfully attached in Zoho; Reason explains the rest.
    if st == "Attached":
        r["Attach Status"] = "Attached"
        r["Reason"] = ""
        attachdist["Attached"] += 1
    else:
        # EXACT reason: the raw latest logged/derived reason (no bucketing)
        reason = st.split("—", 1)[1].strip() if "—" in st else st
        # multi-PO: use the specific per-bill error from the multi-PO run
        if r["Multi-PO Bill"] == "Yes" and r["Bill Number"] in MULTIPO_REASON:
            reason = MULTIPO_REASON[r["Bill Number"]]
        r["Attach Status"] = "Not Attached"
        r["Reason"] = reason
        attachdist["Not Attached"] += 1
        reasondist[reason] += 1

# output schema: keep "Attach Status" (Attached/Not Attached) and "Reason" as the last two columns
base = [c for c in rows[0].keys() if c not in ("Attach Status", "Reason")]
fields = base + ["Attach Status", "Reason"]
with open(RECON, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
    w.writeheader(); w.writerows(rows)

print("rows:", len(rows), "| Status (match):", dict(statusdist), "| Attach Status:", dict(attachdist))
print("Amount Agreement:", dict(Counter(r["Amount Agreement"] for r in rows)))
print("Amount Verdict:", dict(Counter(r["Amount Verdict (Qty mismatch)"] for r in rows)))
print("Not-Matched reasons:")
for k, v in reasondist.most_common():
    print(f"  {v:6}  {k}")
