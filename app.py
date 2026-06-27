import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import date
from sheets_client import load_data, append_row, find_row_by_date, update_row
from llm_parser import parse_health_input

st.set_page_config(
    page_title="健康管理ダッシュボード",
    page_icon="💪",
    layout="wide",
)

st.title("💪 健康管理ダッシュボード")

# --- サイドバー ---
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
    st.header("🎯 目標体重")
    HEIGHT_M = 1.71
    ideal_weight = round(22.0 * HEIGHT_M ** 2, 1)
    target_weight_input = st.number_input(
        "目標体重 (kg)",
        min_value=40.0,
        max_value=120.0,
        value=ideal_weight,
        step=0.1,
        format="%.1f",
    )
    target_bmi = target_weight_input / HEIGHT_M ** 2
    if target_bmi < 18.5:
        bmi_warn = "⚠️ 低体重域（BMI 18.5未満）"
    elif target_bmi < 25.0:
        bmi_warn = "✅ 普通体重域"
    elif target_bmi < 30.0:
        bmi_warn = "⚠️ 過体重域"
    else:
        bmi_warn = "⚠️ 肥満域"
    st.caption(f"BMI: {target_bmi:.1f}　{bmi_warn}")

    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")

tab_dashboard, tab_input = st.tabs(["📊 ダッシュボード", "📝 データ入力"])

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
                    if bmi < 18.5:
                        bmi_label = "低体重"
                    elif bmi < 25.0:
                        bmi_label = "普通体重"
                    elif bmi < 30.0:
                        bmi_label = "過体重"
                    else:
                        bmi_label = "肥満"
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
            TARGET_WEIGHT = target_weight_input

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
                    days_to_target = (TARGET_WEIGHT - intercept) / slope
                    future_days = np.arange(0, int(days_to_target) + 1, 1)
                    pred_dates = [origin + pd.Timedelta(days=int(d)) for d in future_days]
                    pred_y = (slope * future_days + intercept).tolist()
                    target_date = origin + pd.Timedelta(days=int(days_to_target))

            st.subheader("📈 体重・体脂肪の推移")
            if target_date:
                st.caption(f"目標体重: **{TARGET_WEIGHT} kg**（BMI {target_bmi:.1f}）　予測到達日: **{target_date.strftime('%Y/%m/%d')}**")

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
                               line=dict(color="#4CAF50", width=2, dash="dash"),
                               mode="lines"),
                    secondary_y=False,
                )
                fig_weight.add_hline(
                    y=TARGET_WEIGHT, line_dash="dot", line_color="#4CAF50", line_width=1.5,
                    annotation_text=f"目標 {TARGET_WEIGHT} kg",
                    annotation_position="bottom right",
                    annotation_font_color="#4CAF50",
                    secondary_y=False,
                )
            fig_weight.update_layout(height=400, hovermode="x unified", legend=dict(orientation="h", y=1.1))
            fig_weight.update_yaxes(title_text="体重 (kg)", secondary_y=False)
            fig_weight.update_yaxes(title_text="体脂肪率 (%)", secondary_y=True)
            st.plotly_chart(fig_weight, use_container_width=True)

            # --- 本日のカロリー・PFC ---
            today_str = pd.Timestamp.today().normalize()
            today_rows = df[df["日付"] == today_str]
            if not today_rows.empty:
                t = today_rows.iloc[-1]
                st.subheader("🗓️ 本日の摂取状況")
                tc1, tc2, tc3, tc4, tc5, tc6, tc7 = st.columns(7)
                CALORIE_LIMIT_TODAY = 2160
                cal_val = t["総カロリー"] if pd.notna(t["総カロリー"]) else None
                with tc1:
                    st.metric(
                        "総Cal",
                        f"{cal_val:.0f}" if cal_val is not None else "-",
                        delta=f"{cal_val - CALORIE_LIMIT_TODAY:+.0f}" if cal_val is not None else None,
                        delta_color="inverse",
                        help="総カロリー (kcal)",
                    )
                with tc2:
                    st.metric("朝食", f"{t['朝食Cal']:.0f}" if pd.notna(t["朝食Cal"]) else "-", help="朝食カロリー (kcal)")
                with tc3:
                    st.metric("昼食", f"{t['昼食Cal']:.0f}" if pd.notna(t["昼食Cal"]) else "-", help="昼食カロリー (kcal)")
                with tc4:
                    st.metric("夕食", f"{t['夕食Cal']:.0f}" if pd.notna(t["夕食Cal"]) else "-", help="夕食カロリー (kcal)")
                with tc5:
                    st.metric("P (g)", f"{t['総タンパク質']:.0f}" if pd.notna(t["総タンパク質"]) else "-", help="タンパク質 (g)")
                with tc6:
                    st.metric("F (g)", f"{t['総脂質']:.0f}" if pd.notna(t["総脂質"]) else "-", help="脂質 (g)")
                with tc7:
                    st.metric("C (g)", f"{t['総炭水化物']:.0f}" if pd.notna(t["総炭水化物"]) else "-", help="炭水化物 (g)")

            # --- グラフ2: カロリー摂取の内訳 ---
            CALORIE_LIMIT = 2160

            st.subheader("🍽️ カロリー摂取の推移（朝・昼・夕）")
            fig_cal = go.Figure()
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["朝食Cal"], name="朝食", marker_color="#FFB74D"))
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["昼食Cal"], name="昼食", marker_color="#4DB6AC"))
            fig_cal.add_trace(go.Bar(x=df_filtered["日付"], y=df_filtered["夕食Cal"], name="夕食", marker_color="#7986CB"))
            fig_cal.add_hline(
                y=CALORIE_LIMIT,
                line_dash="dot",
                line_color="red",
                line_width=2,
                annotation_text=f"推奨上限 {CALORIE_LIMIT} kcal",
                annotation_position="top right",
                annotation_font_color="red",
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

# =====================
# データ入力タブ
# =====================
with tab_input:
    st.subheader("📝 今日の記録を入力")
    st.markdown(
        "体重・体脂肪・朝食・昼食・夕食・飲酒・運動の内容を自由に入力してください。"
        "Claudeがカロリー・PFCを推定し、スプレッドシートに記録します。"
    )

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "parsed_data" not in st.session_state:
        st.session_state.parsed_data = None
    if "existing_row_index" not in st.session_state:
        st.session_state.existing_row_index = None

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("例：体重73.5kg、体脂肪26%、朝はご飯とみそ汁、昼はラーメン、夜は焼き肉、ビール1杯、スクワット10分")

    if user_input:
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        if not api_key:
            with st.chat_message("assistant"):
                st.error("サイドバーに Anthropic API キーを入力してください。")
            st.session_state.chat_messages.append({"role": "assistant", "content": "⚠️ サイドバーに Anthropic API キーを入力してください。"})
        else:
            with st.chat_message("assistant"):
                with st.spinner("Claudeが解析中..."):
                    try:
                        today_str = date.today().strftime("%Y-%m-%d")
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
                        st.success("既存データを更新しました！")
                    else:
                        append_row(row)
                        st.success("スプレッドシートに保存しました！")
                    st.session_state.parsed_data = None
                    st.session_state.existing_row_index = None
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"保存に失敗しました: {e}")
        with col_cancel:
            if st.button("❌ キャンセル"):
                st.session_state.parsed_data = None
                st.session_state.existing_row_index = None
                st.rerun()
