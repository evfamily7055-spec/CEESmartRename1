import streamlit as st
import json
import os
from pydantic import BaseModel, Field
from typing import Optional, Literal
# Google Gemini APIのライブラリ
# from google import genai 
# from google.genai import types

# 🚨 実際のファイルパースライブラリや音声文字起こしライブラリは別途インストールが必要です
# 例: import PyPDF2, docx, librosa
# 🚨 実際のGemini APIクライアント初期化は省略しています

# ----------------------------------------------------------------------
# 1. Gemini API構造化応答スキーマ定義 (要件 5.1, 5.2)
# ----------------------------------------------------------------------

# 論文
class PaperData(BaseModel):
    year: str = Field(description="出版年西暦 (例: 2024)")
    author: str = Field(description="主要著者名")
    title: str = Field(description="論文のタイトル")

# 請求書・領収書
class InvoiceData(BaseModel):
    invoice_date: str = Field(description="発行日 (YYYY-MM-DD形式を推奨)")
    invoice_amount: str = Field(description="合計金額 (数字と通貨記号を含む文字列)")
    invoice_issuer: str = Field(description="発行元/発行者名")
    invoice_subject: str = Field(description="請求書/領収書の件名")

# その他
class OtherData(BaseModel):
    title: str = Field(description="AIが推測したタイトル")

# AIコアからの最終応答スキーマ
Category = Literal["論文", "請求書・領収書", "その他", "不明"]

class AICoreResponse(BaseModel):
    category: Category = Field(description="ファイルの分類カテゴリ。必須。")
    extracted_data: Optional[PaperData | InvoiceData | OtherData | dict] = Field(None, description="分類に応じた抽出データを含むオブジェクト。不明の場合は空。")
    reasoning: str = Field(description="LLMがその分類と抽出を行った根拠。")

# ----------------------------------------------------------------------
# 2. バックエンド処理機能 (モック/骨格)
# ----------------------------------------------------------------------

def extract_text_mock(uploaded_file):
    """
    🚨 ファイル形式に応じてテキストを抽出するモック関数。
    実際のアプリでは、PyPDF2, python-docx, openpyxlなどを使って実装が必要です。
    """
    file_ext = uploaded_file.name.split('.')[-1].lower()
    
    if file_ext in ['mp3', 'wav', 'm4a']:
        # 🚨 音声ファイルは文字起こし (ASR) を想定
        st.info(f"🔊 音声ファイル ({uploaded_file.name}): 自動文字起こし処理をスキップし、モックテキストを使用します。")
        asr_text = "音声文字起こし: 2023年10月5日、田中商事から15000円の請求書を受領しました。件名はソフトウェアライセンスです。"
        # 実際にはここで .txt ファイルも生成する (要件 4)
        return asr_text, True # Trueは文字起こしテキストがあることを示す
    
    elif file_ext in ['pdf', 'docx', 'xlsx', 'pptx', 'csv']:
        # 🚨 標準テキスト抽出 (およびOCRフォールバック) を想定
        st.info(f"📄 ドキュメントファイル ({uploaded_file.name}): テキスト抽出処理をスキップし、モックテキストを使用します。")
        # デモ用としてランダムにモックテキストを割り当てる
        if '請求' in uploaded_file.name or 'invoice' in uploaded_file.name:
            mock_text = "請求書データ。日付: 2024年5月10日、金額: ¥25,000、発行元: Google株式会社、件名: AIサービス利用料。"
        elif '論文' in uploaded_file.name or 'paper' in uploaded_file.name:
            mock_text = "論文。タイトル: The Impact of AI on File Management. 著者: J. Smith, A. Brown. 出版年: 2025."
        else:
            mock_text = f"その他のファイル。内容: {uploaded_file.name}の概要です。"
            
        return mock_text, False
        
    else:
        return f"ファイル形式 '{file_ext}' は対応していません。", False


def get_ai_core_response_mock(text_content: str) -> AICoreResponse:
    """
    🚨 Gemini API呼び出しのモック関数。
    実際は `genai.client.models.generate_content(..., response_schema=AICoreResponse)` を使用。
    """
    st.info("🤖 Gemini API呼び出しをスキップし、内容に基づいたモック応答を返します。")
    
    # モックロジック
    if "請求書" in text_content or "Google株式会社" in text_content:
        return AICoreResponse(
            category="請求書・領収書",
            extracted_data=InvoiceData(
                invoice_date="2024-05-10",
                invoice_amount="25,000",
                invoice_issuer="Google株式会社",
                invoice_subject="AIサービス利用料"
            ).model_dump(),
            reasoning="請求書に関するキーワードと金額情報が含まれていたため。"
        )
    elif "論文" in text_content or "Impact of AI" in text_content:
        return AICoreResponse(
            category="論文",
            extracted_data=PaperData(
                year="2025",
                author="J. Smith, A. Brown",
                title="The Impact of AI on File Management"
            ).model_dump(),
            reasoning="タイトル、著者、出版年に関するキーワードと構造が検出されたため。"
        )
    else:
        return AICoreResponse(
            category="その他",
            extracted_data=OtherData(
                title="新しいAI時代のファイル管理"
            ).model_dump(),
            reasoning="特定の文書形式に一致せず、タイトルをAIが推測したため。"
        )


def apply_rename_rule(ai_response: AICoreResponse, original_name: str) -> str:
    """
    要件 6 に基づき、AIの応答からリネーム後のファイル名を生成する。
    """
    base_name, ext = os.path.splitext(original_name)
    category = ai_response.category
    data = ai_response.extracted_data

    # 4. 不明: リネームスキップ
    if category == "不明":
        st.warning("⚠️ カテゴリが「不明」のため、リネーム処理はスキップされました。")
        return original_name

    # 1. 論文 (要件 6.1)
    elif category == "論文" and isinstance(data, dict):
        # 実際はPydanticモデルのインスタンスとして扱う
        year = data.get("year", "YYYY")
        authors = data.get("author", "著者名不明")
        title = data.get("title", "タイトル不明")

        # 短縮ロジック (簡略化)
        authors_short = authors[:15] if len(authors) > 15 else authors
        title_short = title[:(50 - len(year) - len(authors_short) - 2)] # 2は区切り文字 '_' の数

        new_name = f"{year}_{authors_short}_{title_short}".strip('_')
        return f"{new_name}{ext}"

    # 2. 請求書・領収書 (要件 6.2)
    elif category == "請求書・領収書" and isinstance(data, dict):
        # 実際はPydanticモデルのインスタンスとして扱う
        date_str = data.get("invoice_date", "YYYYMMDD").replace('-', '').replace('/', '')
        issuer = data.get("invoice_issuer", "発行元不明")[:15] # 15字程度に短縮
        amount = ''.join(filter(str.isdigit, data.get("invoice_amount", "0")))
        subject = data.get("invoice_subject", "件名なし")[:15] # 15字程度に短縮

        new_name = f"{date_str}_{issuer}_{amount}_{subject}".strip('_')
        return f"{new_name}{ext}"

    # 3. その他 (要件 6.3)
    elif category == "その他" and isinstance(data, dict):
        # 実際はPydanticモデルのインスタンスとして扱う
        title = data.get("title", "AI推測タイトル")[:30] # 30字以内に短縮
        return f"{title}{ext}"
    
    # エラー時のフォールバック
    else:
        st.error(f"🚨 リネームルール適用エラー: カテゴリ '{category}' またはデータ構造が不正です。")
        return original_name

# ----------------------------------------------------------------------
# 3. Streamlit UI定義 (要件 3)
# ----------------------------------------------------------------------

# ページ設定
st.set_page_config(page_title="🤖 AIスマートファイルリネームシステム", layout="wide")

## サイドバー
with st.sidebar:
    st.header("🔑 設定")
    # Gemini APIキー入力フィールド (要件 3)
    api_key = st.text_input(
        "Gemini APIキーを入力", 
        type="password", 
        help="Google AI Studioで取得したAPIキーを入力してください。"
    )
    if api_key:
        # 実際はここでAPIクライアントを初期化する
        # client = genai.Client(api_key=api_key)
        st.success("APIキーが設定されました。")
    else:
        st.warning("APIキーが未設定です。モック応答で処理を実行します。")
    
    st.markdown("---")
    st.subheader("対応ファイル形式 (要件 4)")
    st.markdown("""
    * **文書**: PDF, DOCX, XLSX, PPTX, CSV
    * **音声**: MP3, WAV, M4A
    """)

## メインエリア
st.title("🤖 AIスマートファイルリネームシステム")
st.caption("アップロードされたファイルの内容をAIが分析し、命名ルールに従って自動リネームを行います。")

# ファイルアップロードエリア (要件 3)
uploaded_files = st.file_uploader(
    "ファイルをアップロード", 
    type=['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'mp3', 'wav', 'm4a'],
    accept_multiple_files=True
)

if uploaded_files:
    if st.button("🚀 AIリネーム・文字起こしを実行"):
        
        # 処理状況の表示 (要件 3)
        st.subheader("📊 処理結果")
        results = []
        
        with st.spinner("ファイルを分析中... (Gemini API呼び出し中)"):
            for uploaded_file in uploaded_files:
                
                # 1. テキスト抽出/文字起こし (要件 4)
                text_content, is_asr = extract_text_mock(uploaded_file)
                
                if "対応していません" in text_content:
                    results.append({
                        "オリジナルファイル名": uploaded_file.name,
                        "処理状況": "スキップ (非対応ファイル)",
                        "分類カテゴリ": "-",
                        "リネーム後ファイル名": uploaded_file.name,
                        "ダウンロード": "---"
                    })
                    continue

                # 2. AIコア連携 (要件 5)
                try:
                    # 実際はAPIキーがある場合にクライアントを使い、モックを使用しない
                    ai_response = get_ai_core_response_mock(text_content)
                except Exception as e:
                    st.error(f"❌ AIコア処理エラー: {e}")
                    ai_response = AICoreResponse(category="不明", extracted_data={}, reasoning="APIエラーが発生したため。")

                # 3. リネームルール適用 (要件 6)
                new_filename = apply_rename_rule(ai_response, uploaded_file.name)
                
                # 4. 結果の記録
                result_data = {
                    "オリジナルファイル名": uploaded_file.name,
                    "処理状況": "完了",
                    "分類カテゴリ": ai_response.category,
                    "リネーム後ファイル名": new_filename,
                    "ダウンロード": "リネーム済ファイル"
                }

                # 音声ファイルの場合、文字起こしテキストのダウンロードオプションを追加 (要件 4)
                if is_asr:
                    result_data["ダウンロード"] += " / 文字起こしTXT"
                    # 実際は文字起こしテキストをファイルに書き出し、ダウンロード用の処理を行う
                    st.download_button(
                        label=f"📝 {uploaded_file.name}.txt ダウンロード (モック)",
                        data=text_content,
                        file_name=f"{os.path.splitext(uploaded_file.name)[0]}.txt",
                        mime="text/plain"
                    )

                # リネーム済みファイルのダウンロードボタン (要件 3)
                # 実際はリネームされたファイルを保存し、その内容をダウンロードさせる
                st.download_button(
                    label=f"💾 {new_filename} ダウンロード (モック)",
                    data=uploaded_file.getvalue(), # オリジナルファイルの内容を代用
                    file_name=new_filename,
                    mime=uploaded_file.type,
                    key=f"download_{uploaded_file.name}"
                )

                results.append(result_data)
        
        # 処理結果の表形式での表示 (要件 3)
        st.dataframe(results, use_container_width=True)
        
        st.markdown("---")
        st.subheader("💡 AI分析結果 (デバッグ/詳細)")
        # 抽出結果、AI分類カテゴリ、リネーム後のファイル名を表示 (要件 3)
        st.json(ai_response.model_dump() if 'ai_response' in locals() else {})
