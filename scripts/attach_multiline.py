"""
Multi-line canonical attach (inventory-first) with ALL fixes from the single-line run:
  bill lines = the PO's lines (rate, item, per-line location, receive link); single round-off
  on the bill so total == paid; PO untouched. Item status from CSV; verify via PUT response;
  network + transient-400 retry; partially-billed guard; round-off corrective.
Reads attach_candidates_multiline.csv -> logs attach_results_multiline.csv.
"""
import json, http.client, urllib.parse, time, os, csv, glob

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
CAND = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_candidates_mixed.csv"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results_mixed.csv"

DRY_RUN = False
REACTIVATE_INACTIVE = True
ALLOW_QTY_MISMATCH = True   # mixed set may include qty-both-match; amount guard is the gate
LIMIT = 400
SLEEP = 1.0
ROUNDOFF_MAX = 10.0
ONLY_BILL = None
ITEM_CLASS_ORDER = ["inventory", "sales_purchase"]   # inventory first

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None; _ts = 0


class RateLimited(Exception):
    pass


def token(force=False):
    global _tok, _ts
    if _tok and not force and (time.time() - _ts) < 2400:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}"); _tok = json.loads(c.getresponse().read())["access_token"]; _ts = time.time()
    return _tok


def call(method, path, body=None, _retry=True, _net=5):
    sep = "&" if "?" in path else "?"
    full = f"/books/v3{path}{sep}organization_id={ORG}"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    try:
        c = http.client.HTTPSConnection("www.zohoapis.in", timeout=60)
        if body is not None:
            h["content-type"] = "application/json"; c.request(method, full, json.dumps(body), h)
        else:
            c.request(method, full, headers=h)
        r = c.getresponse(); status = r.status; data = json.loads(r.read().decode())
    except (OSError, http.client.HTTPException):
        if _net > 0:
            time.sleep(3 * (6 - _net)); return call(method, path, body, _retry, _net - 1)
        raise
    if status == 401 and _retry:
        token(force=True); return call(method, path, body, _retry=False)
    msg = str(data.get("message", "")).lower()
    if status == 400 and "requested action could not be completed" in msg and _net > 0:
        time.sleep(3 * (6 - _net)); return call(method, path, body, _retry, _net - 1)
    if status == 429 or data.get("code") in (1001,) or "too many requests" in msg:
        raise RateLimited(msg)
    return status, data


def money_eq(a, b):
    return abs(float(a) - float(b)) < 0.005


ITEM_STATUS = {}
for _f in glob.glob(r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files\Item_*\Item*.csv"):
    for _row in csv.DictReader(open(_f, encoding="utf-8")):
        _i = _row["Item ID"].strip()
        if _i:
            ITEM_STATUS[_i] = _row.get("Status", "").strip()


def attach_multi(bill_id, po_id):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bill["total"], po["total"]
    bls, pls = bill["line_items"], po["line_items"]
    if len(bls) < 2 or len(pls) != len(bls):
        return "SKIP", f"line count bill={len(bls)} po={len(pls)}", bill["total"], po["total"]
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bill["total"], po["total"]
    if po.get("status") != "open":
        return "SKIP", f"PO status {po.get('status')}", bill["total"], po["total"]
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bill["total"], po["total"]
    if any(float(pl.get("quantity_billed") or 0) > 0 for pl in pls):
        return "SKIP", "PO line partially billed", bill["total"], po["total"]
    if not ALLOW_QTY_MISMATCH and sorted(round(float(x["quantity"]), 2) for x in bls) != sorted(round(float(x["quantity"]), 2) for x in pls):
        return "SKIP", "qty multiset mismatch", bill["total"], po["total"]
    orig_total = float(bill["total"])
    diff = round(orig_total - float(po["total"]), 2)
    if abs(diff) > ROUNDOFF_MAX:
        return "SKIP", f"MANUAL: amount diff {diff} > {ROUNDOFF_MAX}", bill["total"], po["total"]

    inactive = [i for i in {pl["item_id"] for pl in pls} if str(ITEM_STATUS.get(i, "active")).lower() != "active"]
    if inactive and not REACTIVATE_INACTIVE:
        return "SKIP", "inactive item", orig_total, po["total"]

    # receive lines per item -> queue of receive_item_ids
    recq = {}
    for rec in po.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            recq.setdefault(rli.get("item_id"), []).append(rli["line_item_id"])

    new_lines = []
    for i, pl in enumerate(pls, 1):
        nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl["item_id"],
              "account_id": pl.get("account_id"), "name": pl.get("name"), "description": "",
              "rate": float(pl["rate"]), "quantity": float(pl["quantity"]), "discount": 0,
              "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
              "location_id": pl.get("location_id") or po.get("location_id"),
              "item_order": i, "is_billable": False}
        q = recq.get(pl["item_id"])
        if q:
            nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
        new_lines.append(nl)

    round_off = round(orig_total - round(float(po["total"]) - float(po.get("adjustment") or 0), 2), 2)
    ref = bill.get("reference_number") or ""
    if po["purchaseorder_number"] not in ref:
        ref = (ref + "," + po["purchaseorder_number"]).strip(",")
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": new_lines,
          "location_id": po.get("location_id") or bill.get("location_id"),
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]

    if DRY_RUN:
        return "DRY", f"would link {len(new_lines)} lines: paid={orig_total} round_off={round_off} inactive={len(inactive)}", orig_total, po["total"]

    for iid in inactive:
        call("POST", f"/items/{iid}/active")
    st, r = call("PUT", f"/bills/{bill_id}", bu)
    ok_put = st // 100 == 2 and r.get("code") in (0, None)
    if ok_put:
        b2 = r.get("bill", {})
        resid = round(orig_total - float(b2.get("total", orig_total)), 2)
        if 0.005 < abs(resid) <= 1.0:
            bu["adjustment"] = round(round_off + resid, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu)
            ok_put = st // 100 == 2 and r.get("code") in (0, None)
    for iid in inactive:
        call("POST", f"/items/{iid}/inactive")
    if not ok_put:
        return "FAIL", f"bill PUT {st}: {r.get('message')}", orig_total, po["total"]
    b2 = r.get("bill", {})
    ok = (money_eq(b2.get("total", -1), orig_total) and len(b2.get("line_items", [])) == len(pls)
          and b2.get("purchaseorder_ids"))
    return ("OK" if ok else "CHECK",
            "" if ok else f"verify billtot={b2.get('total')} lines={len(b2.get('line_items', []))}",
            b2.get("total", orig_total), po["total"])


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    cands.sort(key=lambda c: ITEM_CLASS_ORDER.index(c["item_class"]) if c["item_class"] in ITEM_CLASS_ORDER else 9)
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "n_lines", "item_class", "result", "reason", "bill_total", "po_total"])
    if w and not done:
        w.writeheader()
    counts, n = {}, 0
    for c in cands:
        if ONLY_BILL and c["bill_id"] != ONLY_BILL:
            continue
        if c["bill_id"] in done:
            continue
        if n >= LIMIT:
            break
        n += 1
        try:
            res, reason, bt, pt = attach_multi(c["bill_id"], c["po_id"])
        except RateLimited as e:
            print(f"\nRATE LIMITED ({e}) -- stopping cleanly."); n -= 1; break
        except Exception as e:
            res, reason, bt, pt = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                        "po_number": c["po_number"], "n_lines": c["n_lines"], "item_class": c["item_class"],
                        "result": res, "reason": reason, "bill_total": bt, "po_total": pt}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} ({c['item_class']},{c['n_lines']}ln) -> {res} {reason}")
        time.sleep(SLEEP)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()
