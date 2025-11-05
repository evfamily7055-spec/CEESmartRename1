import streamlit as st
import json
import os
import io
import csv # CSV処理ライブラリ
import time # ファイルアップロード後の待機用
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, Literal, Dict, Any, List, Union # Unionを追加

# 外部ライブラリ
import pypdf # PDF処理ライブラリ
import docx # DOCX処理ライブラリ (python-docx)
import openpyxl # XLSX処理ライブラリ
from pptx import Presentation # PPTX処理ライブラリ (python-pptx)

# Google Gemini APIのライブラリ
from google import genai 
from google.genai import types 
from google.genai.errors import APIError 

# ----------------------------------------------------------------------
# 1. Gemini API構造化応答スキーマ定義 (要件 5.1, 5.2)
# ----------------------------------------------------------------------

# 論文データ
class PaperData(BaseModel):
    year: str = Field(description="出版年西暦 (例: 2024)")
    author: str = Field(description="主要著者名。カンマ区切りで記述してください。")
    title: str = Field(description="論文のタイトル。")

# 請求書・領収書データ
class InvoiceData(BaseModel):
    invoice_date: str = Field(description="発行日。YYYY-MM-DD形式に変換してください。")
    invoice_amount: str = Field(description="合計金額。数字と通貨記号を含んだ元の文字列。")
    invoice_issuer: str = Field(description="発行元/発行者名。")
    invoice_subject: str = Field(description="請求書/領収書の件名。")

# その他データ
class OtherData(BaseModel):
    title: str = Field(description="ファイル内容を最もよく表す、AIが推測したタイトル。")

# AIコアからの最終応答スキーマ
Category = Literal["論文", "請求書・領収書", "その他", "不明"]

class AICoreResponse(BaseModel):
    category: Category = Field(description="ファイルの分類カテゴリ。必須。取りうる値: 論文, 請求書・領収書, その他, 不明")
    # extracted_data の型を具体的な Pydantic モデルのユニオンに変更
    extracted_data: Optional[Union[PaperData, InvoiceData, OtherData]] = Field( 
        None, 
        description="分類に応じた抽出データを含むオブジェクト。不明の場合は null にしてください。このフィールドの構造は category の値に依存します。"
    )
    reasoning: str = Field(description="LLMがその分類と抽出を行った根拠。")
    transcript: Optional[str] = Field(None, description="音声ファイルが入力された場合の文字起こし結果。")

# ----------------------------------------------------------------------
# 2. バックエンド処理機能 (ファイル抽出とAIコア連携)
# ----------------------------------------------------------------------

def extract_text(uploaded_file: st.runtime.uploaded_file_manager.UploadedFile) -> tuple[str, bool]:
    """
    ファイル形式に応じてテキストを抽出する関数。
    音声ファイルは「文字起こしが必要」としてフラグ (is_asr=True) を返す。
    """
    file_ext = uploaded_file.name.split('.')[-1].lower()
    
    # 対応ファイル形式のチェック
    supported_extensions = ['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'mp3', 'wav', 'm4a']
    if file_ext not in supported_extensions:
        return f"ファイル形式 '{file_ext}' は対応していません。", False

    # --- 音声ファイル処理 (フラグを返す) ---
    if file_ext in ['mp3', 'wav', 'm4a']:
        st.info(f"🔊 音声ファイル ({uploaded_file.name}): ファイルをGemini APIに直接送信します。")
        return uploaded_file.name, True 

    # --- PDF 処理 (安定性強化) ---
    if file_ext == 'pdf':
        try:
            st.info(f"📄 PDFファイル ({uploaded_file.name}): テキスト抽出を実行中...")
            pdf_reader = pypdf.PdfReader(uploaded_file)
            text_content = ""
            for page in pdf_reader.pages:
                # 抽出時にエラーが発生する可能性を考慮し、try/exceptを追加
                try:
                    text_content += page.extract_text() or ""
                except (TypeError, ValueError) as e:
                    st.warning(f"⚠️ ページ抽出エラー: {e}")
                    continue
                
            if not text_content.strip():
                st.warning("⚠️ PDFからテキストが抽出できませんでした。スキャン画像と見なしてモックOCRテキストを使用します。")
                text_content = "OCR結果: このファイルは2024年4月1日に発行された領収書であり、金額は25,000円です。発行元はABCコンサルティングです。"
            
            return text_content, False
        
        except Exception as e:
            st.error(f"🚨 PDF処理エラー: {e}")
            return f"PDF処理中にエラーが発生しました: {e}", False

    # --- DOCX 処理 ---
    elif file_ext == 'docx':
        try:
            st.info(f"📄 DOCXファイル ({uploaded_file.name}): テキスト抽出を実行中...")
            document = docx.Document(io.BytesIO(uploaded_file.getvalue()))
            text_content = ""
            for paragraph in document.paragraphs:
                text_content += paragraph.text + '\n' 
                
            if not text_content.strip():
                st.warning("⚠️ DOCXからテキストが抽出できませんでした。ファイル内容が空か、読み取りに失敗しました。")
            
            return text_content, False

        except Exception as e:
            st.error(f"🚨 DOCX処理エラー: {e}")
            return f"DOCX処理中にエラーが発生しました: {e}", False

    # --- XLSX 処理 ---
    elif file_ext == 'xlsx':
        try:
            st.info(f"📊 XLSXファイル ({uploaded_file.name}): テキスト抽出を実行中...")
            workbook = openpyxl.load_workbook(uploaded_file, read_only=True)
            text_content = ""
            
            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                text_content += f"\n--- シート: {sheet_name} ---\n"
                
                for row in sheet.iter_rows():
                    row_data = []
                    for cell in row:
                         if cell.value is not None:
                            row_data.append(str(cell.value))
                    if row_data:
                        text_content += ', '.join(row_data) + '\n'
            
            if not text_content.strip():
                st.warning("⚠️ XLSXからテキストが抽出できませんでした。")
            
            return text_content, False

        except Exception as e:
            st.error(f"🚨 XLSX処理エラー: {e}")
            return f"XLSX処理中にエラーが発生しました: {e}", False

    # --- PPTX 処理 (安定性強化) ---
    elif file_ext == 'pptx':
        try:
            st.info(f"🖼️ PPTXファイル ({uploaded_file.name}): テキスト抽出を実行中...")
            presentation = Presentation(uploaded_file)
            text_content = ""
            
            for i, slide in enumerate(presentation.slides):
                text_content += f"\n--- スライド {i+1} ---\n"
                for shape in slide.shapes:
                    if hasattr(shape, "text_frame") and shape.text_frame: # テキストフレームの存在チェック
                        text_content += shape.text + '\n'
                    elif shape.has_table:
                        # テーブルセル内のテキストをより確実に取得
                        for row in shape.table.rows:
                            row_data = []
                            for cell in row.cells:
                                if cell.text_frame:
                                    row_data.append(cell.text)
                            text_content += ' | '.join(row_data) + '\n'
                    elif shape.has_text_frame: # has_text_frameはtext_frameの有無をチェック
                        text_content += shape.text_frame.text + '\n'

            if not text_content.strip():
                st.warning("⚠️ PPTXからテキストが抽出できませんでした。")

            return text_content, False
        
        except Exception as e:
            st.error(f"🚨 PPTX処理エラー: {e}")
            return f"PPTX処理中にエラーが発生しました: {e}", False

    # --- CSV 処理 ---
    elif file_ext == 'csv':
        try:
            st.info(f"📋 CSVファイル ({uploaded_file.name}): テキスト抽出を実行中...")
            text_stream = io.StringIO(uploaded_file.getvalue().decode('utf-8'))
            reader = csv.reader(text_stream)
            
            text_content = ""
            for row in reader:
                text_content += ', '.join(row) + '\n'

            if not text_content.strip():
                st.warning("⚠️ CSVファイルが空か、読み取りに失敗しました。")

            return text_content, False

        except Exception as e:
            st.error(f"🚨 CSV処理エラー: {e}")
            return f"CSV処理中にエラーが発生しました: {e}", False
            
# 🚨 モック応答関数（APIキー未入力時に使用）
def get_ai_core_response_mock(text_content: str, uploaded_file: st.runtime.uploaded_file_manager.UploadedFile, is_asr: bool) -> AICoreResponse:
    """
    Gemini API呼び出しのモック関数。APIキーがない場合にフォールバックとして使用。
    """
    if is_asr:
        # 音声ファイルのモック応答
        transcript = "モック文字起こし: 2023年10月5日、田中商事から15000円の請求書を受領しました。件名はソフトウェアライセンスです。"
        data = InvoiceData(
            invoice_date="2023-10-05",
            invoice_amount="15000円",
            invoice_issuer="田中商事",
            invoice_subject="ソフトウェアライセンス"
        )
        return AICoreResponse(
            category="請求書・領収書",
            extracted_data=data,
            reasoning="音声から請求情報が文字起こしされました。",
            transcript=transcript
        )

    # 文書ファイルのモック応答 (文書の内容がエラーでないか確認)
    if "処理中にエラーが発生しました" in text_content:
        return AICoreResponse(category="不明", extracted_data=None, reasoning="ファイル処理中にエラーが発生し、内容を取得できませんでした。")
    
    # 文書ファイルのモック応答 (以前と同じロジック)
    if "請求書" in text_content or "Google株式会社" in text_content or "領収書" in text_content:
        data = InvoiceData(
            invoice_date="2024-05-10",
            invoice_amount="25,000円",
            invoice_issuer="Google株式会社",
            invoice_subject="AIサービス利用料"
        )
        return AICoreResponse(
            category="請求書・領収書",
            extracted_data=data,
            reasoning="請求書に関するキーワードと金額情報が含まれていたため。"
        )
    elif "論文" in text_content or "Impact of AI" in text_content or "著者" in text_content:
        data = PaperData(
            year="2025",
            author="J. Smith, A. Brown",
            title="The Impact of AI on File Management"
        )
        return AICoreResponse(
            category="論文",
            extracted_data=data,
            reasoning="タイトル、著者、出版年に関するキーワードと構造が検出されたため。"
        )
    else:
        data = OtherData(
            title="新しいAI時代のファイル管理"
        )
        return AICoreResponse(
            category="その他",
            extracted_data=data,
            reasoning="特定の文書形式に一致せず、タイトルをAIが推測したため。"
        )

# 実際のAPI連携関数 (マルチモーダル対応)
def get_ai_core_response(client: genai.Client, text_content: str, uploaded_file: st.runtime.uploaded_file_manager.UploadedFile, is_asr: bool) -> AICoreResponse:
    """
    Gemini APIを呼び出し、構造化されたJSON応答を取得し、Pydanticで厳密に検証する。
    """
    # 応答スキーマを Pydantic モデルから直接生成
    response_schema = AICoreResponse.model_json_schema()

    system_instruction = f"""
    あなたはファイルの内容を分析し、リネームのための構造化データを抽出するAIです。

    [音声ファイルの場合の特別指示]
    入力が音声ファイルの場合、まず**文字起こし**を行い、その結果を必ず 'transcript' フィールドに格納してください。その後、文字起こし結果に基づいてファイルを分類し、'extracted_data' に必要な情報を抽出してください。

    [文書ファイルの場合の指示]
    提供されたテキスト内容（OCR結果を含む）を分析し、以下のいずれかのカテゴリに分類し、'extracted_data' に必要な情報を抽出してください。

    [全JSON出力ルール]
    1. 応答は必ずJSON形式で、提供されたスキーマに厳密に従ってください。
    2. 'category' が "不明" の場合、'extracted_data' は必ず null にしてください。
    3. JSON以外の追加のテキストは一切含めないでください。
    """
    
    parts = []
    
    if is_asr:
        st.info("⬆️ 音声ファイルをGemini APIにアップロードし、文字起こしと分析を同時に行います。")
        
        uploaded_file_gemini = None
        try:
            uploaded_file_gemini = client.files.upload(
                file=uploaded_file.getvalue(), 
                mime_type=uploaded_file.type
            )
        except Exception as e:
            st.error(f"🚨 ファイルアップロードエラー: {e}")
            return AICoreResponse(category="不明", extracted_data=None, reasoning=f"音声ファイルのアップロードに失敗: {e}")

        parts.append(uploaded_file_gemini)
        parts.append(f"この音声ファイルの内容を文字起こしし、その結果に基づき、内容を分析して以下の構造化データ形式で抽出してください。")
        
    else:
        # 文書ファイルの場合
        parts.append(f"以下のファイル内容を分析し、JSON形式で分類・情報抽出を行ってください:\n\n---\n{text_content}\n---")

    
    final_response = None
    uploaded_file_gemini = locals().get('uploaded_file_gemini') # finallyブロックのために定義
    
    # --- 修正箇所: response_text を try ブロック外で初期化 ---
    response_text = ""
    # --------------------------------------------------------

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-09-2025',
            contents=parts,
            system_instruction=system_instruction,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                # Pydantic スキーマを直接渡す
                response_schema=response_schema, 
                timeout=120  
            )
        )
        
        # --- JSONパース前のクリーンアップ ---
        response_text = response.text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:].strip()
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()
        
        if not response_text:
            raise json.JSONDecodeError("Received empty response text.", "response.text", 0)

        response_json = json.loads(response_text)
        
        # Pydantic の Union 型検証により、一度のバリデーションで済む
        final_response = AICoreResponse.model_validate(response_json)
        
        return final_response

    except APIError as e:
        st.error(f"❌ Gemini APIエラーが発生しました: {e}")
        return AICoreResponse(category="不明", extracted_data=None, reasoning=f"APIエラー: {e}")
    except json.JSONDecodeError:
        st.error(f"❌ Geminiからの応答が不正なJSON形式でした。生の応答: {response_text[:200]}...")
        return AICoreResponse(category="不明", extracted_data=None, reasoning="AI応答のJSON解析に失敗しました。不正な形式のJSONが出力されました。")
    except ValidationError as e:
        # Pydantic の厳密な検証 (Union型を含む) に失敗した場合
        st.error(f"❌ 構造化データ検証失敗: LLMの出力が要求スキーマに一致しません。")
        # response_text が確実に定義されているため、ここで参照しても安全
        st.json({"validation_error_details": e.errors(), "raw_response_text": response_text[:500]})
        
        return AICoreResponse(category="不明", extracted_data=None, reasoning="AI応答がAICoreResponseスキーマ検証に失敗しました。詳細をログで確認してください。")
    except Exception as e:
        st.error(f"❌ 予期せぬエラーが発生しました: {e}")
        return AICoreResponse(category="不明", extracted_data=None, reasoning=f"予期せぬエラー: {e}")
    finally:
        # 3. アップロードしたファイルを削除 (リソースの節約とセキュリティのため)
        if is_asr and uploaded_file_gemini:
             st.info("⬇️ アップロードした一時ファイルを削除しています。")
             client.files.delete(name=uploaded_file_gemini.name)
             time.sleep(1)


def apply_rename_rule(ai_response: AICoreResponse, original_name: str) -> str:
    """
    要件 6 に基づき、AIの応答からリネーム後のファイル名を生成する。
    """
    base_name, ext = os.path.splitext(original_name)
    category = ai_response.category
    
    # データを dict 形式で取得。extracted_data が None の場合は空の dict を使用
    data = ai_response.extracted_data.model_dump() if ai_response.extracted_data else {} 

    # ファイル名に使用できない文字を削除/置換するヘルパー関数
    def sanitize_filename(name: str) -> str:
        safe_name = name.replace(' ', '_')
        return ''.join(c for c in safe_name if c.isalnum() or c in '._-')

    # 4. 不明: リネームスキップ
    if category == "不明":
        st.warning("⚠️ カテゴリが「不明」のため、リネーム処理はスキップされました。")
        return original_name

    # 1. 論文 (要件 6.1)
    elif category == "論文":
        year = data.get("year", "YYYY")
        authors = data.get("author", "著者名不明")
        title = data.get("title", "タイトル不明")

        authors_short = authors[:15] if len(authors) > 15 else authors
        max_title_len = 50 - len(year) - len(authors_short) - 2
        title_short = title[:max(0, max_title_len)]

        new_name_raw = f"{year}_{authors_short}_{title_short}"
        return f"{sanitize_filename(new_name_raw)}{ext}"

    # 2. 請求書・領収書 (要件 6.2)
    elif category == "請求書・領収書":
        date_str_raw = data.get("invoice_date", "YYYYMMDD")
        date_str = ''.join(filter(str.isdigit, date_str_raw))[:8]

        issuer = data.get("invoice_issuer", "発行元不明")[:15]
        
        amount_raw = data.get("invoice_amount", "0")
        amount = ''.join(filter(str.isdigit, amount_raw)) or "0" 
        
        subject = data.get("invoice_subject", "件名なし")[:15]

        new_name_raw = f"{date_str}_{issuer}_{amount}_{subject}"
        return f"{sanitize_filename(new_name_raw)}{ext}"

    # 3. その他 (要件 6.3)
    elif category == "その他":
        title = data.get("title", "AI推測タイトル")[:30]
        return f"{sanitize_filename(title)}{ext}"
    
    # 予期せぬ分類エラー
    else:
        st.error(f"🚨 リネームルール適用エラー: カテゴリ '{category}' またはデータ構造が不正です。元のファイル名を返します。")
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
        help="Google AI Studioで取得したAPIキーを入力してください。未入力の場合はモック応答を使用します。"
    )
    
    # APIクライアントの初期化
    client = None
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            st.success("APIキーが設定されました。Gemini APIを使用して分析します。")
        except Exception as e:
             st.error(f"APIキーが無効です: {e}")
             api_key = None 
    
    if not api_key:
        st.warning("APIキーが未設定です。デモのためモック応答で処理を実行します。")
    
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
    "ファイルをアップロード (複数選択可)", 
    type=['pdf', 'docx', 'xlsx', 'pptx', 'csv', 'mp3', 'wav', 'm4a'],
    accept_multiple_files=True
)

if uploaded_files:
    if st.button("🚀 AIリネーム・文字起こしを実行", use_container_width=True):
        
        # 処理状況の表示 (要件 3)
        st.subheader("📊 処理結果")
        results: List[Dict[str, Any]] = []
        
        progress_bar = st.progress(0)
        
        with st.empty(): 
            for i, uploaded_file in enumerate(uploaded_files):
                
                progress_bar.progress((i + 1) / len(uploaded_files))
                st.info(f"👉 **{uploaded_file.name}** の処理を開始...")
                
                # 1. テキスト抽出/ASR判定
                text_content, is_asr = extract_text(uploaded_file)
                
                if "対応していません" in text_content or "エラー" in text_content:
                    results.append({
                        "オリジナルファイル名": uploaded_file.name,
                        "処理状況": "スキップ/エラー",
                        "分類カテゴリ": "-",
                        "リネーム後ファイル名": uploaded_file.name,
                    })
                    continue
                
                # 2. AIコア連携 (Gemini API またはモック)
                ai_response = None
                
                if client:
                    # 実際のAPI呼び出し
                    ai_response = get_ai_core_response(client, text_content, uploaded_file, is_asr)
                else:
                    # モック呼び出し
                    st.warning("⚠️ APIキーがないため、モック応答を使用します。")
                    ai_response = get_ai_core_response_mock(text_content, uploaded_file, is_asr)
                
                if ai_response.category == "不明":
                    st.error(f"❌ ファイル {uploaded_file.name} の処理に失敗しました。理由: {ai_response.reasoning}")

                # 3. リネームルール適用 (要件 6)
                new_filename = apply_rename_rule(ai_response, uploaded_file.name)
                
                # 4. 結果の記録とダウンロードボタンの設置
                result_data = {
                    "オリジナルファイル名": uploaded_file.name,
                    "処理状況": "完了" if ai_response.category != "不明" else "失敗",
                    "分類カテゴリ": ai_response.category,
                    "リネーム後ファイル名": new_filename,
                }
                results.append(result_data)
                
                st.markdown(f"**結果 ({uploaded_file.name})**:")
                
                col1, col2, col3 = st.columns([1, 1, 2])
                
                with col1:
                    st.download_button(
                        label=f"💾 {new_filename} をダウンロード",
                        data=uploaded_file.getvalue(), 
                        file_name=new_filename,
                        mime=uploaded_file.type,
                        key=f"download_renamed_{uploaded_file.name}"
                    )

                if is_asr and ai_response.transcript:
                    with col2:
                        asr_file_name = f"{os.path.splitext(uploaded_file.name)[0]}.txt"
                        st.download_button(
                            label=f"📝 {asr_file_name} ダウンロード",
                            data=ai_response.transcript,
                            file_name=asr_file_name,
                            mime="text/plain",
                            key=f"download_asr_{uploaded_file.name}"
                        )
                
                with col3:
                    st.caption(f"分類: **{ai_response.category}** | 理由: {ai_response.reasoning}")

            st.success("✅ 全ファイルの処理が完了しました！")

        st.dataframe(results, use_container_width=True)
        
        st.markdown("---")
        st.subheader("💡 最終AI分析結果 (構造化データ)")
        if 'ai_response' in locals() and ai_response:
            # Pydanticモデルを辞書に変換して表示
            st.json(ai_response.model_dump())
        else:
            st.write("ファイルが処理されていません。")
