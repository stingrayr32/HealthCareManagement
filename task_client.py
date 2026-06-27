from __future__ import annotations
import uuid
from datetime import datetime
import gspread
import pandas as pd
import streamlit as st
from sheets_client import _get_credentials, SPREADSHEET_ID

BACKLOG_SHEET = "バックログ"
SCHEDULE_SHEET = "スケジュール"

BACKLOG_COLS = ["ID", "タイトル", "詳細", "優先度", "状態", "作成日時"]
SCHEDULE_COLS = ["ID", "タイトル", "詳細", "日付", "開始時刻", "終了時刻", "バックログID", "色"]

PRIORITY_COLOR = {"高": "#e53935", "中": "#FB8C00", "低": "#1976d2"}


def _get_spreadsheet():
    creds = _get_credentials()
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)


def _get_or_create(ss, name, headers):
    try:
        return ss.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=1000, cols=len(headers))
        ws.append_row(headers)
        return ws


@st.cache_data(ttl=30)
def load_backlog() -> pd.DataFrame:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, BACKLOG_SHEET, BACKLOG_COLS)
    records = ws.get_all_values()
    if len(records) <= 1:
        return pd.DataFrame(columns=BACKLOG_COLS)
    df = pd.DataFrame(records[1:], columns=BACKLOG_COLS)
    return df[df["ID"] != ""].reset_index(drop=True)


@st.cache_data(ttl=30)
def load_schedule(date_str: str) -> pd.DataFrame:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, SCHEDULE_SHEET, SCHEDULE_COLS)
    records = ws.get_all_values()
    if len(records) <= 1:
        return pd.DataFrame(columns=SCHEDULE_COLS)
    df = pd.DataFrame(records[1:], columns=SCHEDULE_COLS)
    df = df[df["ID"] != ""]
    return df[df["日付"] == date_str].reset_index(drop=True)


def add_backlog_item(item: dict) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, BACKLOG_SHEET, BACKLOG_COLS)
    item.setdefault("ID", str(uuid.uuid4())[:8])
    item.setdefault("状態", "未着手")
    item.setdefault("優先度", "中")
    item.setdefault("作成日時", datetime.now().strftime("%Y-%m-%d %H:%M"))
    ws.append_row([item.get(c, "") for c in BACKLOG_COLS], value_input_option="USER_ENTERED")
    load_backlog.clear()


def update_backlog_status(item_id: str, status: str) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, BACKLOG_SHEET, BACKLOG_COLS)
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == item_id:
            ws.update_cell(i, BACKLOG_COLS.index("状態") + 1, status)
            break
    load_backlog.clear()


def delete_backlog_item(item_id: str) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, BACKLOG_SHEET, BACKLOG_COLS)
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == item_id:
            ws.delete_rows(i)
            break
    load_backlog.clear()


def add_schedule_event(event: dict) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, SCHEDULE_SHEET, SCHEDULE_COLS)
    event.setdefault("ID", str(uuid.uuid4())[:8])
    event.setdefault("色", "#1976d2")
    ws.append_row([event.get(c, "") for c in SCHEDULE_COLS], value_input_option="USER_ENTERED")
    load_schedule.clear()


def update_schedule_event(event_id: str, updates: dict) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, SCHEDULE_SHEET, SCHEDULE_COLS)
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == event_id:
            for col, val in updates.items():
                if col in SCHEDULE_COLS:
                    ws.update_cell(i, SCHEDULE_COLS.index(col) + 1, str(val))
            break
    load_schedule.clear()


def delete_schedule_event(event_id: str) -> None:
    ss = _get_spreadsheet()
    ws = _get_or_create(ss, SCHEDULE_SHEET, SCHEDULE_COLS)
    for i, row in enumerate(ws.get_all_values()[1:], start=2):
        if row[0] == event_id:
            ws.delete_rows(i)
            break
    load_schedule.clear()
