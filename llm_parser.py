from __future__ import annotations

import json
import anthropic

SYSTEM_PROMPT = """あなたは健康管理アシスタントです。ユーザーが日本語で入力した情報を解析し、
以下のJSON形式で返してください。推定カロリーとPFC（タンパク質・脂質・炭水化物）は食品データベースの知識から推定してください。

返答はJSON形式のみで、他のテキストは含めないでください。

{
  "日付": "YYYY-MM-DD形式（記載がなければ今日の日付）",
  "体重": 数値またはnull,
  "体脂肪": 数値またはnull,
  "運動の有無": "あり（内容）またはなし",
  "歩数": 数値またはnull,
  "朝食内容": "朝食の説明",
  "昼食内容": "昼食の説明",
  "夕食内容": "夕食の説明",
  "飲酒": "あり（種類・量）またはなし",
  "朝食Cal": 数値（推定kcal）またはnull,
  "昼食Cal": 数値（推定kcal）またはnull,
  "夕食Cal": 数値（推定kcal）またはnull,
  "総カロリー": 数値（朝昼夕合計kcal）またはnull,
  "総タンパク質": 数値（g）またはnull,
  "総脂質": 数値（g）またはnull,
  "総炭水化物": 数値（g）またはnull,
  "食事内容": "朝：〇〇 昼：〇〇 夕：〇〇 飲酒：〇〇 形式でまとめた文字列",
  "メモ": "その他のメモ",
  "comment": "入力内容への感想・励まし・アドバイスを2〜3文の日本語で。体重・体脂肪の変化、カロリーバランス、運動、食事内容などに触れて、親しみやすく前向きなトーンで。"
}

カロリー・PFCの推定ルール：
- 各食事の内容から食品ごとのカロリーとPFCを推定して合計する
- 飲酒がある場合はそのカロリーも夕食または食事全体に含める
- 不明な食品はよく似た食品で代替推定する
- 数値は整数で返す

既存データがある場合のマージルール：
- 新しい入力で言及されたフィールドのみ更新する
- 新しい入力で言及されていないフィールドは既存の値をそのまま維持する
- 「食事内容」フィールドは朝・昼・夕それぞれの最新情報を反映して再構築する
- 「総カロリー」「総タンパク質」「総脂質」「総炭水化物」は更新後の朝昼夕すべての値から再計算する"""


def _extract_json(response) -> dict:
    for block in response.content:
        if block.type == "text":
            text = block.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])
            return json.loads(text)
    raise ValueError("Claude からの応答にテキストが含まれていませんでした")


def parse_health_input(user_text: str, api_key: str, today: str,
                       existing_data: dict | None = None) -> dict:
    client = anthropic.Anthropic(api_key=api_key)

    if existing_data:
        existing_summary = "\n".join(
            f"  {k}: {v}" for k, v in existing_data.items() if v not in ("", None)
        )
        user_message = (
            f"今日の日付は{today}です。\n\n"
            f"【既存データ（本日分として既にシートに保存されている内容）】\n{existing_summary}\n\n"
            f"【新しい入力】\n{user_text}\n\n"
            "既存データと新しい入力をマージして、最終的な1日分のデータをJSON形式で返してください。"
            "新しく言及されていないフィールドは既存の値を維持してください。"
        )
    else:
        user_message = f"今日の日付は{today}です。以下の内容を解析してください：\n\n{user_text}"

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return _extract_json(response)
