import json
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import time as dt_time, datetime, timezone, timedelta

_JST = timezone(timedelta(hours=9))
from sheets_client import load_data, append_row, find_row_by_date, update_row
from llm_parser import parse_health_input
from task_client import (
    load_backlog, load_schedule, add_backlog_item,
    update_backlog_status, delete_backlog_item,
    add_schedule_event, delete_schedule_event,
    PRIORITY_COLOR,
)
from task_parser import parse_task_input
try:
    from gcal_client import fetch_events, get_allday_titles, get_calendar_id
    _gcal_available = True
except Exception:
    _gcal_available = False
    def fetch_events(*a, **kw): return []
    def get_allday_titles(*a, **kw): return []
    def get_calendar_id(): return None

st.set_page_config(
    page_title="My Life Dashboard",
    page_icon="🌟",
    layout="wide",
)

HEIGHT_M = 1.71
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "target_weight": round(22.0 * HEIGHT_M ** 2, 1),
    "cal_min": 1600,
    "cal_max": 2160,
    "protein_min": 60,
    "protein_max": 120,
    "fat_min": 40,
    "fat_max": 80,
    "carb_min": 150,
    "carb_max": 300,
}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


APP_VERSION = "1.6.0"

if "config" not in st.session_state:
    st.session_state.config = load_config()
if "save_success" not in st.session_state:
    st.session_state.save_success = False


# =====================
# ダイアログ定義
# =====================
@st.dialog("🎯 目標体重の設定")
def weight_dialog():
    cfg = st.session_state.config
    new_weight = st.number_input(
        "目標体重 (kg)", min_value=40.0, max_value=120.0,
        value=float(cfg["target_weight"]), step=0.1, format="%.1f",
    )
    bmi = new_weight / HEIGHT_M ** 2
    if bmi < 18.5:
        label = "⚠️ 低体重域"
    elif bmi < 25.0:
        label = "✅ 普通体重域"
    elif bmi < 30.0:
        label = "⚠️ 過体重域"
    else:
        label = "⚠️ 肥満域"
    st.caption(f"BMI: {bmi:.1f}　{label}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", use_container_width=True):
            cfg["target_weight"] = new_weight
            save_config(cfg)
            st.session_state.config = cfg
            st.rerun()
    with c2:
        if st.button("キャンセル", use_container_width=True):
            st.rerun()


@st.dialog("🍽️ 摂取目標の設定")
def intake_dialog():
    cfg = st.session_state.config

    def range_row(label, key_min, key_max, default_min, default_max, max_val, step):
        st.markdown(f"**{label}**")
        c1, c2, c3 = st.columns([5, 1, 5])
        with c1:
            v_min = st.number_input("下限", min_value=0, max_value=max_val, value=int(cfg.get(default_min, 0)),
                                    step=step, key=key_min, label_visibility="collapsed")
        with c2:
            st.markdown("<div style='text-align:center;padding-top:8px'>〜</div>", unsafe_allow_html=True)
        with c3:
            v_max = st.number_input("上限", min_value=0, max_value=max_val, value=int(cfg.get(default_max, 0)),
                                    step=step, key=key_max, label_visibility="collapsed")
        st.caption(f"下限 {v_min} 〜 上限 {v_max}")
        return v_min, v_max

    cal_min, cal_max         = range_row("カロリー (kcal)", "d_cal_min", "d_cal_max", "cal_min", "cal_max", 5000, 50)
    protein_min, protein_max = range_row("タンパク質 (g)", "d_p_min",   "d_p_max",   "protein_min", "protein_max", 300, 5)
    fat_min, fat_max         = range_row("脂質 (g)",       "d_f_min",   "d_f_max",   "fat_min", "fat_max", 300, 5)
    carb_min, carb_max       = range_row("炭水化物 (g)",   "d_c_min",   "d_c_max",   "carb_min", "carb_max", 500, 10)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("保存", type="primary", use_container_width=True):
            cfg.update({
                "cal_min": cal_min, "cal_max": cal_max,
                "protein_min": protein_min, "protein_max": protein_max,
                "fat_min": fat_min, "fat_max": fat_max,
                "carb_min": carb_min, "carb_max": carb_max,
            })
            save_config(cfg)
            st.session_state.config = cfg
            st.rerun()
    with c2:
        if st.button("キャンセル", use_container_width=True):
            st.rerun()


# =====================
# サイドバー
# =====================
with st.sidebar:
    st.header("📅 期間フィルター")
    filter_days = st.selectbox(
        "表示期間",
        options=[0, 7, 14, 30, 60, 90, 180, 365],
        index=0,
        format_func=lambda x: "全期間" if x == 0 else f"直近 {x} 日間",
    )
    if st.button("🔄 データを再読み込み"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    cfg = st.session_state.config
    st.caption(f"目標体重: **{cfg['target_weight']} kg**")
    if st.button("🎯 目標体重を設定", use_container_width=True):
        weight_dialog()

    st.divider()
    st.caption(
        f"カロリー: **{cfg['cal_min']}〜{cfg['cal_max']} kcal**  \n"
        f"P: **{cfg['protein_min']}〜{cfg['protein_max']} g**  \n"
        f"F: **{cfg.get('fat_min', 40)}〜{cfg.get('fat_max', 80)} g**  \n"
        f"C: **{cfg.get('carb_min', 150)}〜{cfg.get('carb_max', 300)} g**"
    )
    if st.button("🍽️ 摂取目標を設定", use_container_width=True):
        intake_dialog()

    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")

st.title("🌟 My Life Dashboard")

tab_dashboard, tab_tasks, tab_info = st.tabs(["🏃 Health", "📋 ToDo", "ℹ️ バージョン情報"])

# =====================
# ダッシュボードタブ
# =====================
with tab_dashboard:
    try:
        df = load_data()
    except Exception as e:
        st.error(f"データの読み込みに失敗しました。\n\n詳細: {e}")
        df = None

    if df is None or df.empty:
        st.warning("スプレッドシートにデータがありません。")
    else:
        cfg = st.session_state.config
        target_weight = float(cfg["target_weight"])
        cal_min = int(cfg["cal_min"])
        cal_max = int(cfg["cal_max"])
        protein_min = int(cfg["protein_min"])
        protein_max = int(cfg["protein_max"])
        fat_min = int(cfg.get("fat_min", 40))
        fat_max = int(cfg.get("fat_max", 80))
        carb_min = int(cfg.get("carb_min", 150))
        carb_max = int(cfg.get("carb_max", 300))
        target_bmi = target_weight / HEIGHT_M ** 2

        if filter_days == 0:
            df_filtered = df.copy()
        else:
            cutoff = df["日付"].max() - pd.Timedelta(days=filter_days)
            df_filtered = df[df["日付"] >= cutoff].copy()

        if df_filtered.empty:
            st.warning("選択した期間にデータがありません。期間を広げてください。")
        else:
            latest = df_filtered.iloc[-1]
            first = df_filtered.iloc[0]

            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                diff_weight = latest["体重"] - first["体重"] if pd.notna(latest["体重"]) and pd.notna(first["体重"]) else None
                delta = f"{diff_weight:+.1f} kg" if diff_weight is not None else None
                st.metric("最新体重", f"{latest['体重']:.1f} kg" if pd.notna(latest["体重"]) else "N/A", delta=delta, delta_color="inverse")
            with col2:
                if pd.notna(latest["体重"]):
                    bmi = latest["体重"] / (HEIGHT_M ** 2)
                    bmi_label = "低体重" if bmi < 18.5 else "普通体重" if bmi < 25 else "過体重" if bmi < 30 else "肥満"
                    st.metric("BMI", f"{bmi:.1f}", delta=bmi_label, delta_color="off")
                else:
                    st.metric("BMI", "N/A")
            with col3:
                diff_fat = latest["体脂肪"] - first["体脂肪"] if pd.notna(latest["体脂肪"]) and pd.notna(first["体脂肪"]) else None
                delta_fat = f"{diff_fat:+.1f} %" if diff_fat is not None else None
                st.metric("最新体脂肪率", f"{latest['体脂肪']:.1f} %" if pd.notna(latest["体脂肪"]) else "N/A", delta=delta_fat, delta_color="inverse")
            with col4:
                avg_cal = df_filtered["総カロリー"].mean()
                st.metric("平均カロリー", f"{avg_cal:.0f} kcal" if pd.notna(avg_cal) else "N/A")
            with col5:
                exercise_days = df_filtered["運動の有無"].str.strip().str.startswith("あり").sum()
                st.metric("運動日数", f"{exercise_days} 日 / {len(df_filtered)} 日")
            with col6:
                if pd.notna(latest["体重"]) and pd.notna(latest["体脂肪"]):
                    fat_kg = latest["体重"] * latest["体脂肪"] / 100
                    lean_kg = latest["体重"] - fat_kg
                    diff_fat_kg = None
                    if pd.notna(first["体重"]) and pd.notna(first["体脂肪"]):
                        fat_kg_first = first["体重"] * first["体脂肪"] / 100
                        diff_fat_kg = fat_kg - fat_kg_first
                    delta_fat_kg = f"{diff_fat_kg:+.1f} kg" if diff_fat_kg is not None else None
                    st.metric("脂肪量", f"{fat_kg:.1f} kg", delta=delta_fat_kg, delta_color="inverse",
                              help=f"除脂肪体重（筋肉・骨など）: {lean_kg:.1f} kg")
                else:
                    st.metric("脂肪量", "N/A")

            st.divider()

            # --- グラフ1: 体重・体脂肪の推移 ---
            weight_data = df_filtered[["日付", "体重"]].dropna()
            pred_dates = []
            pred_y = []
            target_date = None

            if len(weight_data) >= 2:
                origin = weight_data["日付"].iloc[0]
                x_days = (weight_data["日付"] - origin).dt.days.values.astype(float)
                y_vals = weight_data["体重"].values.astype(float)
                slope, intercept = np.polyfit(x_days, y_vals, 1)
                if slope < 0:
                    days_to_target = (target_weight - intercept) / slope
                    future_days = np.arange(0, int(days_to_target) + 1, 1)
                    pred_dates = [origin + pd.Timedelta(days=int(d)) for d in future_days]
                    pred_y = (slope * future_days + intercept).tolist()
                    target_date = origin + pd.Timedelta(days=int(days_to_target))

            st.subheader("📈 体重・体脂肪の推移")
            if target_date:
                st.caption(f"目標体重: **{target_weight} kg**（BMI {target_bmi:.1f}）　予測到達日: **{target_date.strftime('%Y/%m/%d')}**")

            fat_mass = (df_filtered["体重"] * df_filtered["体脂肪"] / 100).where(
                df_filtered["体重"].notna() & df_filtered["体脂肪"].notna()
            )

            fig_weight = make_subplots(specs=[[{"secondary_y": True}]])
            fig_weight.add_trace(
                go.Scatter(x=df_filtered["日付"], y=df_filtered["体重"], name="体重 (kg)",
                           line=dict(color="#2196F3", width=2), mode="lines+markers"),
                secondary_y=False,
            )
            fig_weight.add_trace(
                go.Scatter(x=df_filtered["日付"], y=fat_mass, name="脂肪量 (kg)",
                           line=dict(color="#FF9800", width=2, dash="dot"), mode="lines+markers",
                           visible="legendonly"),
                secondary_y=False,
            )
            fig_weight.add_trace(
                go.Scatter(x=df_filtered["日付"], y=df_filtered["体脂肪"], name="体脂肪率 (%)",
                           line=dict(color="#FF5722", width=2, dash="dot"), mode="lines+markers"),
                secondary_y=True,
            )
            if pred_dates:
                fig_weight.add_trace(
                    go.Scatter(x=pred_dates, y=pred_y, name="予測トレンド",
                               line=dict(color="#4CAF50", width=2, dash="dash"), mode="lines"),
                    secondary_y=False,
                )
                fig_weight.add_hline(
                    y=target_weight, line_dash="dot", line_color="#4CAF50", line_width=1.5,
                    annotation_text=f"目標 {target_weight} kg",
                    annotation_position="bottom right",
                    annotation_font_color="#4CAF50",
                    secondary_y=False,
                )
            fig_weight.update_layout(height=400, hovermode="x unified", legend=dict(orientation="h", y=1.1))
            fig_weight.update_yaxes(title_text="体重 (kg)", secondary_y=False)
            fig_weight.update_yaxes(title_text="体脂肪率 (%)", secondary_y=True)
            st.plotly_chart(fig_weight, use_container_width=True)

            # --- 本日のカロリー・PFC ---
            def _colored_card(label, value_str, color, status):
                return f"""<div style="text-align:center;padding:4px 2px">
<div style="font-size:0.75em;color:#888;margin-bottom:2px">{label}</div>
<div style="font-size:1.3em;font-weight:bold;color:{color}">{value_str}</div>
<div style="font-size:0.72em;color:{color};margin-top:1px">{status}</div>
</div>"""

            today_ts = pd.Timestamp.today().normalize()
            today_rows = df[df["日付"] == today_ts]
            if not today_rows.empty:
                t = today_rows.iloc[-1]
                st.subheader("🗓️ 本日の摂取状況")
                tc1, tc2, tc3, tc4, tc5, tc6, tc7 = st.columns(7)

                def cal_color(val):
                    if val is None:
                        return "gray", "-", "未記録"
                    if val < cal_min:
                        return "#e53935", f"{val:.0f}", f"不足 ({val - cal_min:.0f})"
                    if val > cal_max:
                        return "#e53935", f"{val:.0f}", f"超過 (+{val - cal_max:.0f})"
                    return "#43a047", f"{val:.0f}", "目標内 ✓"

                def protein_color(val):
                    if val is None:
                        return "gray", "-", "未記録"
                    if val < protein_min:
                        return "#e53935", f"{val:.0f}", f"不足 ({val - protein_min:.0f})"
                    if val > protein_max:
                        return "#e53935", f"{val:.0f}", f"超過 (+{val - protein_max:.0f})"
                    return "#43a047", f"{val:.0f}", "目標内 ✓"

                def range_color(val, v_min, v_max):
                    if val is None:
                        return "gray", "-", "未記録"
                    if val < v_min:
                        return "#e53935", f"{val:.0f}", f"不足 ({val - v_min:.0f})"
                    if val > v_max:
                        return "#e53935", f"{val:.0f}", f"超過 (+{val - v_max:.0f})"
                    return "#43a047", f"{val:.0f}", "目標内 ✓"

                cal_val = t["総カロリー"] if pd.notna(t["総カロリー"]) else None
                c, v, s = cal_color(cal_val)
                with tc1:
                    st.markdown(_colored_card("総Cal (kcal)", v, c, s), unsafe_allow_html=True)
                with tc2:
                    v2 = f"{t['朝食Cal']:.0f}" if pd.notna(t["朝食Cal"]) else "-"
                    st.markdown(_colored_card("朝食 (kcal)", v2, "#1976d2", ""), unsafe_allow_html=True)
                with tc3:
                    v3 = f"{t['昼食Cal']:.0f}" if pd.notna(t["昼食Cal"]) else "-"
                    st.markdown(_colored_card("昼食 (kcal)", v3, "#1976d2", ""), unsafe_allow_html=True)
                with tc4:
                    v4 = f"{t['夕食Cal']:.0f}" if pd.notna(t["夕食Cal"]) else "-"
                    st.markdown(_colored_card("夕食 (kcal)", v4, "#1976d2", ""), unsafe_allow_html=True)
                p_val = t["総タンパク質"] if pd.notna(t["総タンパク質"]) else None
                cp, vp, sp = protein_color(p_val)
                with tc5:
                    st.markdown(_colored_card("タンパク質 (g)", vp, cp, sp), unsafe_allow_html=True)
                f_val = t["総脂質"] if pd.notna(t["総脂質"]) else None
                cf, vf, sf = range_color(f_val, fat_min, fat_max)
                with tc6:
                    st.markdown(_colored_card("脂質 (g)", vf, cf, sf), unsafe_allow_html=True)
                c_val = t["総炭水化物"] if pd.notna(t["総炭水化物"]) else None
                cc, vc, sc = range_color(c_val, carb_min, carb_max)
                with tc7:
                    st.markdown(_colored_card("炭水化物 (g)", vc, cc, sc), unsafe_allow_html=True)

            # --- グラフ2: カロリー摂取の内訳 ---
            st.subheader("🍽️ カロリー摂取の推移（朝・昼・夕）")
            fig_cal = go.Figure()
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["朝食Cal"], name="朝食", marker_color="#FFB74D"))
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["昼食Cal"], name="昼食", marker_color="#4DB6AC"))
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["夕食Cal"], name="夕食", marker_color="#7986CB"))
            fig_cal.add_hline(
                y=cal_max, line_dash="dot", line_color="red", line_width=2,
                annotation_text=f"上限 {cal_max} kcal",
                annotation_position="top right", annotation_font_color="red",
            )
            fig_cal.update_layout(barmode="stack", height=350, yaxis_title="カロリー (kcal)",
                                  hovermode="x unified", legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_cal, use_container_width=True)

            # --- グラフ3: PFCバランス & 歩数 ---
            col_left, col_right = st.columns(2)
            with col_left:
                st.subheader("🥗 平均PFCバランス")
                avg_p = df_filtered["総タンパク質"].mean()
                avg_f = df_filtered["総脂質"].mean()
                avg_c = df_filtered["総炭水化物"].mean()
                if pd.notna(avg_p) and pd.notna(avg_f) and pd.notna(avg_c):
                    fig_pfc = go.Figure(data=[go.Pie(
                        labels=["タンパク質 (P)", "脂質 (F)", "炭水化物 (C)"],
                        values=[avg_p * 4, avg_f * 9, avg_c * 4],
                        hole=0.4,
                        marker_colors=["#66BB6A", "#EF5350", "#FFA726"],
                    )])
                    fig_pfc.update_layout(height=350, showlegend=True, legend=dict(orientation="h", y=-0.1))
                    st.plotly_chart(fig_pfc, use_container_width=True)
                else:
                    st.info("PFCデータが不足しています。")

            with col_right:
                st.subheader("🚶 歩数の推移")
                fig_steps = px.bar(df_filtered, x="日付", y="歩数", color_discrete_sequence=["#AB47BC"])
                fig_steps.add_hline(y=8000, line_dash="dash", line_color="gray", annotation_text="目標: 8,000歩")
                fig_steps.update_layout(height=350, yaxis_title="歩数")
                st.plotly_chart(fig_steps, use_container_width=True)

            st.divider()

            # --- テーブル: 食事内容・メモ ---
            st.subheader("📋 食事内容・メモ一覧")
            display_cols = ["日付", "食事内容", "メモ", "総カロリー"]
            df_table = df_filtered[display_cols].copy()
            df_table["日付"] = df_table["日付"].dt.strftime("%Y/%m/%d")
            df_table = df_table.iloc[::-1].reset_index(drop=True)
            st.dataframe(df_table, use_container_width=True, height=300)

            st.caption("データは5分間キャッシュされます。最新データを反映するにはサイドバーの「再読み込み」ボタンを押してください。")

    # ─── 今日の記録を入力 ───
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "parsed_data" not in st.session_state:
        st.session_state.parsed_data = None
    if "existing_row_index" not in st.session_state:
        st.session_state.existing_row_index = None

    if st.session_state.save_success:
        st.toast("スプレッドシートに保存しました！", icon="✅")
        st.session_state.save_success = False

    st.divider()
    st.subheader("📝 今日の記録を入力")
    st.markdown(
        "体重・体脂肪・朝食・昼食・夕食・飲酒・運動の内容を自由に入力してください。"
        "Claudeがカロリー・PFCを推定し、スプレッドシートに記録します。"
    )

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if st.session_state.parsed_data is not None:
        col_save, col_cancel = st.columns([1, 5])
        with col_save:
            if st.button("✅ スプレッドシートに保存", type="primary"):
                try:
                    p = st.session_state.parsed_data
                    row = {
                        "日付": p.get("日付", ""),
                        "体重": p.get("体重", ""),
                        "体脂肪": p.get("体脂肪", ""),
                        "運動の有無": p.get("運動の有無", ""),
                        "歩数": p.get("歩数", ""),
                        "総カロリー": p.get("総カロリー", ""),
                        "総タンパク質": p.get("総タンパク質", ""),
                        "総脂質": p.get("総脂質", ""),
                        "総炭水化物": p.get("総炭水化物", ""),
                        "朝食Cal": p.get("朝食Cal", ""),
                        "昼食Cal": p.get("昼食Cal", ""),
                        "夕食Cal": p.get("夕食Cal", ""),
                        "食事内容": p.get("食事内容", ""),
                        "メモ": p.get("メモ", ""),
                    }
                    existing_idx = st.session_state.get("existing_row_index")
                    if existing_idx:
                        update_row(existing_idx, row)
                    else:
                        append_row(row)
                    st.session_state.parsed_data = None
                    st.session_state.existing_row_index = None
                    st.session_state.save_success = True
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"保存に失敗しました: {e}")
        with col_cancel:
            if st.button("❌ キャンセル"):
                st.session_state.parsed_data = None
                st.session_state.existing_row_index = None
                st.rerun()

    user_input = st.chat_input("例：体重73.5kg、体脂肪26%、朝はご飯とみそ汁、昼はラーメン、夜は焼き肉、ビール1杯、スクワット10分")

    if user_input:
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        if not api_key:
            with st.chat_message("assistant"):
                st.error("Anthropic API キーが設定されていません。")
        else:
            with st.chat_message("assistant"):
                with st.spinner("Claudeが解析中..."):
                    try:
                        today_str = datetime.now(_JST).strftime("%Y-%m-%d")
                        existing_row_index, existing_data = find_row_by_date(today_str)
                        parsed = parse_health_input(
                            user_input, api_key, today_str,
                            existing_data=existing_data,
                        )
                        st.session_state.parsed_data = parsed
                        st.session_state.existing_row_index = existing_row_index

                        preview = {
                            "日付": parsed.get("日付", ""),
                            "体重 (kg)": parsed.get("体重", ""),
                            "体脂肪率 (%)": parsed.get("体脂肪", ""),
                            "運動": parsed.get("運動の有無", ""),
                            "歩数": parsed.get("歩数", ""),
                            "朝食Cal": parsed.get("朝食Cal", ""),
                            "昼食Cal": parsed.get("昼食Cal", ""),
                            "夕食Cal": parsed.get("夕食Cal", ""),
                            "総カロリー": parsed.get("総カロリー", ""),
                            "P (g)": parsed.get("総タンパク質", ""),
                            "F (g)": parsed.get("総脂質", ""),
                            "C (g)": parsed.get("総炭水化物", ""),
                            "食事内容": parsed.get("食事内容", ""),
                            "メモ": parsed.get("メモ", ""),
                        }
                        comment = parsed.get("comment", "")
                        if comment:
                            st.markdown(comment)
                        if existing_row_index:
                            st.info("本日のデータが既に存在します。既存データとマージした結果を表示しています。")
                        st.markdown("以下の内容を確認して「保存」ボタンを押してください。")
                        st.dataframe(
                            pd.DataFrame([preview]).T.rename(columns={0: "値"}),
                            use_container_width=True,
                        )
                    except Exception as e:
                        st.error(f"解析に失敗しました: {e}")
                        st.session_state.parsed_data = None
                        st.session_state.existing_row_index = None

# =====================
# タスク管理タブ
# =====================
with tab_tasks:
    today_str = datetime.now(_JST).date().isoformat()

    if "task_messages" not in st.session_state:
        st.session_state.task_messages = []
    if "pending_task" not in st.session_state:
        st.session_state.pending_task = None
    if "scheduling_item" not in st.session_state:
        st.session_state.scheduling_item = None
    if "clicked_event_id" not in st.session_state:
        st.session_state.clicked_event_id = None
    if "clicked_event_title" not in st.session_state:
        st.session_state.clicked_event_title = None

    # カレンダーとバックログを独立して扱う（一方が失敗しても他方を表示）
    try:
        schedule_df = load_schedule(today_str)
        schedule_load_ok = True
    except Exception as e:
        schedule_df = None
        schedule_load_ok = False
        schedule_error = str(e)

    try:
        backlog_df = load_backlog()
        backlog_load_ok = True
    except Exception as e:
        backlog_df = None
        backlog_load_ok = False
        backlog_error = str(e)

    # ローカルスケジュール → カード用リスト
    local_events = []
    if schedule_load_ok:
        for _, row in schedule_df.iterrows():
            if row["開始時刻"] and row["終了時刻"]:
                local_events.append({
                    "source": "local",
                    "id": row["ID"],
                    "title": row["タイトル"],
                    "detail": row.get("詳細", ""),
                    "start": row["開始時刻"],
                    "end": row["終了時刻"],
                    "color": row.get("色", "#1976d2"),
                })

    # Google Calendar → 終日リスト + 時刻付きカード用リスト
    gcal_id = get_calendar_id()
    gcal_error = None
    gcal_allday = []
    gcal_timed = []
    if gcal_id:
        try:
            gcal_items = fetch_events(gcal_id, today_str)
            gcal_allday = get_allday_titles(gcal_items)
            for ev in gcal_items:
                start_dt = ev.get("start", {}).get("dateTime")
                end_dt   = ev.get("end",   {}).get("dateTime")
                if start_dt:
                    gcal_timed.append({
                        "source": "gcal",
                        "id": f"gcal_{ev.get('id', '')}",
                        "title": ev.get("summary", "（タイトルなし）"),
                        "detail": ev.get("description", ""),
                        "start": start_dt[11:16],
                        "end":   end_dt[11:16] if end_dt else start_dt[11:16],
                        "color": "#0F9D58",
                    })
        except Exception as e:
            gcal_error = str(e)

    all_events = sorted(local_events + gcal_timed, key=lambda x: x["start"])

    left_col, right_col = st.columns([6, 5], gap="medium")

    with left_col:
        # ヘッダー
        date_label = datetime.now(_JST).strftime("%-m月%-d日 (%a)")
        gcal_badge = "　🟢 Google Calendar連携中" if (gcal_id and not gcal_error) else ""
        st.markdown(f"### 📅 {date_label}{gcal_badge}")

        if not schedule_load_ok:
            st.warning(f"スケジュールデータの読み込みに失敗しました: {schedule_error}")
        if gcal_error:
            st.warning(f"Google Calendar 取得エラー: {gcal_error}")

        # 終日予定バナー
        if gcal_allday:
            st.info("📅 終日予定: " + "　/　".join(gcal_allday))

        # ─── スケジュールカード ───
        if not all_events:
            st.markdown(
                '<div style="text-align:center;color:#a0aec0;padding:40px 0;font-size:0.9rem;">本日の予定はありません</div>',
                unsafe_allow_html=True,
            )
        else:
            for ev in all_events:
                done_key = f"sched_done_{ev['id']}"
                if done_key not in st.session_state:
                    st.session_state[done_key] = False
                is_done = st.session_state[done_key]

                if is_done:
                    bg, border_color, title_style, time_style = (
                        "#f4f4f4", "#cbd5e0",
                        "color:#b0b8c1;text-decoration:line-through;",
                        "color:#c0c8d0;",
                    )
                    icon = "✅"
                else:
                    bg           = "#f0fdf6" if ev["source"] == "gcal" else "#f5f7ff"
                    border_color = ev["color"]
                    title_style  = "color:#2d3748;"
                    time_style   = "color:#718096;"
                    icon         = "📅" if ev["source"] == "gcal" else "📌"

                detail_html = (
                    f'<p style="margin:3px 0 0;font-size:0.78rem;{time_style}">{ev["detail"]}</p>'
                    if ev.get("detail") and not is_done else ""
                )
                card_html = f"""
<div style="
    border-left:4px solid {border_color};
    background:{bg};
    border-radius:0 10px 10px 0;
    padding:10px 14px;
    margin:5px 0;
    box-shadow:0 1px 4px rgba(0,0,0,0.04);
    transition:all .2s;
">
  <span style="font-size:0.72rem;font-weight:500;letter-spacing:.3px;{time_style}">
    {ev['start']} – {ev['end']}
  </span>
  <p style="margin:3px 0 0;font-size:0.88rem;font-weight:600;{title_style}">
    {icon} {ev['title']}
  </p>
  {detail_html}
</div>"""

                if ev["source"] == "local":
                    c_chk, c_card, c_btn = st.columns([1, 10, 1])
                    with c_chk:
                        st.markdown("<div style='padding-top:16px'>", unsafe_allow_html=True)
                        st.checkbox("", key=done_key, label_visibility="collapsed")
                        st.markdown("</div>", unsafe_allow_html=True)
                    with c_card:
                        st.markdown(card_html, unsafe_allow_html=True)
                    with c_btn:
                        st.markdown("<div style='padding-top:14px'>", unsafe_allow_html=True)
                        if st.button("🗑", key=f"del_sched_{ev['id']}", help="削除"):
                            delete_schedule_event(ev["id"])
                            load_schedule.clear()
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)
                else:
                    c_chk, c_card = st.columns([1, 11])
                    with c_chk:
                        st.markdown("<div style='padding-top:16px'>", unsafe_allow_html=True)
                        st.checkbox("", key=done_key, label_visibility="collapsed")
                        st.markdown("</div>", unsafe_allow_html=True)
                    with c_card:
                        st.markdown(card_html, unsafe_allow_html=True)

        # ─── スケジュール追加フォーム ───
        st.markdown("<div style='margin-top:12px'>", unsafe_allow_html=True)
        with st.expander("➕ スケジュールに追加"):
            with st.form("add_sched_form", clear_on_submit=True):
                sched_title = st.text_input("タイトル", placeholder="例：数学の授業")
                cs, ce = st.columns(2)
                with cs:
                    sched_start = st.time_input("開始", value=dt_time(9, 0), step=1800)
                with ce:
                    sched_end = st.time_input("終了", value=dt_time(10, 0), step=1800)
                sched_detail = st.text_input("詳細（任意）", placeholder="")
                if st.form_submit_button("追加する", use_container_width=True):
                    if sched_title:
                        add_schedule_event({
                            "タイトル": sched_title,
                            "詳細": sched_detail,
                            "日付": today_str,
                            "開始時刻": sched_start.strftime("%H:%M"),
                            "終了時刻": sched_end.strftime("%H:%M"),
                        })
                        st.rerun()
                    else:
                        st.warning("タイトルを入力してください")
        st.markdown("</div>", unsafe_allow_html=True)

        # GCal 診断（折りたたみ）
        with st.expander("🔍 Google Calendar 接続状態", expanded=False):
            st.write(f"- ライブラリ読込: {'✅' if _gcal_available else '❌'}")
            st.write(f"- GOOGLE_CALENDAR_ID: `{gcal_id or '未設定'}`")
            if gcal_error:
                st.error(gcal_error)
            elif gcal_id:
                st.write(f"- 時刻付き予定: {len(gcal_timed)} 件 / 終日予定: {len(gcal_allday)} 件")

        with right_col:
            st.markdown("### 📝 バックログ")
            if not backlog_load_ok:
                st.warning(f"バックログの読み込みに失敗しました: {backlog_error}")
            bl_filter = st.radio("表示", ["未着手・進行中", "完了"], horizontal=True, key="bl_filter")
            if backlog_load_ok:
                if bl_filter == "未着手・進行中":
                    view_df = backlog_df[backlog_df["状態"].isin(["未着手", "進行中"])]
                else:
                    view_df = backlog_df[backlog_df["状態"] == "完了"]
            else:
                view_df = None

            if view_df is None:
                pass  # エラーは上に表示済み
            elif view_df.empty:
                st.info("バックログにアイテムがありません。")
            else:
                for _, item in view_df.iterrows():
                    color = PRIORITY_COLOR.get(item.get("優先度", "中"), "#888")
                    with st.container(border=True):
                        hc1, hc2 = st.columns([8, 3])
                        with hc1:
                            st.markdown(
                                f'<span style="background:{color};color:white;padding:1px 8px;'
                                f'border-radius:10px;font-size:0.75em;font-weight:bold">'
                                f'{item.get("優先度", "中")}</span>　**{item["タイトル"]}**',
                                unsafe_allow_html=True,
                            )
                            if item.get("詳細"):
                                st.caption(item["詳細"])
                        with hc2:
                            if st.button("📅", key=f"sched_{item['ID']}", help="スケジュールに追加"):
                                st.session_state.scheduling_item = item.to_dict()
                            if item.get("状態") != "完了":
                                if st.button("✅", key=f"done_{item['ID']}", help="完了にする"):
                                    update_backlog_status(item["ID"], "完了")
                                    st.rerun()
                            if st.button("🗑️", key=f"del_bl_{item['ID']}", help="削除"):
                                delete_backlog_item(item["ID"])
                                st.rerun()

            if st.session_state.scheduling_item:
                sitem = st.session_state.scheduling_item
                with st.container(border=True):
                    st.markdown(f"**📅 スケジュールに追加：{sitem['タイトル']}**")
                    sched_date = st.date_input("日付", value=datetime.now(_JST).date(), key="sched_date")
                    sc1, sc2 = st.columns(2)
                    with sc1:
                        start_t = st.time_input("開始時刻", value=dt_time(9, 0), key="sched_start")
                    with sc2:
                        end_t = st.time_input("終了時刻", value=dt_time(10, 0), key="sched_end")
                    btn1, btn2 = st.columns(2)
                    with btn1:
                        if st.button("追加", type="primary", key="sched_confirm", use_container_width=True):
                            add_schedule_event({
                                "タイトル": sitem["タイトル"],
                                "詳細": sitem.get("詳細", ""),
                                "日付": sched_date.isoformat(),
                                "開始時刻": start_t.strftime("%H:%M"),
                                "終了時刻": end_t.strftime("%H:%M"),
                                "バックログID": sitem.get("ID", ""),
                                "色": PRIORITY_COLOR.get(sitem.get("優先度", "中"), "#1976d2"),
                            })
                            st.session_state.scheduling_item = None
                            st.rerun()
                    with btn2:
                        if st.button("キャンセル", key="sched_cancel", use_container_width=True):
                            st.session_state.scheduling_item = None
                            st.rerun()

            st.divider()
            st.markdown("### ➕ タスクを追加")
            st.caption("Claudeが優先度を判断してバックログに追加します")

            for msg in st.session_state.task_messages[-6:]:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if st.session_state.pending_task:
                ptask = st.session_state.pending_task
                with st.container(border=True):
                    st.markdown(
                        f"**タイトル:** {ptask.get('タイトル', '')}  \n"
                        f"**優先度:** {ptask.get('優先度', '中')}  \n"
                        f"**詳細:** {ptask.get('詳細', '')}  \n"
                        f"**推定時間:** {ptask.get('推定時間', '-')} 分"
                    )
                tc1, tc2 = st.columns(2)
                with tc1:
                    if st.button("✅ バックログに追加", type="primary", key="add_task_confirm", use_container_width=True):
                        add_backlog_item({
                            "タイトル": ptask.get("タイトル", ""),
                            "詳細": ptask.get("詳細", ""),
                            "優先度": ptask.get("優先度", "中"),
                        })
                        st.session_state.pending_task = None
                        st.session_state.task_messages = []
                        st.rerun()
                with tc2:
                    if st.button("❌ キャンセル", key="cancel_task_confirm", use_container_width=True):
                        st.session_state.pending_task = None
                        st.session_state.task_messages = []
                        st.rerun()

            with st.form("task_form", clear_on_submit=True):
                task_text = st.text_area(
                    "タスクを入力",
                    placeholder="例：来週月曜日までに報告書を作成する（重要）",
                    height=80,
                    label_visibility="collapsed",
                )
                if st.form_submit_button("📤 Claudeに送信", type="primary", use_container_width=True):
                    if not task_text.strip():
                        st.warning("タスクを入力してください。")
                    elif not api_key:
                        st.error("Anthropic API キーが設定されていません。")
                    else:
                        with st.spinner("Claudeが解析中..."):
                            try:
                                parsed = parse_task_input(task_text.strip(), api_key)
                                st.session_state.pending_task = parsed
                                st.session_state.task_messages.append({"role": "user", "content": task_text.strip()})
                                comment = parsed.get("comment", "")
                                if comment:
                                    st.session_state.task_messages.append({"role": "assistant", "content": comment})
                                st.rerun()
                            except Exception as e:
                                st.error(f"解析エラー: {e}")

# =====================
# バージョン情報タブ
# =====================
with tab_info:
    st.subheader("ℹ️ バージョン情報")
    st.markdown(f"**バージョン:** {APP_VERSION}")
    st.markdown("**最終更新:** 2026-06-27　（v1.5.0: My Life Dashboard）")

    st.divider()
    st.markdown("### 使用技術")
    tech = {
        "フレームワーク": "[Streamlit](https://streamlit.io)",
        "データストア": "[Google Sheets](https://workspace.google.com/products/sheets/) / gspread",
        "AI解析": "[Anthropic Claude API](https://www.anthropic.com/) (claude-opus-4-8)",
        "グラフ": "[Plotly](https://plotly.com/python/)",
        "ホスティング": "[Streamlit Community Cloud](https://share.streamlit.io)",
    }
    for k, v in tech.items():
        st.markdown(f"- **{k}**: {v}")

    st.divider()
    st.markdown("### リンク")
    st.markdown("- [GitHubリポジトリ](https://github.com/stingrayr32/HealthCareManagement)")
    st.markdown("- [Google スプレッドシート](https://docs.google.com/spreadsheets/d/1zfuPXxXN3nvaIsxo__4kWtTx7Pm3FFgxPSySULpMGXs)")

    st.divider()
    st.markdown("### 更新履歴")
    history = [
        ("1.6.0", "2026-06-27", "タブ名をHealth/ToDoに変更、Google Calendar連携機能を追加"),
        ("1.5.0", "2026-06-27", "アプリ名を「My Life Dashboard」に変更、データ入力をダッシュボードタブに統合"),
        ("1.4.0", "2026-06-27", "タスク管理ページ追加：バックログ管理・日次スケジュール・Claude AIタスク入力"),
        ("1.3.0", "2026-06-27", "摂取目標ダイアログのレイアウト改善、脂質・炭水化物の目標設定を追加"),
        ("1.2.0", "2026-06-27", "摂取目標の設定ダイアログ追加、設定の永続化、保存後の自動更新"),
        ("1.1.0", "2026-06-27", "チャット形式のデータ入力機能を追加（Claude AI連携）"),
        ("1.0.0", "2026-06-26", "初回リリース：体重・体脂肪・カロリー・PFC・歩数のダッシュボード"),
    ]
    for ver, dt, desc in history:
        st.markdown(f"**v{ver}** `{dt}`  \n{desc}")
        st.write("")
