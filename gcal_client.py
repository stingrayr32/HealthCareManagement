from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from sheets_client import CREDENTIALS_FILE

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

JST = timezone(timedelta(hours=9))


def _get_credentials():
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    return Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)


def _build_service():
    return build("calendar", "v3", credentials=_get_credentials(), cache_discovery=False)


@st.cache_data(ttl=300)
def fetch_events(calendar_id: str, target_date: str) -> list[dict]:
    """指定日のGoogle Calendarイベントを取得する（JST基準）"""
    try:
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
    except Exception as e:
        return []


def to_calendar_events(items: list[dict]) -> list[dict]:
    """Google CalendarイベントをFullCalendar形式に変換（読み取り専用・緑色）"""
    events = []
    for ev in items:
        start = ev.get("start", {})
        end = ev.get("end", {})
        start_str = start.get("dateTime", start.get("date", ""))
        end_str = end.get("dateTime", end.get("date", ""))
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
    """GOOGLE_CALENDAR_ID をシークレットまたは環境から取得"""
    return st.secrets.get("GOOGLE_CALENDAR_ID", None)
