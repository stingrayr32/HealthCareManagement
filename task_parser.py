from __future__ import annotations
import json
import anthropic

TASK_SYSTEM_PROMPT = """あなたはタスク管理アシスタントです。
ユーザーが日本語で入力したタスクや作業項目を解析し、以下のJSON形式のみで返してください。
他のテキストは一切含めないでください。

{
  "タイトル": "タスクの短い名前（30文字以内）",
  "詳細": "タスクの詳細説明（なければ空文字）",
  "優先度": "高/中/低のいずれか",
  "推定時間": 数値（分）またはnull,
  "comment": "ユーザーへの返答（バックログ登録確認・アドバイスなど、2文程度）"
}

優先度の判断基準：
- 高：緊急・締め切りが近い・重要な業務
- 中：重要だが急ぎではない（デフォルト）
- 低：できれば対応したい程度"""


def parse_task_input(user_text: str, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1024,
        thinking={"type": "adaptive"},
        system=TASK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    for block in response.content:
        if block.type == "text":
            text = block.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(text)
    raise ValueError("Claude からの応答にテキストが含まれていませんでした")
