# Zoho Bill ↔ PO Reconciliation & Attach — Scripts

Main scripts from the operation that reconciled Zoho Books vendor bills (Sep 2025+) to their
Purchase Orders for the **Haut Luxe** org and attached the matched ones via the OAuth API.

- Data lives in the repo root (`bill_po_reconciliation.csv`, `attach_results_*.csv`, `*_candidates.csv`, `Raw Files/`).
- Scripts reference the original session **scratchpad** paths internally (imports + file paths); to run
  from here those absolute paths need updating first.
- Only the production-pipeline scripts are kept here; candidate builders, one-off repairs/reverts and
  exploratory tests were removed (still available in git history).

## Attach flows (one per case)

| Script | Case it was used for |
|---|---|
| **sync_po_then_attach.py** | **Core module** — OAuth `call()`/`token()` + `money_eq`/`ITEM_STATUS` imported by the others. Also the single-line **amount-diff Bucket A** flow (Zoho PO ≠ DB, Bill = DB): drive PO to DB value, then attach. Reused as single-line **take-bill** (target = bill amount). |
| **draft_issue_grn_attach.py** | **Draft-PO, single-line** (delivered + inventory): sync PO → issue (draft→open) → create GRN → attach. Also provides `call_inv()`/`grn_endpoint()` (Inventory API) reused by the charge/subset attaches. |
| **batch_attach.py** | **Clean single-line matched** bills → bulk attach (the largest bucket, ~8.8k). |
| **attach_multiline.py** | **Multi-line + mixed (Inventory+Sales&Purchase)** matched bills → attach. |
| **sync_multiline_db.py** | **Amount-diff Bucket A, multi-line**: drive multi-line PO total to DB, then attach. |
| **scale_po_own_multi.py** | **Take-the-bill-amount, multi-line qty-mismatch**: scale the PO's own lines to the bill total (no bill↔PO line pairing). |
| **draft_multiline_grn_attach.py** | **Draft-PO, multi-line** (delivered + inventory): sync/scale → issue → GRN all lines → attach. |
| **attach_charge_itemcount.py** | **Item-count mismatch = freight/transport charge**: add the charge line to the PO (take bill as truth), then attach (products → GRN, charge → direct). |
| **itemcount_subset_attach.py** | **Item-count = bill subset / false-mismatch**: mirror ONLY the amount-matched PO lines; extra PO lines left unbilled (PO partially billed). Bridges to paid via per-line tax %. |
| **multipo_attach.py** | **Multi-PO** (one bill → several POs): attach the UNION of all POs' lines (each line → its PO line + that PO's GRN); gap on bill round-off, cap ₹10. |
| **vendor_repoint_attach.py** | **Vendor mismatch, same-GSTIN** (duplicate vendor record): repoint the PO to the bill's vendor (activate if inactive), then attach. |

## Analysis / classification

| Script | Case |
|---|---|
| **check_amountdiff_vs_db.py** | Compare Zoho PO amount vs DB `total_po_raised_amount` (active row) for "amount diff > ₹10" bills → `amount_diff_po_vs_db.csv`. |
| **classify_itemcount.py** | Classify paid+inventory item-count-mismatch bills by line-amount matching: charge vs genuine extra product vs bill-subset vs false-mismatch. |
| **classify_vendorfix_v3.py** | Vendor-mismatch classifier: GET each pair's bill+PO vendor_ids → GSTIN via Vendors CSV → same-GST (repointable) vs genuinely different. |
| **get_multipo_delivery.py** | DB delivery status for every MD ref in the multi-PO set → `multipo_db_delivery.csv`. |

## Sheet builder

| Script | Case |
|---|---|
| **update_recon_status.py** | **The reconciliation sheet rebuilder** — recomputes Status (Matched/Not Matched), Attach Status, Amount columns, and the exact per-row Reason from the latest result logs. |
