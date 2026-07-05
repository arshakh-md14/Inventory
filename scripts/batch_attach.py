"""
Batch-attach matched single-line bills to their POs in Zoho Books (Haut Luxe).

Reads attach_candidates.csv, processes up to LIMIT not-yet-done candidates,
appends one result row per bill to attach_results.csv (resume-safe).

Per bill: validate -> (reactivate item if inactive) -> set PO rate+adjustment to
match bill total -> PUT bill linking PO line (+receive) and dropping old line ->
(re-deactivate item) -> verify. Paid-bill total is preserved.
"""
import json, http.client, urllib.parse, time, os, csv, glob

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
CAND = r"C:\Users\Jogesh Behera\Code file\Inventory\qtymatch_pending_single.csv"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results_qtymatch.csv"

DRY_RUN = False
REACTIVATE_INACTIVE = True
ALLOW_QTY_MISMATCH = True   # qty-both-match set: bill adopts PO qty; amount guard (<=ROUNDOFF) is the gate
LIMIT = 100
SLEEP = 1.0               # seconds between bills (rate limit)
ROUNDOFF_MAX = 10.0        # max bill-vs-PO gap absorbed into PO round-off; larger -> MANUAL
ONLY_BILL = None


class RateLimited(Exception):
    pass

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_token = None
_token_ts = 0


def token(force=False):
    global _token, _token_ts
    if _token and not force and (time.time() - _token_ts) < 2400:   # refresh every 40 min
        return _token
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC,
                                "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _token = json.loads(c.getresponse().read()).get("access_token")
    _token_ts = time.time()
    if not _token:
        raise RuntimeError("token refresh failed")
    return _token


def call(method, path, body=None, _retry=True, _net_retries=5):
    sep = "&" if "?" in path else "?"
    full = f"/books/v3{path}{sep}organization_id={ORG}"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    try:
        c = http.client.HTTPSConnection("www.zohoapis.in", timeout=60)
        if body is not None:
            h["content-type"] = "application/json"
            c.request(method, full, json.dumps(body), h)
        else:
            c.request(method, full, headers=h)
        r = c.getresponse()
        status = r.status
        data = json.loads(r.read().decode())
    except (OSError, http.client.HTTPException):
        if _net_retries > 0:
            time.sleep(3 * (6 - _net_retries))   # progressive backoff: 3,6,9,12,15s (~45s window)
            return call(method, path, body, _retry, _net_retries - 1)
        raise
    if status == 401 and _retry:
        token(force=True)
        return call(method, path, body, _retry=False)
    # transient server hiccup: generic "requested action could not be completed" -> retry w/ backoff
    msg = str(data.get("message", "")).lower()
    if status == 400 and "requested action could not be completed" in msg and _net_retries > 0:
        time.sleep(3 * (6 - _net_retries))
        return call(method, path, body, _retry, _net_retries - 1)
    # Zoho rate limit: code 429 (per-minute) or message-based daily cap
    code = data.get("code")
    if status == 429 or code in (1001,) or "too many requests" in str(data.get("message", "")).lower():
        raise RateLimited(str(data.get("message", "rate limited")))
    return status, data


def money_eq(a, b):
    return abs(float(a) - float(b)) < 0.005


# Item status from the Item CSV (avoids a GET /items per case). Status: Active/Inactive.
ITEM_STATUS = {}
for _f in glob.glob(r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files\Item_*\Item*.csv"):
    for _row in csv.DictReader(open(_f, encoding="utf-8")):
        _i = _row["Item ID"].strip()
        if _i:
            ITEM_STATUS[_i] = _row.get("Status", "").strip()


def attach_one(bill_id, po_id):
    """Returns (result, reason, bill_total, po_total). result in OK/SKIP/FAIL/CHECK."""
    _, bd = call("GET", f"/bills/{bill_id}")
    bill = bd.get("bill")
    if not bill:
        return "FAIL", f"bill fetch: {str(bd)[:120]}", "", ""
    _, pd = call("GET", f"/purchaseorders/{po_id}")
    po = pd.get("purchaseorder")
    if not po:
        return "FAIL", f"po fetch: {str(pd)[:120]}", "", ""

    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bill["total"], po["total"]
    if len(bill["line_items"]) != 1 or len(po["line_items"]) != 1:
        return "SKIP", "not single-line", bill["total"], po["total"]
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bill["total"], po["total"]
    if po.get("status") != "open":
        return "SKIP", f"PO status {po.get('status')}", bill["total"], po["total"]
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bill["total"], po["total"]
    b_li, p_li = bill["line_items"][0], po["line_items"][0]
    if float(p_li.get("quantity_billed") or 0) > 0:   # already partly mapped -> would exceed remaining
        return "SKIP", "PO line partially billed", bill["total"], po["total"]
    if not ALLOW_QTY_MISMATCH and round(float(b_li["quantity"]), 2) != round(float(p_li["quantity"]), 2):
        return "SKIP", "qty mismatch", bill["total"], po["total"]
    # tax-rate mismatch -> MANUAL intervention (never auto-process)
    bt_pct, pt_pct = b_li.get("tax_percentage"), p_li.get("tax_percentage")
    if bt_pct is not None and pt_pct is not None and round(float(bt_pct), 2) != round(float(pt_pct), 2):
        return "SKIP", f"MANUAL: tax mismatch bill {bt_pct}% / po {pt_pct}%", bill["total"], po["total"]
    # PO is kept as-is; only a round-off adjustment absorbs the gap. Gap beyond the
    # round-off cap is a genuine rate difference -> MANUAL.
    bt_total, pt_total = float(bill["total"]), float(po["total"])
    diff = round(bt_total - pt_total, 2)
    if abs(diff) > ROUNDOFF_MAX:
        return "SKIP", f"MANUAL: amount diff {diff} > {ROUNDOFF_MAX} (bill {bt_total} / po {pt_total})", bill["total"], po["total"]

    orig_total = float(bill["total"])
    qty = float(p_li["quantity"])   # mirror the PO line's qty (matters for qty-mismatch/both-match)
    item_id = p_li["item_id"]

    # item status from CSV (no GET); fall back to API only if missing from CSV
    item_status = ITEM_STATUS.get(item_id)
    if item_status is None:
        _, itd = call("GET", f"/items/{item_id}")
        item_status = itd.get("item", {}).get("status")
    need_react = str(item_status).lower() != "active"
    if need_react and not REACTIVATE_INACTIVE:
        return "SKIP", "item inactive", orig_total, po["total"]

    # NOTE: the PO is the source of truth (= DB) and is NEVER modified. The bill is just
    # linked to it; any small round-off difference stays on the bill (its own adjustment).
    receive = []
    for rec in po.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            if rli.get("item_id") == item_id:
                receive.append({"receive_item_id": rli["line_item_id"], "quantity": qty})
                break

    # CANONICAL form: the bill line mirrors the PO line (rate = PO rate, no discount), and the
    # bill total is held at the paid amount by a round-off adjustment on the bill. The PO is
    # never modified (= DB). round_off = paid_total - PO_line_total(excl PO's own adjustment).
    po_line_total = round(float(po["total"]) - float(po.get("adjustment") or 0), 2)
    round_off = round(orig_total - po_line_total, 2)
    new_line = {"purchaseorder_item_id": p_li["line_item_id"], "item_id": item_id,
                "account_id": p_li.get("account_id") or b_li.get("account_id"),
                "name": p_li.get("name"), "description": "",
                "rate": float(p_li["rate"]), "quantity": qty, "discount": 0,
                "unit": p_li.get("unit") or b_li.get("unit"),
                "hsn_or_sac": p_li.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
                "tax_id": b_li.get("tax_id") or p_li.get("tax_id"),  # bill's tax = valid for its supply (intrastate vs IGST)
                "location_id": p_li.get("location_id") or po.get("location_id"),  # PO line's location (matches the receive)
                "item_order": 1, "is_billable": False}
    if receive:
        new_line["receive_line_items"] = receive

    ref = bill.get("reference_number") or ""
    if po["purchaseorder_number"] not in ref:
        ref = (ref + "," + po["purchaseorder_number"]).strip(",")
    # NOTE: do NOT send location_id — bills generated from a PO have a locked location;
    # sending it triggers "location cannot be modified". Omitting it keeps the existing one.
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms",
                "exchange_rate", "is_inclusive_tax", "is_item_level_tax_calc",
                "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bill_update = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line],
                   "location_id": po.get("location_id") or bill.get("location_id"),  # PO's location
                   "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bill_update[k] = bill[k]
    if bill.get("custom_fields"):
        bill_update["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")}
                                        for c in bill["custom_fields"]]

    if DRY_RUN:
        return "DRY", f"would link (PO untouched): bill_total={orig_total} po_line={po_line_total} round_off={round_off} react={need_react}", orig_total, po["total"]

    # ---- writes (link bill to PO only; PO is NOT modified) ----
    if need_react:
        call("POST", f"/items/{item_id}/active")
    st, r = call("PUT", f"/bills/{bill_id}", bill_update)
    bill_ok = st // 100 == 2 and r.get("code") in (0, None)
    # corrective: tax-type rounding (IGST vs CGST+SGST) can drift the total ~1 paisa;
    # nudge the round-off by the residual and re-PUT once so total == paid exactly.
    if bill_ok:
        b2 = r.get("bill", {})
        resid = round(orig_total - float(b2.get("total", orig_total)), 2)
        if 0.005 < abs(resid) <= 1.0:
            bill_update["adjustment"] = round(round_off + resid, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bill_update)
            bill_ok = st // 100 == 2 and r.get("code") in (0, None)
    if need_react:
        call("POST", f"/items/{item_id}/inactive")
    if not bill_ok:
        return "FAIL", f"bill update {st}: {r.get('message')}", orig_total, po["total"]

    # ---- verify from the PUT response (no extra GETs) ----
    b2 = r.get("bill", {})
    ok = (b2 and money_eq(b2.get("total", -1), orig_total)
          and len(b2.get("line_items", [])) == 1 and b2.get("purchaseorder_ids"))
    return ("OK" if ok else "CHECK",
            "" if ok else f"verify: billtot={b2.get('total')} lines={len(b2.get('line_items', []))} linked={bool(b2.get('purchaseorder_ids'))}",
            b2.get("total", orig_total), po["total"])


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    new = os.path.exists(RESULTS) is False
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number",
                                       "result", "reason", "bill_total", "po_total"])
    if w and new:
        w.writeheader()

    counts = {}
    n = 0
    for c in cands:
        if ONLY_BILL and c["bill_id"] != ONLY_BILL:
            continue
        if c["bill_id"] in done:
            continue
        if n >= LIMIT:
            break
        n += 1
        try:
            res, reason, bt, pt = attach_one(c["bill_id"], c["po_id"])
        except RateLimited as e:
            print(f"\nRATE LIMITED ({e}) -- stopping cleanly. Re-run later to resume.")
            n -= 1
            break
        except Exception as e:
            res, reason, bt, pt = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                        "po_number": c["po_number"], "result": res, "reason": reason,
                        "bill_total": bt, "po_total": pt})
            fh.flush()
        print(f"[{n}] {c['bill_number']} / {c['po_number']} -> {res} {reason}")
        time.sleep(SLEEP)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()
