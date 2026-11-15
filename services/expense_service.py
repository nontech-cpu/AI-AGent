"""
PeculiarAI — Smart Expense & Receipt Tracking Service
Handles:
1. LLM structured extraction from Bill/Receipt OCR and conversational expense text (Zero-Regex).
2. Multi-category expense recording and database persistence.
3. Aggregation and summaries (food, travel, bills, monthly breakdowns).
4. Styled Excel spreadsheet generation (.xlsx) via openpyxl and direct Google Drive cloud sync.
"""
import io
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date

import db
import core.timeutil as timeutil
from timing import IST

logger = logging.getLogger("expense_service")

EXPENSE_CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Travel & Commute",
    "Bills & Utilities",
    "Shopping",
    "Healthcare",
    "Entertainment",
    "Other"
]


async def extract_expense_details(text_or_analysis: str) -> Dict[str, Any]:
    """
    Extracts structured expense details from bill OCR or user message using LLM JSON completion.
    Zero-regex, resilient to varied formats, currencies, and languages.
    """
    prompt = f"""You are an expert financial receipt and expense parsing engine.
Analyze the following text or image OCR analysis and extract expense details strictly into JSON.

Required JSON Structure:
{{
    "is_expense": true, // false if the text has nothing to do with a bill, payment, or expense
    "amount": 450.00, // numeric float, omit currency symbols. Extract the total/final paid amount
    "currency": "INR", // 3-letter currency code, default INR
    "merchant": "Starbucks Coffee", // Name of shop, restaurant, vendor, or receiver. Null if unknown
    "category": "Food & Dining", // One of: "Food & Dining", "Groceries", "Travel & Commute", "Bills & Utilities", "Shopping", "Healthcare", "Entertainment", "Other"
    "date": "YYYY-MM-DD", // Date of bill or transaction. If missing, use null
    "payment_method": "UPI", // UPI, Card, Cash, NetBanking, or Unknown
    "notes": "Short note of items if visible"
}}

Input Text / OCR:
\"\"\"{text_or_analysis[:3500]}\"\"\""""

    try:
        from llm.provider import chat
        raw_res = await chat([
            {"role": "system", "content": "You are a precise financial data extraction assistant. Return JSON only."},
            {"role": "user", "content": prompt}
        ], json_mode=True, temperature=0.1)

        cleaned = (raw_res or "").strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
        data = json.loads(cleaned)
        if isinstance(data, dict) and data.get("is_expense"):
            if not data.get("amount"):
                data["is_expense"] = False
            return data
    except Exception as exc:
        logger.warning(f"Error in extract_expense_details: {exc}")

    return {"is_expense": False}


def record_user_expense(
    chat_id: int,
    amount: float,
    merchant: Optional[str] = None,
    category: Optional[str] = None,
    expense_date: Optional[str] = None,
    payment_method: str = "UPI",
    notes: Optional[str] = None,
    receipt_file_id: Optional[str] = None
) -> Dict[str, Any]:
    """Saves a confirmed expense to database."""
    now_ist = timeutil.now(IST)
    if not expense_date:
        expense_date = now_ist.strftime("%Y-%m-%d")

    rec_id = db.save_user_expense(
        chat_id=chat_id,
        amount=amount,
        merchant=merchant,
        category=category,
        expense_date=expense_date,
        payment_method=payment_method,
        notes=notes,
        receipt_file_id=receipt_file_id
    )

    clean_merch = merchant or "Vendor"
    clean_cat = category or "General"
    db.save_note_or_fact(
        chat_id=chat_id,
        fact=f"Expense Logged: ₹{amount:.2f} at {clean_merch} for {clean_cat} on {expense_date}",
        category="expense"
    )

    return {
        "id": rec_id,
        "amount": amount,
        "merchant": clean_merch,
        "category": clean_cat,
        "date": expense_date,
        "payment_method": payment_method,
        "notes": notes or ""
    }


def query_expense_summary(
    chat_id: int,
    category: Optional[str] = None,
    timeframe: Optional[str] = "this_month",
    month: Optional[int] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    """Queries user expense aggregates for a given category and timeframe."""
    now_ist = timeutil.now(IST)
    start_d = None
    end_d = None

    if month and year:
        start_d = f"{year:04d}-{month:02d}-01"
        import calendar
        _, last_day = calendar.monthrange(year, month)
        end_d = f"{year:04d}-{month:02d}-{last_day:02d}"
    elif timeframe == "today":
        start_d = end_d = now_ist.strftime("%Y-%m-%d")
    elif timeframe in ("this_month", "month"):
        y = now_ist.year
        m = now_ist.month
        start_d = f"{y:04d}-{m:02d}-01"
        import calendar
        _, last_day = calendar.monthrange(y, m)
        end_d = f"{y:04d}-{m:02d}-{last_day:02d}"
    elif timeframe == "last_month":
        m = now_ist.month - 1 or 12
        y = now_ist.year if now_ist.month > 1 else now_ist.year - 1
        start_d = f"{y:04d}-{m:02d}-01"
        import calendar
        _, last_day = calendar.monthrange(y, m)
        end_d = f"{y:04d}-{m:02d}-{last_day:02d}"

    summary = db.get_user_expense_summary(chat_id, category=category, start_date=start_d, end_date=end_d)
    summary["timeframe"] = timeframe
    summary["start_date"] = start_d
    summary["end_date"] = end_d
    return summary


def build_expenses_excel(expenses: List[Dict[str, Any]], title: str = "Expense Report") -> bytes:
    """Builds an executive-styled Excel workbook (.xlsx) using openpyxl."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Expenses"

    # Title Banner
    ws.merge_cells("A1:F1")
    title_cell = ws["A1"]
    title_cell.value = f"📊 {title}"
    title_cell.font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 36

    # Headers
    headers = ["Date", "Merchant / Payee", "Category", "Amount (INR)", "Payment Mode", "Notes"]
    ws.append([])  # Row 2 empty
    ws.append(headers)  # Row 3 headers

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    thin_border = Border(
        left=Side(style='thin', color="CBD5E1"),
        right=Side(style='thin', color="CBD5E1"),
        top=Side(style='thin', color="CBD5E1"),
        bottom=Side(style='thin', color="CBD5E1")
    )

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=3, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws.row_dimensions[3].height = 24

    # Data Rows
    row_idx = 4
    for exp in expenses:
        ws.append([
            exp.get("expense_date", ""),
            exp.get("merchant", "Unknown"),
            exp.get("category", "General"),
            float(exp.get("amount") or 0.0),
            exp.get("payment_method", "UPI"),
            exp.get("notes", "")
        ])
        ws.cell(row=row_idx, column=4).number_format = '₹#,##0.00'
        for col_num in range(1, len(headers) + 1):
            c = ws.cell(row=row_idx, column=col_num)
            c.border = thin_border
            if col_num in (1, 5):
                c.alignment = Alignment(horizontal="center")
            elif col_num == 4:
                c.alignment = Alignment(horizontal="right")
        row_idx += 1

    # Total Summary Row
    if expenses:
        ws.append(["TOTAL", "", "", f"=SUM(D4:D{row_idx-1})", "", ""])
        total_row = row_idx
        ws.merge_cells(f"A{total_row}:C{total_row}")
        tot_label = ws.cell(row=total_row, column=1)
        tot_label.font = Font(name="Calibri", size=11, bold=True, color="0F172A")
        tot_label.alignment = Alignment(horizontal="center")

        tot_val = ws.cell(row=total_row, column=4)
        tot_val.font = Font(name="Calibri", size=12, bold=True, color="0F172A")
        tot_val.number_format = '₹#,##0.00'
        tot_val.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        tot_val.border = thin_border

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row != 1 and cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 5, 14)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


async def sync_expenses_to_drive(
    chat_id: int,
    timeframe: str = "this_month",
    folder_name: str = "Expenses"
) -> Dict[str, Any]:
    """Generates an Excel spreadsheet of user expenses and uploads directly to Google Drive."""
    summary = query_expense_summary(chat_id, timeframe=timeframe)
    expenses = db.list_user_expenses(
        chat_id,
        start_date=summary.get("start_date"),
        end_date=summary.get("end_date"),
        limit=500
    )

    now_ist = timeutil.now(IST)
    month_name = now_ist.strftime("%B_%Y")
    filename = f"Expenses_{month_name}.xlsx"

    excel_bytes = build_expenses_excel(expenses, title=f"Expense Report — {now_ist.strftime('%B %Y')}")

    import google_services
    upload_res = await google_services.upload_file_to_drive(
        chat_id=chat_id,
        filename=filename,
        file_bytes=excel_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        folder_name=folder_name
    )

    return {
        "ok": True,
        "filename": filename,
        "folder_name": folder_name,
        "file_id": upload_res.get("id"),
        "webViewLink": upload_res.get("webViewLink", ""),
        "total_amount": summary.get("total_amount", 0.0),
        "count": len(expenses)
    }
