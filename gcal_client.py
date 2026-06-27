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
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=_get_credentials(), cache_discovery=False)


@st.cache_data(ttl=300)
def fetch_events(calendar_id: str, target_date: str) -> list[dict]:
    """指定日(JST)のGoogle Calendarイベントを取得する。"""
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


def to_timed_calendar_events(items: list[dict]) -> list[dict]:
    """時刻付き予定のみFullCalendar形式に変換（終日予定は除外）"""
    events = []
    for ev in items:
        start = ev.get("start", {})
        end = ev.get("end", {})
        start_dt = start.get("dateTime")
        end_dt = end.get("dateTime")
        if not start_dt:
            continue  # 終日予定はスキップ（カレンダーグリッドに入れない）
        events.append({
            "id": f"gcal_{ev.get('id', '')}",
            "title": "📅 " + ev.get("summary", "（タイトルなし）"),
            "start": start_dt[:19],
            "end": end_dt[:19] if end_dt else start_dt[:19],
            "color": "#0F9D58",
            "editable": False,
        })
    return events


def get_allday_titles(items: list[dict]) -> list[str]:
    """終日予定のタイトル一覧を返す（カレンダー上部リスト表示用）"""
    return [
        ev.get("summary", "（タイトルなし）")
        for ev in items
        if not ev.get("start", {}).get("dateTime")
    ]


def get_calendar_id() -> str | None:
    return st.secrets.get("GOOGLE_CALENDAR_ID", None)
