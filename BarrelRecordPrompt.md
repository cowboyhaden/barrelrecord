# Prompt: Coffee Barrel QR Tracking App (Streamlit + Google Sheets)

Build a Streamlit app (`app.py` + `requirements.txt`) that tracks coffee barrels via QR codes. The app has three workflows sharing a single Google Sheet as the backend. Deploy via GitHub → Streamlit Community Cloud.

---

## Architecture

### Google Sheets Setup (2 tabs)

**Tab: `barrels`**
| Column | Description |
|--------|-------------|
| `barrel_id` | Unique ID: `{variety_slug}_{YYYYMMDD}_{##}` (e.g., `ethiopan-yirgacheffe_20260313_01`) |
| `variety` | Coffee variety name (free text) |
| `date_created` | Date barrel was created (YYYY-MM-DD) |
| `barrel_number` | Sequence number for same variety + date (1, 2, 3…) |
| `qr_code_id` | The fixed QR code identifier (a short random string like `QR-A7X3`) — this is what's physically printed on the label |
| `status` | `active` or `reassigned` |
| `assigned_date` | Timestamp when this QR was linked to this barrel |

**Tab: `withdrawals`**
| Column | Description |
|--------|-------------|
| `withdrawal_id` | Auto-generated UUID |
| `barrel_id` | Links to barrels tab |
| `qr_code_id` | The QR that was scanned |
| `product_type` | One of the product options (see below) |
| `weight_oz` | Weight taken, in ounces |
| `timestamp` | When the withdrawal was recorded |
| `notes` | Optional notes field |

### QR Code Design

Each QR code encodes a URL: `https://{your-app-url}/?qr={qr_code_id}`

The `qr_code_id` is a persistent physical label ID (e.g., `QR-A7X3`). It does NOT change when a barrel is reassigned. The app looks up which barrel is currently assigned to that QR code.

### Product Types (constants list)

```python
PRODUCT_TYPES = [
    "Bulk / Drive-Thru",
    "12 oz Bag – Whole Bean",
    "12 oz Bag – Ground",
    "5 lb Bag – Whole Bean",
    "5 lb Bag – Ground",
    "Sample / Tasting",
    "Other",
]
```

---

## Code Structure (MANDATORY)

Follow this exact four-section layout with `# ====` separators:

```
1. IMPORTS
2. CONFIGURATION CONSTANTS
3. HELPER FUNCTIONS (pure — no st.* calls)
4. STREAMLIT UI CODE (all st.* calls here)
```

Use `st.set_page_config()` as the very first `st.* call`. Use `st.session_state` for multi-step flows.

---

## Google Sheets Integration

Use `gspread` with a service account. Credentials come from `st.secrets["gcp_service_account"]` (a dict). The sheet name comes from `st.secrets["sheets"]["spreadsheet_id"]`.

```python
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_gsheet_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_gsheet_client()
    return client.open_by_key(st.secrets["sheets"]["spreadsheet_id"])
```

All reads/writes go through helper functions (no `st.*` inside them). Handle `gspread.exceptions.APIError` gracefully.

---

## Workflow 1: Generate QR Code & Register Barrel (Admin)

**Route:** App loads with no `?qr=` param → show admin interface behind a simple password gate (`st.secrets["app"]["admin_password"]`).

**UI Flow:**
1. Password input → if correct, show admin panel (store auth in `st.session_state`)
2. Admin panel has two sub-sections via `st.tabs`: **"New Barrel"** and **"Manage Barrels"**

**New Barrel tab:**
- `st.text_input` for coffee variety
- `st.date_input` for date (default today)
- `st.number_input` for barrel number (min=1, step=1) — auto-suggest next number by querying sheet for existing barrels of same variety + date
- `st.button("Generate QR & Register")`
- On submit:
  1. Generate a random `qr_code_id` (e.g., `QR-` + 6 alphanumeric chars). Verify it's unique in the sheet.
  2. Build `barrel_id` from `{variety_slug}_{date}_{number}`
  3. Append row to `barrels` tab
  4. Generate QR code image (using `qrcode` library) encoding the URL `https://{BASE_URL}/?qr={qr_code_id}` — the `BASE_URL` comes from `st.secrets["app"]["base_url"]`
  5. Display the QR code image with `st.image()`
  6. Provide `st.download_button` for the QR code PNG
  7. Show barrel details as confirmation

**Manage Barrels tab:**
- `st.dataframe` showing all active barrels with their QR codes
- Basic search/filter by variety

---

## Workflow 2: Scan QR → Record Withdrawal

**Route:** App loads with `?qr={qr_code_id}` in URL query params.

**Logic:**
1. Read `qr_code_id` from `st.query_params`
2. Look up the QR in the `barrels` tab — find the row where `qr_code_id` matches AND `status` == `active`
3. If no active barrel found → show message + offer reassignment (Workflow 3)
4. If found → show barrel info and withdrawal form

**UI Flow:**
- Display barrel info at top: variety, date created, barrel number (use `st.markdown` or `st.metric`)
- `st.selectbox` for product type (from `PRODUCT_TYPES`)
- `st.number_input` for weight in ounces (min=0.0, step=0.1, format="%.1f")
- `st.text_input` for optional notes
- `st.button("Record Withdrawal")`
- On submit:
  1. Validate weight > 0
  2. Append row to `withdrawals` tab
  3. Show `st.success` with summary
  4. Show `st.balloons()` for fun
  5. After success, show a "Record Another" button that resets the form via `st.session_state`

---

## Workflow 3: Reassign QR Code to New Barrel

**Route:** Same as Workflow 2, but triggered when the scanned QR has no active barrel OR user clicks "Reassign this QR."

**UI Flow:**
- Show current barrel info (if any) with status note
- `st.warning("This QR code needs to be assigned to a new barrel.")`
- Same inputs as Workflow 1's New Barrel form: variety, date, barrel number
- `st.button("Reassign QR to New Barrel")`
- On submit:
  1. Set old barrel row's `status` to `reassigned` (update in sheet)
  2. Append new row to `barrels` tab with same `qr_code_id` but new barrel details, `status` = `active`
  3. Show `st.success` confirmation
  4. Auto-refresh to show the new barrel's withdrawal form

---

## Secrets Structure

The app expects this in `.streamlit/secrets.toml`:

```toml
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "..."
client_email = "..."
client_id = "..."
auth_uri = "..."
token_uri = "..."
auth_provider_x509_cert_url = "..."
client_x509_cert_url = "..."

[sheets]
spreadsheet_id = "your-google-sheet-id"

[app]
base_url = "https://your-app.streamlit.app"
admin_password = "your-admin-password"
```

---

## Requirements

```
streamlit>=1.30.0
gspread>=6.0.0
google-auth>=2.0.0
qrcode[pil]>=7.4
pandas>=2.0.0
```

---

## Mobile-First Considerations

- Use `layout="centered"` in `st.set_page_config` (not "wide") — the scan workflow is phone-first
- Keep the withdrawal form minimal and thumb-friendly — large buttons, clear labels
- Use `st.form` with `st.form_submit_button` for the withdrawal to prevent accidental double-submits
- The admin workflow (QR generation) can assume desktop but should still work on mobile

---

## Error Handling

- Wrap all Google Sheets calls in try/except with user-friendly `st.error()` messages
- If the sheet connection fails, show a clear error and stop (don't crash)
- Validate all inputs before writing to sheet
- Handle the case where `?qr=` param contains an invalid/unknown QR code gracefully

---

## Summary of Files to Produce

1. **`app.py`** — single file, four-section structure, all logic included
2. **`requirements.txt`** — pinned minimum versions, only what's imported
