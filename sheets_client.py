from __future__ import annotations

import json
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import streamlit as st

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = "1zfuPXxXN3nvaIsxo__4kWtTx7Pm3FFgxPSySULpMGXs"
SHEET_NAME = "食事・体重管理シート"
CREDENTIALS_FILE = "healthcaremanagement-500614-fdb21eb0a46d.json"

COLUMNS = [
    "日付", "体重", "体脂肪", "運動の有無", "歩数",
    "総カロリー", "総タンパク質", "総脂質", "総炭水化物",
    "朝食Cal", "昼食Cal", "夕食Cal", "食事内容", "メモ"
]


def _get_credentials():
    # クラウド環境: Streamlit Secrets の [gcp_service_account] セクションから読み込む
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    # ローカル環境: JSONファイルから読み込む
    return Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)


def _get_sheet():
    creds = _get_credentials()
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    sheet = _get_sheet()
    records = sheet.get_all_values()

    if len(records) <= 1:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(records[1:], columns=COLUMNS[:len(records[0])])
    df = df[df["日付"].notna() & (df["日付"] != "")]
    df["日付"] = pd.to_datetime(df["日付"], errors="coerce")

    numeric_cols = [
        "体重", "体脂肪", "歩数", "総カロリー",
        "総タンパク質", "総脂質", "総炭水化物",
        "朝食Cal", "昼食Cal", "夕食Cal"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["日付"]).sort_values("日付").reset_index(drop=True)


def find_row_by_date(date_str: str) -> tuple[int | None, dict | None]:
    """日付文字列(YYYY-MM-DD)でシートを検索し、(行番号, 既存データdict) を返す。
    見つからない場合は (None, None)。行番号は1始まり（ヘッダー=1行目）。"""
    sheet = _get_sheet()
    records = sheet.get_all_values()
    if len(records) <= 1:
        return None, None

    # 比較用に日付を正規化（YYYY/MM/DD と YYYY-MM-DD 両対応）
    target = date_str  # YYYY-MM-DD
    for i, row in enumerate(records[1:], start=2):  # i はシート上の行番号
        cell_date = row[0].strip() if row else ""
        try:
            normalized = pd.to_datetime(cell_date).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized == target:
            row_dict = {}
            for j, col in enumerate(COLUMNS):
                row_dict[col] = row[j] if j < len(row) else ""
            return i, row_dict

    return None, None


def append_row(row_data: dict) -> None:
    sheet = _get_sheet()
    row = [str(row_data.get(col, "")) for col in COLUMNS]
    sheet.append_row(row, value_input_option="USER_ENTERED")


def update_row(sheet_row_index: int, row_data: dict) -> None:
    """指定した行番号（1始まり）の行をまるごと更新する。"""
    sheet = _get_sheet()
    row = [str(row_data.get(col, "")) for col in COLUMNS]
    sheet.update(f"A{sheet_row_index}", [row], value_input_option="USER_ENTERED")
