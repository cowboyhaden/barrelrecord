# ============================
# 1. IMPORTS
# ============================

import io
import random
import string
import uuid
from datetime import date, datetime

import gspread
import pandas as pd
import qrcode
import streamlit as st
from google.oauth2.service_account import Credentials
from PIL import Image
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# ============================
# 2. CONFIGURATION CONSTANTS
# ============================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PRODUCT_TYPES = [
    "Bulk / Drive-Thru",
    "12 oz Bag – Whole Bean",
    "12 oz Bag – Ground",
    "5 lb Bag – Whole Bean",
    "5 lb Bag – Ground",
    "Sample / Tasting",
    "Other",
]

APP_VERSION = "1.6.1"

BARRELS_SHEET = "barrels"
WITHDRAWALS_SHEET = "withdrawals"
VARIETIES_SHEET = "varieties"
PRODUCTS_SHEET = "products"

BARRELS_HEADERS = [
    "barrel_id",
    "variety",
    "date_created",
    "barrel_number",
    "qr_code_id",
    "status",
    "assigned_date",
]

WITHDRAWALS_HEADERS = [
    "withdrawal_id",
    "barrel_id",
    "qr_code_id",
    "product_type",
    "weight_oz",
    "timestamp",
    "notes",
]

# ============================
# 3. HELPER FUNCTIONS
# ============================


def get_gsheet_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    return gspread.authorize(creds)


def get_spreadsheet():
    client = get_gsheet_client()
    return client.open_by_key(st.secrets["sheets"]["spreadsheet_id"])


def get_barrels_df(spreadsheet):
    """Return barrels tab as a DataFrame. Returns empty DataFrame on error."""
    try:
        ws = spreadsheet.worksheet(BARRELS_SHEET)
        records = ws.get_all_records()
        if not records:
            return pd.DataFrame(columns=BARRELS_HEADERS)
        return pd.DataFrame(records)
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to read barrels sheet: {e}") from e


def get_withdrawals_df(spreadsheet):
    """Return withdrawals tab as a DataFrame."""
    try:
        ws = spreadsheet.worksheet(WITHDRAWALS_SHEET)
        records = ws.get_all_records()
        if not records:
            return pd.DataFrame(columns=WITHDRAWALS_HEADERS)
        return pd.DataFrame(records)
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to read withdrawals sheet: {e}") from e


def find_active_barrel(spreadsheet, qr_code_id):
    """Return the active barrel dict for a given qr_code_id, or None."""
    try:
        df = get_barrels_df(spreadsheet)
        if df.empty:
            return None
        match = df[(df["qr_code_id"] == qr_code_id) & (df["status"] == "active")]
        if match.empty:
            return None
        return match.iloc[0].to_dict()
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Sheet lookup failed: {e}") from e


def get_next_barrel_number(spreadsheet, variety, date_str):
    """Return the suggested next barrel number for variety + date combo."""
    try:
        df = get_barrels_df(spreadsheet)
        if df.empty:
            return 1
        filtered = df[
            (df["variety"].str.lower() == variety.lower())
            & (df["date_created"] == date_str)
        ]
        if filtered.empty:
            return 1
        return int(filtered["barrel_number"].max()) + 1
    except Exception:
        return 1


def generate_unique_qr_id(spreadsheet):
    """Generate a unique QR-XXXXXX identifier not already in the barrels sheet."""
    try:
        df = get_barrels_df(spreadsheet)
        existing = set(df["qr_code_id"].tolist()) if not df.empty else set()
    except Exception:
        existing = set()

    for _ in range(20):
        candidate = "QR-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if candidate not in existing:
            return candidate
    raise RuntimeError("Could not generate a unique QR code ID after 20 attempts.")


def build_variety_slug(variety):
    """Convert variety name to a URL-safe slug."""
    return variety.lower().strip().replace(" ", "-").replace("/", "-")


def register_barrel(spreadsheet, barrel_id, variety, date_str, barrel_num, qr_code_id):
    """Append a new row to the barrels tab."""
    try:
        ws = spreadsheet.worksheet(BARRELS_SHEET)
        assigned_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [barrel_id, variety, date_str, barrel_num, qr_code_id, "active", assigned_date]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to register barrel: {e}") from e


def record_withdrawal(spreadsheet, barrel_id, qr_code_id, product_type, weight_oz, notes):
    """Append a new row to the withdrawals tab."""
    try:
        ws = spreadsheet.worksheet(WITHDRAWALS_SHEET)
        withdrawal_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [withdrawal_id, barrel_id, qr_code_id, product_type, weight_oz, timestamp, notes]
        ws.append_row(row, value_input_option="USER_ENTERED")
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to record withdrawal: {e}") from e


def reassign_qr(spreadsheet, qr_code_id, new_variety, new_date_str, new_barrel_num):
    """
    Mark the existing active barrel for qr_code_id as 'reassigned',
    then append a new active barrel row with the new details.
    Returns the new barrel_id.
    """
    try:
        ws = spreadsheet.worksheet(BARRELS_SHEET)
        records = ws.get_all_records()
        df = pd.DataFrame(records) if records else pd.DataFrame(columns=BARRELS_HEADERS)

        # Find row index of the currently active barrel (1-based + 1 header row)
        match = df[(df["qr_code_id"] == qr_code_id) & (df["status"] == "active")]
        if not match.empty:
            sheet_row = match.index[0] + 2  # +1 for 0-index, +1 for header
            status_col = BARRELS_HEADERS.index("status") + 1
            ws.update_cell(sheet_row, status_col, "reassigned")

        # Build new barrel row
        slug = build_variety_slug(new_variety)
        num_str = str(new_barrel_num).zfill(2)
        new_barrel_id = f"{slug}_{new_date_str.replace('-', '')}_{num_str}"
        assigned_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_row = [
            new_barrel_id,
            new_variety,
            new_date_str,
            new_barrel_num,
            qr_code_id,
            "active",
            assigned_date,
        ]
        ws.append_row(new_row, value_input_option="USER_ENTERED")
        return new_barrel_id
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to reassign QR code: {e}") from e


def get_products(spreadsheet):
    """Return sorted list of product types from the products tab."""
    try:
        ws = spreadsheet.worksheet(PRODUCTS_SHEET)
        values = ws.col_values(1)
        names = [v.strip() for v in values if v.strip()]
        if names and names[0].lower() in ("product", "product_type", "products", "name"):
            names = names[1:]
        return names
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to read products sheet: {e}") from e


def get_varieties(spreadsheet):
    """Return sorted list of variety names from the varieties tab."""
    try:
        ws = spreadsheet.worksheet(VARIETIES_SHEET)
        values = ws.col_values(1)  # first column
        # Drop header row if present
        names = [v.strip() for v in values if v.strip()]
        if names and names[0].lower() in ("variety", "varieties", "name"):
            names = names[1:]
        return sorted(names)
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Failed to read varieties sheet: {e}") from e


def generate_qr_pdf(barrels_data, base_url):
    """Generate a PDF with one 2x1.5 inch QR label per page."""
    buf = io.BytesIO()
    page_w = 2.0 * inch
    page_h = 1.5 * inch
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    for barrel in barrels_data:
        qr_buf = build_qr_image(barrel["qr_code_id"], base_url)
        qr_img = ImageReader(qr_buf)

        # QR code: 1.1" square, centered horizontally, top-aligned with margin
        qr_size = 1.1 * inch
        qr_x = (page_w - qr_size) / 2
        qr_y = page_h - qr_size - 0.05 * inch

        c.drawImage(qr_img, qr_x, qr_y, qr_size, qr_size)

        # Variety name
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(page_w / 2, qr_y - 0.10 * inch, barrel["variety"])

        # Barrel # and date
        c.setFont("Helvetica", 6)
        c.drawCentredString(
            page_w / 2,
            qr_y - 0.26 * inch,
            f"Barrel #{barrel['barrel_number']}  |  {barrel['date_created']}",
        )

        c.showPage()

    c.save()
    buf.seek(0)
    return buf


def build_qr_image(qr_code_id, base_url):
    """Generate a QR code PNG (BytesIO) encoding the app URL for the given QR ID."""
    url = f"{base_url.rstrip('/')}/?qr={qr_code_id}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ============================
# 4. STREAMLIT UI CODE
# ============================

st.set_page_config(
    page_title="Coffee Barrel Tracker",
    page_icon="☕",
    layout="centered",
)


def show_error_and_stop(message):
    st.error(message)
    st.stop()


# --------------------------------------------------
# Route: determine if this is an admin or scan visit
# --------------------------------------------------
qr_param = st.query_params.get("qr", None)

# ==================================================
# WORKFLOW 2 & 3: QR SCAN
# ==================================================
if qr_param:
    try:
        spreadsheet = get_spreadsheet()
    except Exception as e:
        show_error_and_stop(f"Could not connect to Google Sheets: {e}")

    # Look up the active barrel for this QR
    try:
        barrel = find_active_barrel(spreadsheet, qr_param)
    except Exception as e:
        show_error_and_stop(f"Error looking up QR code: {e}")

    # Determine if we're in reassign mode
    force_reassign = st.session_state.get("force_reassign", False)

    if barrel and not force_reassign:
        # ----------------------------------------
        # WORKFLOW 2: Record a withdrawal
        # ----------------------------------------
        st.title(barrel["variety"])
        col1, col2 = st.columns(2)
        col1.metric("Date Created", barrel["date_created"])
        col2.metric("Barrel #", barrel["barrel_number"])

        st.markdown("---")

        if st.session_state.get("last_withdrawal_done"):
            st.success("Withdrawal recorded!")
            if st.button("Record Another Withdrawal", use_container_width=True):
                st.session_state["last_withdrawal_done"] = False
                st.rerun()
        else:
            try:
                product_options = get_products(spreadsheet)
            except Exception as e:
                show_error_and_stop(f"Could not load products: {e}")

            with st.form("withdrawal_form"):
                product_type = st.selectbox("Product Type", product_options)
                weight_lbs = st.number_input(
                    "Weight (lbs)", min_value=0.0, step=0.1, format="%.1f"
                )
                notes = st.text_input("Notes (optional)")
                submitted = st.form_submit_button("Record Withdrawal", use_container_width=True)

            if submitted:
                if weight_lbs <= 0:
                    st.warning("Please enter a weight greater than 0.")
                else:
                    try:
                        record_withdrawal(
                            spreadsheet,
                            barrel["barrel_id"],
                            qr_param,
                            product_type,
                            weight_lbs,
                            notes,
                        )
                        st.session_state["last_withdrawal_done"] = True
                        st.balloons()
                        st.rerun()
                    except Exception as e:
                        show_error_and_stop(f"Failed to save withdrawal: {e}")

        st.markdown("---")
        if st.button("Reassign this QR to a different barrel", use_container_width=True):
            st.session_state["force_reassign"] = True
            st.rerun()

    else:
        # ----------------------------------------
        # WORKFLOW 3: Reassign QR to a new barrel
        # ----------------------------------------
        if barrel:
            st.warning(
                f"QR code **{qr_param}** is currently assigned to "
                f"**{barrel['variety']}** (barrel {barrel['barrel_number']}, "
                f"created {barrel['date_created']}). "
                "You are reassigning it to a new barrel."
            )
        else:
            st.warning(
                f"QR code **{qr_param}** is not assigned to any active barrel. "
                "Assign it to a new barrel below."
            )

        st.subheader("Assign to New Barrel")

        try:
            variety_options = get_varieties(spreadsheet)
        except Exception as e:
            show_error_and_stop(f"Could not load varieties: {e}")

        variety = st.selectbox("Coffee Variety", variety_options)
        new_date = st.date_input("Date", value=date.today())
        new_date_str = new_date.strftime("%Y-%m-%d")

        barrel_num = 1
        try:
            barrel_num = get_next_barrel_number(spreadsheet, variety, new_date_str)
        except Exception:
            pass

        st.info(f"Barrel number: **{barrel_num}** (auto-assigned)")

        if st.button("Assign QR to New Barrel", use_container_width=True):
            try:
                new_barrel_id = reassign_qr(
                    spreadsheet,
                    qr_param,
                    variety,
                    new_date_str,
                    int(barrel_num),
                )
                st.success(f"QR code assigned to new barrel **{new_barrel_id}**.")
                st.session_state["force_reassign"] = False
                st.rerun()
            except Exception as e:
                show_error_and_stop(f"Failed to reassign QR code: {e}")

# ==================================================
# WORKFLOW 1: ADMIN PANEL (no ?qr= param)
# ==================================================
else:
    st.title("☕ Coffee Barrel Admin")

    try:
        spreadsheet = get_spreadsheet()
    except Exception as e:
        show_error_and_stop(f"Could not connect to Google Sheets: {e}")

    tab_new, tab_manage = st.tabs(["New Barrel", "Manage Barrels"])

    # --------------------------------------------------
    # Tab: New Barrel
    # --------------------------------------------------
    with tab_new:
        st.subheader("Register a New Barrel & Generate QR")

        try:
            variety_options = get_varieties(spreadsheet)
        except Exception as e:
            show_error_and_stop(f"Could not load varieties: {e}")

        variety = st.selectbox("Coffee Variety", variety_options, key="new_variety")
        new_date = st.date_input("Date", value=date.today(), key="new_date")
        new_date_str = new_date.strftime("%Y-%m-%d")

        barrel_num = 1
        try:
            barrel_num = get_next_barrel_number(spreadsheet, variety, new_date_str)
        except Exception:
            pass

        st.info(f"Barrel number: **{barrel_num}** (auto-assigned)")

        if st.button("Generate QR & Register", use_container_width=True):
            try:
                qr_code_id = generate_unique_qr_id(spreadsheet)
                slug = build_variety_slug(variety)
                num_str = str(int(barrel_num)).zfill(2)
                date_compact = new_date_str.replace("-", "")
                barrel_id = f"{slug}_{date_compact}_{num_str}"

                register_barrel(
                    spreadsheet,
                    barrel_id,
                    variety,
                    new_date_str,
                    int(barrel_num),
                    qr_code_id,
                )

                base_url = st.secrets["app"]["base_url"]
                qr_buf = build_qr_image(qr_code_id, base_url)

                st.session_state["last_registered"] = {
                    "barrel_id": barrel_id,
                    "variety": variety,
                    "date_created": new_date_str,
                    "barrel_number": int(barrel_num),
                    "qr_code_id": qr_code_id,
                    "qr_png": qr_buf.getvalue(),
                    "base_url": base_url,
                }
                st.rerun()
            except Exception as e:
                show_error_and_stop(f"Error registering barrel: {e}")

        # Show result from previous registration (persists across rerun)
        reg = st.session_state.get("last_registered")
        if reg:
            st.success(f"Barrel **{reg['barrel_id']}** registered with QR code **{reg['qr_code_id']}**.")
            st.markdown("**Scan URL:**")
            st.code(f"{reg['base_url'].rstrip('/')}/?qr={reg['qr_code_id']}")
            st.image(reg["qr_png"], caption=f"QR Code: {reg['qr_code_id']}", width=250)
            st.download_button(
                label="Download QR Code PNG",
                data=reg["qr_png"],
                file_name=f"{reg['qr_code_id']}_{reg['barrel_id']}.png",
                mime="image/png",
                use_container_width=True,
            )
            st.markdown("---")
            st.markdown("**Barrel Details**")
            st.json({k: v for k, v in reg.items() if k not in ("qr_png", "base_url")})

    # --------------------------------------------------
    # Tab: Manage Barrels
    # --------------------------------------------------
    with tab_manage:
        st.subheader("All Active Barrels")

        try:
            df = get_barrels_df(spreadsheet)
        except Exception as e:
            show_error_and_stop(f"Could not load barrels: {e}")

        if df.empty:
            st.info("No barrels registered yet.")
        else:
            active_df = df[df["status"] == "active"].copy().reset_index(drop=True)

            variety_filter = st.text_input("Filter by variety", key="filter_variety")
            if variety_filter.strip():
                active_df = active_df[
                    active_df["variety"].str.contains(variety_filter.strip(), case=False, na=False)
                ].reset_index(drop=True)

            display_cols = ["barrel_id", "variety", "date_created", "barrel_number", "qr_code_id", "assigned_date"]
            selection = st.dataframe(
                active_df[display_cols],
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
            )
            st.caption(f"{len(active_df)} active barrel(s) shown.")

            selected_rows = selection.selection.rows
            if selected_rows:
                selected_barrels = active_df.iloc[selected_rows].to_dict("records")
                base_url = st.secrets["app"]["base_url"]
                try:
                    pdf_buf = generate_qr_pdf(selected_barrels, base_url)
                    st.download_button(
                        label=f"Download QR Labels PDF ({len(selected_rows)} selected)",
                        data=pdf_buf,
                        file_name="barrel_qr_labels.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Could not generate PDF: {e}")

    st.markdown("---")
    st.caption(f"v{APP_VERSION}")
