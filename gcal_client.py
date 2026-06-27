from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import streamlit as st
from google.oauth2.service_account import Credentials
from sheets_client import CREDENTIALS_FILE

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

JST = timezone(timedelta(hours=9))


def _get_credentials():
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    return Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)


def _build_service():
    # lazy import: googleapiclient をモジュール最上部で読み込まず副作用を回避
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=_get_credentials(), cache_discovery=False)


@st.cache_data(ttl=300)
def fetch_events(calendar_id: str, target_date: str) -> list[dict]:
    """指定日のGoogle Calendarイベントを取得する（JST基準）。エラーは呼び出し元に伝播させる。"""
    service = _build_service()
    day = date.fromisoformat(target_date)
    time_min = datetime.combine(day, datetime.min.time()).replace(tzinfo=JST).isoformat()
    time_max = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(tzinfo=JST).isoformat()
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return result.get("items", [])


def to_calendar_events(items: list[dict]) -> list[dict]:
    """Google CalendarイベントをFullCalendar形式に変換（読み取り専用・緑色）"""
    events = []
    for ev in items:
        start = ev.get("start", {})
        end = ev.get("end", {})
        start_dt = start.get("dateTime")
        end_dt = end.get("dateTime")
        if start_dt:
            # 時刻付き予定: タイムゾーンオフセットを除去してローカル時刻として扱う
            start_str = start_dt[:19]
            end_str = end_dt[:19] if end_dt else start_str
        else:
            # 終日予定: timeGridに表示するため終日ブロックに変換
            date_str = start.get("date", "")
            start_str = f"{date_str}T00:00:00"
            end_str = f"{date_str}T23:59:59"
        events.append({
            "id": f"gcal_{ev.get('id', '')}",
            "title": "📅 " + ev.get("summary", "（タイトルなし）"),
            "start": start_str,
            "end": end_str,
            "color": "#0F9D58",
            "editable": False,
        })
    return events


def get_calendar_id() -> str | None:
    """GOOGLE_CALENDAR_ID をシークレットから取得"""
    return st.secrets.get("GOOGLE_CALENDAR_ID", None)
