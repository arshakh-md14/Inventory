"""
TEST detach/revert on ONE committed CHECK case (7390 / PO-05998).
Goal: unlink bill from PO, restore the bill's ORIGINAL total (it was inflated when
the line discount got dropped), restore the PO to unbilled, mark manual.

Restore strategy: rebuild the bill's original account-based line using an EFFECTIVE
rate (orig item_total / qty) so the total is reproduced exactly without depending on
Zoho's discount-amount semantics. No item_id / no PO link / no receive link.
"""
import json, http.client, urllib.parse

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
DRY_RUN = False

BILL_ID = "2432338000007048522"   # 7390
PO_ID = "2432338000006358857"     # PO-05998
# originals (from export snapshot, pre-attach)
ORIG_ITEM_TOTAL = 13012.75        # pre-tax line total (after discount)
QTY = 37.0
ORIG_BILL_TOTAL = 15354.99
ORIG_DESC = "Flandes Cotto 16x16 Parking Tiles Creanza"
COGS_ACCOUNT = "2432338000000000567"
ORIG_PO_RATE = 351.69
ORIG_PO_ADJ = 0.0

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_tok = None


def token():
    global _tok
    if _tok:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _tok = json.loads(c.getresponse().read())["access_token"]
    return _tok


def call(method, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    sep = "&" if "?" in path else "?"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"
        c.request(method, f"/books/v3{path}{sep}organization_id={ORG}", json.dumps(body), h)
    else:
        c.request(method, f"/books/v3{path}{sep}organization_id={ORG}", headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read().decode())


_, bd = call("GET", f"/bills/{BILL_ID}")
bill = bd["bill"]
bl = bill["line_items"][0]
print(f"BEFORE bill: total={bill['total']} linked={bill.get('purchaseorder_ids')}")

eff_rate = round(ORIG_ITEM_TOTAL / QTY, 6)
revert_line = {"account_id": bl.get("account_id") or COGS_ACCOUNT,
               "name": ORIG_DESC, "description": ORIG_DESC,
               "rate": eff_rate, "quantity": QTY,
               "tax_id": bl.get("tax_id"), "item_order": 1}
PRESERVE = ["location_id", "vendor_id", "date", "due_date", "notes", "terms",
            "adjustment", "adjustment_description", "exchange_rate", "is_inclusive_tax",
            "is_item_level_tax_calc", "payment_terms", "payment_terms_label",
            "gst_treatment", "gst_no", "source_of_supply", "destination_of_supply",
            "template_id", "billing_address_id"]
bill_revert = {"bill_number": bill["bill_number"], "line_items": [revert_line]}
for k in PRESERVE:
    if bill.get(k) not in (None, ""):
        bill_revert[k] = bill[k]
# drop the PO number we appended to reference_number (restore original-ish)
ref = (bill.get("reference_number") or "").replace("PO-05998", "").strip(", ")
bill_revert["reference_number"] = ref

print("\n--- BILL REVERT PUT /bills (effective rate %.6f, no PO link) ---" % eff_rate)
print(json.dumps(bill_revert, indent=2)[:900])

if DRY_RUN:
    print("\n[DRY] no writes.")
    raise SystemExit

# 1. revert bill (unlinks PO)
st, r = call("PUT", f"/bills/{BILL_ID}", bill_revert)
print("bill revert HTTP", st, r.get("message"))
_, bd2 = call("GET", f"/bills/{BILL_ID}")
b2 = bd2["bill"]
print(f"AFTER bill: total={b2['total']} (orig {ORIG_BILL_TOTAL}) linked={b2.get('purchaseorder_ids')} lines={len(b2['line_items'])}")

# 2. restore PO (unbilled now) -> original rate + adjustment 0
_, pd = call("GET", f"/purchaseorders/{PO_ID}")
po = pd["purchaseorder"]; pl = po["line_items"][0]
print(f"PO after unlink: billed={po.get('billed_status')} status={po.get('status')} total={po['total']}")
po_restore = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                              "rate": ORIG_PO_RATE, "quantity": QTY, "tax_id": pl.get("tax_id")}],
              "adjustment": ORIG_PO_ADJ, "adjustment_description": "Round Off"}
st, r = call("PUT", f"/purchaseorders/{PO_ID}", po_restore)
print("PO restore HTTP", st, r.get("message"))
_, pd2 = call("GET", f"/purchaseorders/{PO_ID}")
p2 = pd2["purchaseorder"]
print(f"PO final: billed={p2.get('billed_status')} status={p2.get('status')} total={p2['total']} rate={p2['line_items'][0]['rate']}")

ok = abs(float(b2["total"]) - ORIG_BILL_TOTAL) < 0.05 and not b2.get("purchaseorder_ids") and p2.get("billed_status") != "billed"
print("\nRESULT:", "OK (detached + restored)" if ok else "CHECK MANUALLY")
