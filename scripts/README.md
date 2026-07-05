# Zoho Bill ↔ PO Reconciliation & Attach — Scripts

Working scripts from the operation that reconciled Zoho Books vendor bills (Sep 2025+) to
their Purchase Orders for the **Haut Luxe** org and attached the matched ones via the OAuth API.

- Data lives in the repo root (`bill_po_reconciliation.csv`, `attach_results_*.csv`, `*_candidates.csv`, `Raw Files/`).
- Scripts reference the original session **scratchpad** paths internally (imports + file paths); to run
  from here those absolute paths need updating first.
- **★ = main script** (part of the production flow); the rest are candidate builders, analysis, one-off
  fixes, or exploratory tests.

## Main scripts (the production flow)

| Script | Case it was used for |
|---|---|
| **sync_po_then_attach.py** ★★ | **Core module** — OAuth `call()`/`token()` + `money_eq`/`ITEM_STATUS` imported by most others. Also the single-line **amount-diff Bucket A** flow (Zoho PO ≠ DB, Bill = DB): drive PO to DB value, then attach. Reused as single-line **take-bill** (target = bill amount). |
| **draft_issue_grn_attach.py** ★ | **Draft-PO, single-line** (delivered + inventory): sync PO → issue (draft→open) → create GRN → attach. Also provides `call_inv()`/`grn_endpoint()` (Inventory API) reused by charge/subset attaches. |
| **batch_attach.py** ★ | **Clean single-line matched** bills → bulk attach (the largest bucket, ~8.8k). |
| **attach_multiline.py** ★ | **Multi-line + mixed (Inventory+Sales&Purchase)** matched bills → attach. |
| **sync_multiline_db.py** ★ | **Amount-diff Bucket A, multi-line**: drive multi-line PO total to DB, then attach. |
| **scale_po_own_multi.py** ★ | **Take-the-bill-amount, multi-line qty-mismatch**: scale the PO's own lines to the bill total (no bill↔PO line pairing). |
| **draft_multiline_grn_attach.py** ★ | **Draft-PO, multi-line** (delivered + inventory): sync/scale → issue → GRN all lines → attach. |
| **attach_charge_itemcount.py** ★ | **Item-count mismatch = freight/transport charge**: add the charge line to the PO (take bill as truth), then attach (products → GRN, charge → direct). |
| **itemcount_subset_attach.py** ★ | **Item-count = bill subset / false-mismatch**: mirror ONLY the amount-matched PO lines; extra PO lines left unbilled (PO partially billed). Bridges to paid via per-line tax %. |
| **multipo_attach.py** ★ | **Multi-PO** (one bill → several POs): attach the UNION of all POs' lines (each line → its PO line + that PO's GRN); gap on bill round-off, cap ₹10. |
| **vendor_repoint_attach.py** ★ | **Vendor mismatch, same-GSTIN** (duplicate vendor record): repoint the PO to the bill's vendor (activate if inactive), then attach. |
| **update_recon_status.py** ★★ | **The reconciliation sheet rebuilder** — recomputes Status (Matched/Not Matched), Attach Status, Amount columns, and the exact per-row Reason from the latest result logs. |

## Analysis / classification

| Script | Case |
|---|---|
| **check_amountdiff_vs_db.py** ★ | Compare Zoho PO amount vs DB `total_po_raised_amount` (active row) for "amount diff > ₹10" bills → `amount_diff_po_vs_db.csv`. |
| **classify_itemcount.py** ★ | Classify paid+inventory item-count-mismatch bills by line-amount matching: charge vs genuine extra product vs bill-subset vs false-mismatch. |
| **classify_vendorfix_v3.py** ★ | Latest vendor-mismatch classifier: GET each pair's bill+PO vendor_ids → GSTIN via Vendors CSV → same-GST (repointable) vs genuinely different. |
| classify_vendorfix.py / _v2.py / _offline.py | Earlier iterations of the vendor-mismatch classification (v3 supersedes). |
| **get_multipo_delivery.py** ★ | DB delivery status for every MD ref in the multi-PO set → `multipo_db_delivery.csv`. |
| draft_po_db_status.py | DB delivery-status distribution for the draft-PO bucket. |
| distribution.py / no_po.py / qty_bothmatch.py | Ad-hoc distribution stats / bills with no PO / qty both-match inspection. |
| verify_amounts.py | READ-ONLY check of the Amount-Agreement/Verdict logic before applying to all rows. |

## Candidate builders (one per bucket)

| Script | Builds candidates for |
|---|---|
| build_candidates.py | Single-line matched (with Zoho IDs). |
| build_clean_candidates.py | Clean single-line (excludes vendor/draft/not-received up front). |
| build_multiline_candidates.py | Multi-line matched (all-Inventory or all-S&P). |
| build_mixed_candidates.py | Mixed Inventory + Sales&Purchase multi-line. |
| build_qtymatch_candidates.py / build_qtymatch_pending.py | Qty-mismatch / both-match (value-match). |
| build_amountdiff_candidates.py | Amount-diff Bucket A (carries DB target). |
| build_draft_inv_candidates.py | Draft-PO delivered + inventory. |
| build_takebill_candidates.py / build_takebill_extra.py | Take-the-bill-amount buckets. |
| build_multipo_candidates.py | Multi-PO (bill → several MD POs). |
| build_vendorfix_candidates.py | Vendor-mismatch fix. |

## Repair / revert / one-off fixes

| Script | Case |
|---|---|
| repair_inflated.py | Fix 6 inflated bills from the amount-diff run (dropped discount). |
| normalize_po_to_db.py | Normalize PO total to DB for bills linked under the earlier PO=paid approach. |
| restore_po_to_db.py | Restore reworked POs to their DB export values. |
| correct_multipo_balance.py | Fix the ₹0.01 residual balance left by multi-PO round-off bumping. |
| fix_check_cases.py / fix_check5.py | Align PO tax to bill (tax-mismatch CHECK) / nudge IGST 1-paisa drift. |
| batch_revert.py / tax_revert.py / detach_one.py | Revert committed CHECK / tax-aligned cases to pre-match; single-case detach test. |
| combined_cleanup.py | Rewrite OK cases to canonical bill form. |
| consolidate_status.py / regen_recon.py / reconcile.py | Earlier reconciliation-sheet builders (superseded by update_recon_status.py). |

## Exploratory / tests / utilities

| Script | Purpose |
|---|---|
| zoho_test_read.py / zoho_read2.py | READ-ONLY OAuth connectivity + live bill/PO inspection. |
| canonical_bill_one.py | Prototype the canonical attach on a single case. |
| feasibility_billed_po.py | Test whether a billed PO's line rate can be edited. |
| bisect.py / recon_bisect.py / db_lookup.py | Debug / DB-lookup helpers. |
| add_item_type.py / add_vendor.py / add_no_ref.py | One-off sheet-column enrichment. |
| attach_bill_to_po.py | Early single-attach prototype (superseded by batch_attach.py). |
| _fixpath.py | Repair Windows paths mangled by `sed`. |
