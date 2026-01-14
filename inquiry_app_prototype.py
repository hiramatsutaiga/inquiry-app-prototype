"""
Inquiry-Based English Learning App (v21.1 - Bug Fix & Theme Save)

[FIX] v21.1 - Fixes critical bugs from v21.0 and implements theme saving.
              R1: (Bug Fix) Corrected typo "1.to" to "1.0" in 
                  save_summary_card(). Window now saves and closes correctly.
              R2: (Feature) UserProfile.load/save now handles 'theme_history'.
              R3: (Feature) Image data is converted to/from Base64 for
                  JSON serialization.
              R4: (Feature) save_or_update_theme() now properly saves
                  theme data to the profile.
              R5: (Note) Chat history (conversation_history) is NOT saved
                  in themes to keep profile.json file size manageable.
"""
import os, re, io, sys, base64, tempfile, threading, random, json,time
import tkinter as tk
from tkinter import messagebox, filedialog, Toplevel, scrolledtext
import PIL.Image
from PIL import Image, ImageTk
import cv2
import google.generativeai as genai
from google.cloud import vision

# --- v10.4 (Monolithic) Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- Config (from environment variables) ---
# Vision: set GOOGLE_APPLICATION_CREDENTIALS to your service account json path
# Gemini: set GEMINI_API_KEY to your API key
SERVICE_ACCOUNT_KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account_key.json")

# IMPORTANT: No hard-coded keys in source code
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Optional: allow model override via env
MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "models/gemini-2.5-flash")

PROFILE_FILE = "profile.json"  # v21.0


class APIClients:
    def __init__(self):
        # Gemini model (create once)
        self.gemini_model = genai.GenerativeModel(MODEL_NAME)
        # Vision client (create once)
        self.vision_client = vision.ImageAnnotatorClient()

    def start_chat(self, history=None):
        return self.gemini_model.start_chat(history=history or [])

    def label_detection(self, image_bytes: bytes):
        image = vision.Image(content=image_bytes)
        response = self.vision_client.label_detection(image=image)
        if response.error.message:
            raise Exception(response.error.message)
        return [l.description for l in response.label_annotations]

def read_txt(filename: str) -> str:
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return ""
    for enc in ("utf-8", "utf-8-sig", "cp932"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"

def render_prompt(filename: str, **kwargs) -> str:
    template = read_txt(filename)
    if not template:
        return ""
    return template.format_map(_SafeDict(kwargs))

def build_prompt_from_file(filename: str, fallback_text: str, **kwargs) -> str:
    template = render_prompt(filename, **kwargs)
    if template:
        return f"{template}\n\n{fallback_text}"
    return fallback_text
def parse_question_choices(text: str):
    if not text:
        return "", []
    m = re.search(r"QUESTION:\s*(.+?)\s*CHOICES:\s*(.+)", text, re.S | re.I)
    if not m:
        return "", []
    question = m.group(1).strip()
    choices_raw = m.group(2).strip()
    m2 = re.search(r"\bANSWER\s*:", choices_raw, re.I)
    if m2:
        choices_raw = choices_raw[:m2.start()].strip()
    choices = [c.strip() for c in re.findall(r"\[(.*?)\]", choices_raw)]
    return question, choices



try:
    if os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = SERVICE_ACCOUNT_KEY_PATH
        print("Vision API Service Account Loaded.")
    else:
        print(f"Warning: Vision API key '{SERVICE_ACCOUNT_KEY_PATH}' not found. Vision API will fail.")
except Exception as e:
    print(f"Error loading service account: {e}")

try:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Gemini initialization failed: {e}")
    sys.exit(1)
# --- End of v10.4 Setup ---


# [MOD] v21.1 (R2, R3)
# ユーザープロファイルを管理するクラス
class UserProfile:
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = self.load()

    def get_default_profile(self):
        return {
            "grade": "3-4年生",
            "current_level": "CEFR A1", 
            "coins": 0,
            "theme_history": [] # v21.1: テーマ履歴を保存
        }

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"Profile loaded from {self.file_path}")
                    
                    defaults = self.get_default_profile()
                    for key, value in defaults.items():
                        if key not in data:
                            data[key] = value
                            
                    # [NEW] v21.1 (R3): Base64画像をバイナリに戻す
                    if "theme_history" in data:
                        for theme in data["theme_history"]:
                            if "image_data_b64" in theme:
                                theme["image_data"] = base64.b64decode(theme["image_data_b64"])
                                # del theme["image_data_b64"] # ロード後は不要
                    
                    return data
            except Exception as e:
                print(f"Error loading profile: {e}. Loading defaults.")
                return self.get_default_profile()
        else:
            print("No profile found. Creating new one.")
            return self.get_default_profile()

    def save(self):
        try:
            # [NEW] v21.1 (R3): 画像データをBase64に変換
            # theme_historyは巨大になる可能性があるため、コピーして操作する
            data_to_save = self.data.copy()
            if "theme_history" in data_to_save:
                # 既存のテーマ履歴を（念のため）ディープコピー
                saved_themes = []
                for theme in data_to_save["theme_history"]:
                    new_theme = theme.copy()
                    if "image_data" in new_theme and isinstance(new_theme["image_data"], bytes):
                        new_theme["image_data_b64"] = base64.b64encode(new_theme["image_data"]).decode('utf-8')
                        del new_theme["image_data"] # バイナリデータは削除
                    
                    # word_sessions内のバイナリデータも処理（もしあれば）
                    if "word_sessions" in new_theme:
                        for word, session in new_theme["word_sessions"].items():
                            if "history" in session:
                                del session["history"] # 会話履歴は保存しない
                    
                    saved_themes.append(new_theme)
                
                data_to_save["theme_history"] = saved_themes

            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=4, ensure_ascii=False)
                print(f"Profile saved to {self.file_path}")
        except Exception as e:
            print(f"Error saving profile: {e}")
            
    def get(self, key):
        return self.data.get(key)
        
    def set(self, key, value):
        self.data[key] = value

    def add_coins(self, amount):
        if "coins" not in self.data:
            self.data["coins"] = 0
        self.data["coins"] += amount
        return self.data["coins"]


# v21.0から変更なし
def get_master_prompt(grade, student_level, context_image=None, context_keyword=None, vision_labels=None):
    
    if grade in ["小学生以下", "1-2年生"]:
        choice_prompt = "You MUST provide 3 choices, like this: CHOICES: [[Choice 1],[Choice 2],[Choice 3]]"
    else:
        choice_prompt = "Do not provide choices."

    if context_keyword:
        master_prompt_text = f"""
You are an AI guide for inquiry-based English learning.
The student's grade is: {grade}.
The student's estimated English level is: {student_level}.

Your task is to ask **one single, open-ended question** to start a conversation based on the keyword: '{context_keyword}'.
The question MUST be appropriate for the student's level ({student_level}).

**CRITICAL RULES:**
- **Use simple English**, appropriate for the student's level.
- The question MUST connect the keyword to a wider topic, such as **environmental problems, social studies (how society works), or interesting trivia**.
- **Example 1 (Keyword 'grass'):** Ask "How do plants like grass help our planet?" (Environmental)
- **Example 2 (Keyword 'car'):** Ask "How do cars change the way people live in a city?" (Social Studies)

{choice_prompt}

After your English response, you MUST provide a Japanese translation.
Format it EXACTLY like this (with the [TRANSLATION] tag):

(Your English question...)
{choice_prompt}

[TRANSLATION]
(ここに日本語訳...)
"""
    else:
         master_prompt_text = f"You are a helpful assistant. Please talk in simple English. (Grade: {grade}) (Level: {student_level}) (No keyword provided)"
    initial_context = f"Keyword: {context_keyword}" if context_keyword else "Keyword: (none)"
    if vision_labels:
        vision_context = f"Vision labels: {', '.join(vision_labels)}"
    else:
        vision_context = "Vision labels: (none)"

    master_prompt_text = build_prompt_from_file(
        "prompt_master.txt",
        master_prompt_text,
        grade=grade,
        guide_level=student_level,
        initial_context=initial_context,
        vision_context=vision_context
    )


    if context_image is not None:
        return [master_prompt_text, context_image]
    return [master_prompt_text]

class InquiryApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        
        self.profile = UserProfile(PROFILE_FILE)
        self.api = APIClients()
        
        master.title(f"Inquiry English App (v21.1 — Profile: {self.profile.get('current_level')})")
        master.geometry("800x900")
        
        self.grade = self.profile.get("grade")
        self.student_level = self.profile.get("current_level") 
        self.coins = self.profile.get("coins")

        self.image_data = None; self.initial_image_data = None; self.initial_image_path = ""
        self.initial_image_labels = []; self.current_vision_labels = []
        self.conversation_phase = "conversation"
        self.conversation_history = []
        self.chat_session = None
        
        self.current_english_text = ""
        self.current_story_text = ""
        self.current_story_translation = ""
        
        self.current_theme_title = "" 
        # [MOD] v21.1 (R2) プロファイルからテーマ履歴をロード
        self.theme_history = self.profile.get("theme_history")
        self.theme_photo_references = []
        
        self.quiz_data = [] 
        self.current_quiz_index = 0
        self.total_quizzes_to_generate = 6 
        self.correct_answer = "" 
        self.current_quiz_results = [] 
        
        self.selected_word = None
        self.used_words_in_current_theme = set() 
        
        self.word_select_frame = None; self.word_select_buttons = []
        self.temp_files = []; self.temp_mission_data = None
        
        self.current_daily_mission_word = "dog" 
        self._get_next_daily_mission()
        
        self.summary_creator_window = None
        
        self.setup_ui_v21()
        # [MOD] v21.1 (R4) 
        # もしプロファイルにテーマ履歴が1つ以上あれば、
        # 設定画面ではなくテーマタブから開始する（デバッグ用）
        if self.theme_history:
            self.switch_frame(self.theme_frame)
            self.show_theme_history_page() # UIを再描画
        else:
            self.switch_frame(self.settings_frame)

    def _get_next_daily_mission(self):
        missions = ["dog", "cat", "tree", "car", "book", "flower", "house", "food"]
        self.current_daily_mission_word = random.choice(missions)
        
        if hasattr(self, 'home_button'):
            self.home_button.config(text=f'[Home (Target: "{self.current_daily_mission_word}")]')

    # v21.0から変更なし
    def setup_ui_v21(self):
        # 1. (TOP) 常時表示ステータスバー
        self.status_bar_frame = tk.Frame(self.master, relief=tk.SUNKEN, borderwidth=2, bg="#F0F0F0")
        self.status_bar_frame.pack(fill=tk.X, side=tk.TOP, ipady=5)
        
        self.level_label = tk.Label(self.status_bar_frame, text=f"Level: {self.student_level}", 
                                    font=("", 12, "bold"), bg="#F0F0F0")
        self.level_label.pack(side=tk.LEFT, padx=20)
        
        self.coins_label = tk.Label(self.status_bar_frame, text=f"Coins: {self.coins} 🪙", 
                                    font=("", 12, "bold"), bg="#F0F0F0")
        self.coins_label.pack(side=tk.RIGHT, padx=20)
        
        # 2. (BOTTOM) ナビゲーションバー
        bottom_nav_frame = tk.Frame(self.master, relief=tk.RAISED, borderwidth=1)
        bottom_nav_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        
        self.view_themes_button = tk.Button(bottom_nav_frame, text="[Theme Tab]",
                                            command=self.show_theme_history_page, state=tk.NORMAL)
        self.view_themes_button.pack(side=tk.LEFT, padx=10, pady=5)
        
        self.home_button = tk.Button(bottom_nav_frame, text=f'[Home (Target: "{self.current_daily_mission_word}")]',
                                     command=self.go_to_photo_selection)
        self.home_button.pack(side=tk.LEFT, padx=10, pady=5)
        
        self.exit_button = tk.Button(bottom_nav_frame, text="[Save & Exit]", command=self.on_exit)
        self.exit_button.pack(side=tk.RIGHT, padx=10, pady=5)

        # 3. (CENTER) メインコンテンツフレーム
        self.main_content_frame = tk.Frame(self.master)
        self.main_content_frame.pack(fill=tk.BOTH, expand=True)

        # 4. 思考中ラベル (マスターに配置)
        self.thinking_label = tk.Label(self.master, text="AI is thinking...", font=("", 14, "bold"),
                                       bg="yellow", fg="black", relief=tk.RAISED, borderwidth=2)
        self.thinking_label.place_forget()
        
        # 5. 各画面のフレームを初期化 (self.main_content_frame の子にする)
        self.settings_frame = tk.Frame(self.main_content_frame)
        self.photo_frame = tk.Frame(self.main_content_frame)
        self.conversation_frame = tk.Frame(self.main_content_frame)
        self.content_frame = tk.Frame(self.main_content_frame)
        self.theme_frame = tk.Frame(self.main_content_frame)
        
        # (Settings Frame setup)
        tk.Label(self.settings_frame, text="あなたの学年を選んでください:", font=("", 14)).pack()
        grade_buttons_frame = tk.Frame(self.settings_frame); grade_buttons_frame.pack()
        self.grade_vars = {}
        grade_options = {"Under 6 (Pre-K)": "小学生以下", "Grade 1-2": "1-2年生", "Grade 3-4": "3-4年生", "Grade 5-6": "5-6年生"}
        for text, value in grade_options.items():
            btn = tk.Button(grade_buttons_frame, text=text,
                            command=lambda v=value: self.select_setting("grade", v))
            btn.pack(side=tk.LEFT, padx=5, pady=5); self.grade_vars[value] = btn
        
        tk.Label(self.settings_frame, text="あなたの今の英語レベルを選んでください:", font=("", 14)).pack(pady=(10,0))
        level_buttons_frame = tk.Frame(self.settings_frame); level_buttons_frame.pack()
        self.level_vars = {}
        level_options = {"CEFR Pre-A1": "CEFR Pre-A1", "CEFR A1": "CEFR A1", "CEFR A2": "CEFR A2"}
        for key, display_text in level_options.items():
            btn = tk.Button(level_buttons_frame, text=display_text,
                            command=lambda k=key: self.select_setting("level", k))
            btn.pack(side=tk.LEFT, padx=5, pady=5); self.level_vars[key] = btn
        
        self.current_settings_label = tk.Label(self.settings_frame, text="", fg="blue"); self.current_settings_label.pack(pady=5)
        tk.Button(self.settings_frame, text="Start - Select Photo",
                  command=self.go_to_photo_selection, font=("", 12, "bold")).pack(pady=20)
        
        self.select_setting("grade", self.grade)
        self.select_setting("level", self.student_level)
        
        # (Photo Frame setup) - v11.9から変更なし
        self.photo_frame_title = tk.Label(self.photo_frame, text="", font=("", 20, "bold"),
                                          fg="blue", justify=tk.CENTER); self.photo_frame_title.pack(pady=20, padx=20)
        self.photo_display_label = tk.Label(self.photo_frame, text="Photo will be shown here",
                                            relief="solid", padx=150, pady=100); self.photo_display_label.pack(pady=10)
        photo_buttons_frame = tk.Frame(self.photo_frame); photo_buttons_frame.pack(pady=5)
        tk.Button(photo_buttons_frame, text="Select from File", command=self.load_image_from_file).pack(side=tk.LEFT, padx=5)
        tk.Button(photo_buttons_frame, text="Use Webcam", command=self.open_webcam).pack(side=tk.LEFT, padx=5)
        self.start_buttons_frame = tk.Frame(self.photo_frame); self.start_buttons_frame.pack(pady=10)
        self.start_conv_vision_btn = tk.Button(self.start_buttons_frame, text="Start with Vision API (Detailed)",
                                               command=self.start_inquiry, state=tk.DISABLED, font=("", 12, "bold"))
        self.start_conv_vision_btn.pack(side=tk.LEFT, padx=10)
        self.start_conv_no_vision_btn = tk.Button(self.start_buttons_frame, text="Start (Faster)",
                                                  command=self.start_inquiry_no_vision, state=tk.DISABLED, font=("", 12))
        self.start_conv_no_vision_btn.pack(side=tk.LEFT, padx=10)
        
        # (Conversation Frame setup) - v11.9から変更なし
        conv_main_frame = tk.Frame(self.conversation_frame); conv_main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.conversation_photo_label = tk.Label(conv_main_frame, text="Photo", relief="solid", width=20, height=10)
        self.conversation_photo_label.pack(side=tk.LEFT, padx=10, anchor=tk.NW)
        conv_chat_frame = tk.Frame(conv_main_frame); conv_chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chat_history_text = tk.Text(conv_chat_frame, wrap=tk.WORD, state=tk.DISABLED, height=25, font=("", 11))
        self.chat_history_text.pack(pady=10, fill=tk.BOTH, expand=True)
        input_frame = tk.Frame(conv_chat_frame); input_frame.pack(fill=tk.X, padx=10, pady=5)
        self.user_input_entry = tk.Entry(input_frame, width=60, font=("", 11))
        self.user_input_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.user_input_entry.bind("<Return>", self.send_message_event)
        self.send_button = tk.Button(input_frame, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT, padx=5)
        self.conversation_choice_frame = tk.Frame(conv_chat_frame, pady=5)
        self.conversation_choice_frame.pack()
        self.go_to_story_button = tk.Button(conv_chat_frame, text="[Go to Story & Quiz]", 
                                            font=("", 12, "bold"), command=self.go_to_story_quiz)
        self.go_to_story_button.pack(pady=10)
        
        # (Content Frame setup) - v11.9から変更なし
        self.content_frame_title = tk.Label(self.content_frame, text="Today's Story & Quiz", font=("", 16, "bold"), justify=tk.CENTER)
        self.content_frame_title.pack(pady=(10, 5))
        self.quiz_story_display_frame = tk.Frame(self.content_frame)
        self.quiz_story_display_frame.pack(fill=tk.BOTH, expand=True)
        self.story_text_widget = tk.Text(self.quiz_story_display_frame, wrap=tk.WORD, state=tk.DISABLED, height=10, font=("", 12))
        self.story_text_widget.pack(pady=5, padx=20, fill=tk.X)
        self.start_quiz_button = tk.Button(self.quiz_story_display_frame, text="[Start Quizzes]",
                                            font=("", 12, "bold"), command=self.start_quizzes)
        self.start_quiz_button.pack(pady=10)
        self.start_quiz_button.pack_forget() 
        tk.Frame(self.quiz_story_display_frame, height=2, bg="gray").pack(fill=tk.X, padx=20, pady=10)
        self.quiz_question_label = tk.Label(self.quiz_story_display_frame, text="", font=("", 14), justify=tk.LEFT, wraplength=750)
        self.quiz_question_label.pack(pady=(5, 10), padx=20, anchor=tk.W)
        self.quiz_input_frame = tk.Frame(self.quiz_story_display_frame)
        self.quiz_input_frame.pack(pady=5, padx=20)
        self.quiz_answer_entry = tk.Entry(self.quiz_input_frame, font=("", 14), width=30)
        self.quiz_answer_entry.pack(side=tk.LEFT, padx=10)
        self.quiz_submit_button = tk.Button(self.quiz_input_frame, text="Submit", font=("", 11, "bold"),
                                            command=self.check_quiz_answer)
        self.quiz_submit_button.pack(side=tk.LEFT, padx=10)
        self.quiz_hint_label = tk.Label(self.quiz_story_display_frame, text="", font=("", 11), fg="gray")
        self.quiz_hint_label.pack(pady=5)
        self.quiz_feedback_label = tk.Label(self.quiz_story_display_frame, text="", font=("", 12, "bold"), fg="blue")
        self.quiz_feedback_label.pack(pady=10, padx=20)
        self.next_step_button = tk.Button(self.quiz_story_display_frame, text="Next", font=("", 12, "bold"),
                                          command=self.on_next_quiz_step)
        self.next_step_button.pack(pady=20)
        self.next_step_button.pack_forget() 
        self.quiz_question_label.pack_forget()
        self.quiz_input_frame.pack_forget()
        self.quiz_hint_label.pack_forget()
        self.quiz_feedback_label.pack_forget()
        self.continue_inquiry_frame = tk.Frame(self.content_frame)
        self.continue_inquiry_frame.pack(fill=tk.BOTH, expand=True)
        self.content_word_picker_frame = None

    # v21.0から変更なし
    def switch_frame(self, target_frame):
        if self.content_frame.winfo_ismapped():
            self.clear_content_frame()
        
        for frame in self.main_content_frame.winfo_children():
            frame.pack_forget()
            
        target_frame.pack(fill=tk.BOTH, expand=True)

    # v21.0から変更なし
    def update_status_bar(self):
        if hasattr(self, 'level_label'):
            self.level_label.config(text=f"Level: {self.student_level}")
        if hasattr(self, 'coins_label'):
            self.coins_label.config(text=f"Coins: {self.coins} 🪙")
            
    # v21.0から変更なし
    def add_coins(self, amount):
        self.coins = self.profile.add_coins(amount)
        print(f"Added {amount} coins. Total: {self.coins}")
        self.update_status_bar() 


    def set_display_photo(self, image_data):
        photo = None
        if image_data:
            try:
                # [MOD] v21.1 (R3) image_dataがbytesであることを確認
                if isinstance(image_data, bytes):
                    img = PIL.Image.open(io.BytesIO(image_data))
                    img.thumbnail((150,150))
                    photo = ImageTk.PhotoImage(img)
                else:
                    print(f"Error: image_data is not bytes (type: {type(image_data)})")
            except Exception as e:
                print(f"Error creating thumbnail: {e}")
        lbl = self.conversation_photo_label
        if photo:
            lbl.config(image=photo, text="", width=150, height=150); lbl.image = photo
        else:
            lbl.config(image=None, text="Photo", width=20, height=10); lbl.image = None

    # v21.0から変更なし
    def select_setting(self, setting_type, value):
        if setting_type == "grade":
            self.grade = value
            self.profile.set("grade", value) 
            for _, btn in self.grade_vars.items(): btn.config(relief=tk.RAISED)
            if value in self.grade_vars: self.grade_vars[value].config(relief=tk.SUNKEN)
        elif setting_type == "level":
            self.student_level = value
            self.profile.set("current_level", value) 
            for _, btn in self.level_vars.items(): btn.config(relief=tk.RAISED)
            if value in self.level_vars: self.level_vars[value].config(relief=tk.SUNKEN)
            
        self.update_settings_label()
        self.update_status_bar() 

    # v21.0から変更なし
    def update_settings_label(self):
        grade_text = None; level_text = None
        for text, btn in self.grade_vars.items():
            if btn.cget("relief") == "sunken": grade_text = text; break
        for text, btn in self.level_vars.items():
            if btn.cget("relief") == "sunken": level_text = text; break

        if grade_text and level_text:
            self.current_settings_label.config(text=f"Current Setting: Grade: {grade_text}, Level: {level_text}")

    # v21.0から変更なし
    def go_to_photo_selection(self):
        self.switch_frame(self.photo_frame)
        self.conversation_phase = "conversation"
        self.image_data = None; self.initial_image_data = None; self.initial_image_path = ""
        self.initial_image_labels = []; self.current_vision_labels = []
        self.conversation_history = [] 
        self.chat_session = None
        self.used_words_in_current_theme = set()
        self.quiz_data = []
        self.current_quiz_index = 0
        self.current_theme_title = "" 
        
        self.current_story_text = ""
        self.current_story_translation = ""
        
        self.chat_history_text.config(state=tk.NORMAL)
        self.chat_history_text.delete('1.0', tk.END)
        self.chat_history_text.config(state=tk.DISABLED)
        self.user_input_entry.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL)
        self.go_to_story_button.pack(pady=10) 
        self.photo_frame_title.config(text="Start a New Inquiry\nSelect a photo or use Webcam")
        self.start_conv_vision_btn.config(state=tk.DISABLED); self.start_conv_no_vision_btn.config(state=tk.DISABLED)
        self.photo_display_label.config(image=None, text="Photo will be shown here"); self.photo_display_label.image = None
        if self.word_select_frame is not None:
            self.word_select_frame.pack_forget()

    # v21.0から変更なし
    def get_image_bytes(self, path):
        with open(path, "rb") as f: return f.read()

    # v21.0から変更なし
    def display_image(self, path):
        try:
            img = PIL.Image.open(path); img.thumbnail((300,200)); self.photo = ImageTk.PhotoImage(img)
            self.photo_display_label.config(image=self.photo, text=""); self.photo_display_label.image = self.photo
        except Exception as e:
            messagebox.showerror("Image Error", f"Error displaying image: {e}")
            self.photo_display_label.config(image=None, text="Image display error")

    # v21.0から変更なし
    def load_image_from_file(self):
        path = filedialog.askopenfilename(filetypes=[("Image Files","*.png;*.jpg;*.jpeg;*.gif;*.bmp")])
        if not path: return
        self.display_image(path); self.image_data = self.get_image_bytes(path)
        self.initial_image_data = self.image_data; self.initial_image_path = path
        self.start_conv_vision_btn.config(state=tk.NORMAL); self.start_conv_no_vision_btn.config(state=tk.NORMAL)

    # v21.0から変更なし
    def open_webcam(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened(): messagebox.showerror("Webcam Error","Could not access webcam."); return
        win = Toplevel(self.master); win.title("Webcam (Press C to Capture, Q to Quit)")
        lbl = tk.Label(win); lbl.pack()
        def update():
            ret, frame = cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = PIL.Image.fromarray(frame_rgb); img_tk = ImageTk.PhotoImage(image=img)
                lbl.imgtk = img_tk; lbl.configure(image=img_tk); lbl.after(10, update)
            else:
                cap.release(); win.destroy()
        def capture(_=None):
            ret, frame = cap.read()
            if ret:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg"); p = tmp.name; tmp.close()
                cv2.imwrite(p, frame); self.temp_files.append(p)
                cap.release(); win.destroy()
                self.display_image(p); self.image_data = self.get_image_bytes(p)
                self.initial_image_data = self.image_data; self.initial_image_path = p
                self.start_conv_vision_btn.config(state=tk.NORMAL); self.start_conv_no_vision_btn.config(state=tk.NORMAL)
            else:
                cap.release(); win.destroy()
        def quit_cam(_=None): cap.release(); win.destroy()
        win.bind('c', capture); win.bind('q', quit_cam); update()
        win.transient(self.master); win.grab_set(); self.master.wait_window(win)

    # v21.0から変更なし
    def show_thinking(self, message="AI is thinking..."):
        self.thinking_label.config(text=message); self.thinking_label.lift()
        self.thinking_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.send_button.config(state=tk.DISABLED)
        self.go_to_story_button.config(state=tk.DISABLED) 
        self.start_conv_vision_btn.config(state=tk.DISABLED); self.start_conv_no_vision_btn.config(state=tk.DISABLED)
        self.view_themes_button.config(state=tk.DISABLED)
        self.home_button.config(state=tk.DISABLED) 
        if hasattr(self, 'quiz_submit_button'):
            self.quiz_submit_button.config(state=tk.DISABLED)
            self.quiz_answer_entry.config(state=tk.DISABLED)
        if hasattr(self, 'start_quiz_button'):
            self.start_quiz_button.config(state=tk.DISABLED)
        if self.summary_creator_window and self.summary_creator_window.winfo_exists():
            pass 

    # v21.0から変更なし
    def hide_thinking(self):
        self.thinking_label.place_forget()
        self.view_themes_button.config(state=tk.NORMAL)
        self.home_button.config(state=tk.NORMAL) 
        if self.conversation_phase == "conversation":
            self.send_button.config(state=tk.NORMAL)
            self.go_to_story_button.config(state=tk.NORMAL) 
            self.user_input_entry.config(state=tk.NORMAL)
        elif self.conversation_phase == "select_photo" and self.image_data:
            self.start_conv_vision_btn.config(state=tk.NORMAL); self.start_conv_no_vision_btn.config(state=tk.NORMAL)
        elif self.conversation_phase == "quiz":
            if hasattr(self, 'start_quiz_button'):
                self.start_quiz_button.config(state=tk.NORMAL)
            if self.next_step_button.winfo_ismapped() == False:
                self.quiz_submit_button.config(state=tk.NORMAL)
                self.quiz_answer_entry.config(state=tk.NORMAL)
        if self.summary_creator_window and self.summary_creator_window.winfo_exists():
            pass 

    # v21.0から変更なし
        # v21.1: Threaded API runner with simple 429 retry handling
        # v21.1: Threaded API runner with simple 429 retry handling
    def run_api_in_thread(self, api_func, on_complete_callback, args=(), kwargs=None, message="AI is thinking..."):
        if kwargs is None:
            kwargs = {}
        self.show_thinking(message)

        def worker():
            max_retries = 1  # set to 0 to disable retries
            for attempt in range(max_retries + 1):
                try:
                    result = api_func(*args, **kwargs)
                    self.master.after(0, self.on_api_complete, result, on_complete_callback)
                    return
                except Exception as e:
                    err = str(e)

                    # 429 の場合のみリトライ
                    if "429" in err and ("retry_delay" in err or "quota" in err or "rate" in err) and attempt < max_retries:
                        m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", err)
                        wait_s = int(m.group(1)) if m else 5
                        self.master.after(0, lambda s=wait_s, a=attempt + 1: self.show_thinking(f"Rate limited. Waiting {s}s... (retry {a}/{max_retries})"))
                        time.sleep(wait_s)
                        continue

                    # 429以外、またはリトライ上限に達したら即エラーを返す
                    self.master.after(0, self.on_api_complete, e, on_complete_callback)
                    return

        threading.Thread(target=worker, daemon=True).start()

  

    



    # v21.0から変更なし
    def on_api_complete(self, result, callback_func):
        self.hide_thinking()
        if isinstance(result, Exception):
            print(f"API Thrwead Error: {result}"); messagebox.showerror("API Error", f"{result}"); callback_func(None)
        else:
            callback_func(result)

    # --- v21.0 API functions (変更なし) ---
    def api_get_image_labels(self, image_data):
        try:
           labels = self.api.label_detection(image_data)
           print(f"[DEBUG] Vision API Labels: {labels}")
           return labels
        except Exception as e:
            print(f"Vision API Error: {e}")
            return []


    def api_start_inquiry(self, image_data=None, keyword=None, vision_labels=None):
        
        if image_data:
            img = PIL.Image.open(io.BytesIO(image_data))
            prompt_parts = get_master_prompt(self.grade, self.student_level, context_image=img, context_keyword=keyword, vision_labels=vision_labels)
        elif keyword:
            prompt_parts = get_master_prompt(self.grade, self.student_level, context_keyword=keyword, vision_labels=vision_labels)
        else:
            raise ValueError("image_data or keyword is required.")
        self.chat_session = self.api.start_chat(history=[])
        resp = self.chat_session.send_message(prompt_parts)
        self.conversation_history = self.chat_session.history
        return resp.text
        
        

    def api_continue_conversation(self, user_reply):
        
        if self.chat_session is None:
            self.chat_session = self.api.start_chat(history=self.conversation_history or [])
        
        if self.grade in ["小学生以下", "1-2年生"]:
            choice_prompt = "You MUST provide 3 new choices for this question, like this: CHOICES: [[Choice 1],[Choice 2],[Choice 3]]"
        else:
            choice_prompt = "Do not provide choices."
        
        inquiry_prompt = f"""
The user's last reply was: "{user_reply}"
The student's level is: {self.student_level}.

Continue the inquiry-based conversation.
1. Briefly acknowledge their reply.
2. Ask **one new, open-ended, inquiry-based question** to deepen their thinking (about **environment, social studies, or trivia**).
3. Keep the conversation flowing and use **simple English, appropriate for {self.student_level}**.
- **Example (Social):** User: "Cars are fast." AI: "That's true! But what happens to a town when many cars are used?"
- **Example (Env):** User: "I like trees." AI: "Trees are great! How do trees help keep the air clean?"

{choice_prompt}

CRITICAL: After your English response, you MUST provide a Japanese translation.
Format it EXACTLY like this (with the [TRANSLATION] tag):

(Your English response...)
{choice_prompt}

[TRANSLATION]
(ここに日本語訳...)

"""
        resp = self.chat_session.send_message(inquiry_prompt)
        self.conversation_history = self.chat_session.history
        return resp.text
    def api_generate_quizzes_bulk(self, story_chat_history, total_quizzes, previous_quiz_questions):
        chat = self.api.start_chat(history=story_chat_history)
        prompt = f"""
You are creating exactly {total_quizzes} short quizzes about the story in our chat history.
Rules:
- Allowed types: "True/False" or "Fill-in-the-blank".
- Avoid duplicate or near-duplicate questions. Do NOT repeat these questions: {previous_quiz_questions}
- Difficulty must match a {self.student_level} student.
- True/False: choices must be ["True","False"], answer is "True" or "False".
- Fill-in-the-blank: include a blank like "___" and 3-4 concise choices; answer must exactly match one choice.
- Return JSON ONLY (no prose/markdown/code fences).
- JSON schema (exact keys):
{{"quizzes":[{{"type":"True/False","question":"...","choices":["True","False"],"answer":"True"}},{{"type":"Fill-in-the-blank","question":"... ___ ...","choices":["choice1","choice2","choice3"],"answer":"choice1"}}]}}
- quizzes list length must be exactly {total_quizzes}.
"""
        prompt = build_prompt_from_file(
            "prompt_quiz.txt",
            prompt,
            grade=self.grade,
            english_for_prompt=self.current_story_text or "(no story)"
        )

        resp = chat.send_message(prompt)
        raw = resp.text.strip()
        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            try:
                start = raw.find("{"); end = raw.rfind("}")
                if start != -1 and end != -1:
                    parsed = json.loads(raw[start:end+1])
            except Exception:
                parsed = None

        if not parsed or "quizzes" not in parsed:
            raise ValueError("Failed to parse quiz JSON from Gemini response.")

        quizzes_out = []
        for item in parsed.get("quizzes", []):
            q_type = (item.get("type") or "").strip()
            question = (item.get("question") or "").strip()
            choices = item.get("choices") or []
            answer = (item.get("answer") or "").strip()
            if q_type and question and choices and answer:
                quizzes_out.append({"q": question, "c": choices, "a": answer, "type": q_type})

        if len(quizzes_out) != total_quizzes:
            raise ValueError(f"Expected {total_quizzes} quizzes but got {len(quizzes_out)}.")

        print(f"[DEBUG] Bulk quiz generation: received {len(quizzes_out)} quizzes in one call.")
        return quizzes_out
    def api_generate_tag_choices(self):
        fallback_prompt = f"""
Based on the story below, create a single question and 3-5 keyword choices.
QUESTION: ...
CHOICES: [Choice1],[Choice2],[Choice3]

STORY:
{self.current_story_text}
"""
        prompt = build_prompt_from_file(
            "prompt_tag.txt",
            fallback_prompt,
            english_for_prompt=self.current_story_text,
            grade=self.grade,
            guide_level=self.student_level
        )
        chat = self.api.start_chat(history=[])
        resp = chat.send_message(prompt)
        return resp.text

    def api_generate_mission_choices(self):
        fallback_prompt = f"""
Based on the story below, ask which keyword the student wants to photograph next.
Use keywords from the story and include [ホームに戻る].
QUESTION: ...
CHOICES: [Choice1],[Choice2],[ホームに戻る]

STORY:
{self.current_story_text}
"""
        prompt = build_prompt_from_file(
            "prompt_mission.txt",
            fallback_prompt,
            english_for_prompt=self.current_story_text,
            grade=self.grade,
            guide_level=self.student_level
        )
        chat = self.api.start_chat(history=[])
        resp = chat.send_message(prompt)
        return resp.text



    def api_generate_story(self):
        
    

        # >>> INSERT: CEFR-based story control (ここから)
        level_rules = {
            "CEFR Pre-A1": "Write 3–4 sentences, 4–7 words each. Use only very simple words (dog, tree, book, happy, play). No conjunctions. One idea per sentence.",
            "CEFR A1":     "Write 5–6 sentences, 6–10 words each. Use simple present/past and allow 'and' or 'but'. Keep vocabulary at A1.",
            "CEFR A2":     "Write 6–8 sentences, 8–12 words each. Include at least one sentence with 'because' or 'so', and one comparative adjective (bigger/stronger/healthier). A2-level vocabulary only."
        }
        level_rule = level_rules.get(self.student_level, "")
        # >>> INSERT: CEFR-based story control (ここまで)

        story_prompt = f"""
[CEFR rule] {level_rule}

Based on the **ideas and themes** from our conversation (e.g., environment, social studies), create an educational **5 to 6 sentence** English story.
The story must be written at a {self.student_level} level.
The story should be creative but **provide a learning point**.

**CRITICAL RULE:** After the English story, you MUST provide a Japanese translation.
Format it EXACTLY like this (with the [TRANSLATION] tag):

(English Story...)

[TRANSLATION]
(ここに日本語訳...)
"""
        story_prompt = build_prompt_from_file(
            "prompt_content.txt",
            story_prompt,
            english_for_prompt="(use chat history)",
            grade=self.grade,
            guide_level=self.student_level
        )

         
        

        chat = self.api.start_chat(history=self.conversation_history)
        resp = chat.send_message(story_prompt)
        self.conversation_history = chat.history
        return resp.text, self.conversation_history
        
    

    def api_generate_summary_guidance(self, session_data):
        
        story = session_data.get("story", "No story.")
        quiz_summary = []
        for q in session_data.get("quizzes", []):
            quiz_summary.append(f"- {q['q']} (Answer: {q['a']})")
        quiz_text = "\n".join(quiz_summary)

        guidance_prompt = f"""
You are an AI assistant helping a student fill out their summary card.
The student's grade is {self.grade}.
Your task is to write a short, friendly message (in **simple Japanese**) to the student.
Guide them by asking **one thinking question for each of the first 3 fields**.

Here is the data from their learning session:
---
**STORY:**
{story}

**QUIZZES THEY TOOK:**
{quiz_text}
---

**Example Output (Must be in Japanese):**
"このトピックの学習、おつかれさま！
カードを埋めるために、こんなことを考えてみよう：

1.  **事実:** どんな「こと」（事実）を学んだかな？（ストーリーやクイズに出てきたことなど）
2.  **気持ち/解決策:** この話でどんな「きもち」になったかな？ 私たちにできる小さな「かいけつさく」はあるかな？
3.  **新しい視点:** この勉強で、なにか「あたらしいかんがえ」は生まれた？
4.  **参考:** ばっちりだね！ ほかにも、どこでこれについて学べるかな？（としょかん、はくぶつかんなど）"
"""
        chat = self.api.start_chat(history=[]); resp = chat.send_message(guidance_prompt)
        return resp.text
    # --- End of API functions ---

    # v21.0から変更なし
    def append_chat(self, speaker, message):
        self.chat_history_text.config(state=tk.NORMAL)
        if speaker == "AI (訳)":
            self.chat_history_text.tag_configure("jp_trans", foreground="blue", lmargin1=10, lmargin2=10)
            self.chat_history_text.insert(tk.END, f"[{speaker}]: {message}\n\n", "jp_trans")
        else:
            self.chat_history_text.insert(tk.END, f"[{speaker}]: {message}\n\n")
            
        self.chat_history_text.see(tk.END); self.chat_history_text.config(state=tk.DISABLED)

    # v21.0から変更なし
    def start_inquiry(self):
        if not self.image_data:
            messagebox.showwarning("No Photo", "Please select or capture a photo to start."); return
        self.switch_frame(self.conversation_frame); self.set_display_photo(self.image_data)
        self.conversation_phase = "conversation" 
        self.run_api_in_thread(self.api_get_image_labels, self.handle_vision_response,
                               args=(self.image_data,), message="Analyzing image tags (Vision API)...")

    # v21.0から変更なし
    def start_inquiry_no_vision(self):
        if not self.image_data:
            messagebox.showwarning("No Photo", "Please select or capture a photo to start."); return
        self.switch_frame(self.conversation_frame); self.set_display_photo(self.image_data)
        self.conversation_phase = "conversation"
        if self.initial_image_labels:
            self.show_word_picker(self.initial_image_labels)
        else:
            messagebox.showwarning("Analyze First","No labels yet. Click 'Start with Vision API' to analyze.")

    # v21.0から変更なし
    def handle_vision_response(self, labels):
        if labels is None:
            labels = []
        self.initial_image_labels = labels; self.current_vision_labels = labels
        self.append_chat("System", f"[Vision API analysis complete. Labels: {labels}]")
        self.show_word_picker(labels)

    # v21.0から変更なし
    def _build_words_from_labels(self, labels, limit=10):
        seen = set(); words = []
        for lab in labels or []:
            w = str(lab).replace("_"," ").split(",")[0].strip().lower()
            if 2 <= len(w) <= 30 and w not in seen:
                seen.add(w); words.append(w)
            if len(words) >= limit: break
        if len(words) < 3:
            for w in ["animal","object","place","person","food","plant","color","shape","material","action"]:
                if w not in seen:
                    words.append(w); seen.add(w)
                if len(words) >= limit: break
        return words

    # v21.0から変更なし
    def _ensure_word_select_frame(self):
        if self.word_select_frame is None:
            self.word_select_frame = tk.Frame(self.conversation_frame)
        for w in self.word_select_frame.winfo_children(): w.destroy()
        self.word_select_buttons = []

    # v21.0から変更なし
    def show_word_picker(self, labels):
        self._ensure_word_select_frame()
        tk.Label(self.word_select_frame, text="Choose a keyword to start the conversation",
                 font=("",12,"bold")).pack(pady=6)
        btns = tk.Frame(self.word_select_frame); btns.pack(pady=4)
        words = self._build_words_from_labels(labels, limit=10)
        
        words = [w for w in words if w not in self.used_words_in_current_theme][:10]
        
        if not words:
            tk.Label(self.word_select_frame, text="No new labels found. Try another photo.", fg="red").pack()
        else:
            for w in words:
                b = tk.Button(btns, text=w, width=18, command=lambda x=w: self.on_word_selected(x))
                b.pack(side=tk.LEFT, padx=4, pady=4); self.word_select_buttons.append(b)
        self.word_select_frame.pack(pady=8)

    # v21.0から変更なし
    def on_word_selected(self, word):
        self.selected_word = word
        self.used_words_in_current_theme.add(word) 
        self.append_chat("System", f"[Start from '{word}']")
        
        if not self.current_theme_title:
            self.current_theme_title = word
        if self.word_select_frame:
            self.word_select_frame.pack_forget()
        
        self.run_api_in_thread(self.api_start_inquiry, self.handle_initial_ai_response,
                             args=(self.initial_image_data, word, self.initial_image_labels), 
                               message="Starting conversation (Gemini)...")

    # v21.0から変更なし
    def _parse_and_display_choices(self, ai_response):
        for w in self.conversation_choice_frame.winfo_children():
            w.destroy()
            
        choices_match = re.search(r"CHOICES: \[(.+?)\](?:\n|$)", ai_response, re.DOTALL | re.IGNORECASE)
        if choices_match:
            choices_str = choices_match.group(1).strip()
            choices = []
            if re.search(r"\[.+?\]", choices_str):
                choices = [c.strip() for c in re.findall(r"\[([^\]]+)\]", choices_str)]
            else:
                choices = [c.strip() for c in choices_str.split(',')]
            
            if choices:
                choice_label = tk.Label(self.conversation_choice_frame, 
                                        text=f"Hint (Choices): {', '.join(choices)}", 
                                        font=("", 10, "italic"), fg="gray")
                choice_label.pack()
                ai_response = re.sub(r"CHOICES: \[(.+?)\](?:\n|$)", "", ai_response, flags=re.DOTALL | re.IGNORECASE)
        
        return ai_response.strip()

    # v21.0から変更なし
    def _parse_translation(self, ai_response_cleaned):
        if "[TRANSLATION]" in ai_response_cleaned:
            parts = ai_response_cleaned.split("[TRANSLATION]", 1)
            english_chat = parts[0].strip()
            japanese_chat = parts[1].strip()
        else:
            english_chat = ai_response_cleaned.strip()
            japanese_chat = None
        return english_chat, japanese_chat

    # v21.0から変更なし
    def handle_initial_ai_response(self, ai_response):
        if not ai_response:
            self.append_chat("System", "Error: No response from AI.")
            return
        
        ai_response_cleaned = self._parse_and_display_choices(ai_response)
        english_chat, japanese_chat = self._parse_translation(ai_response_cleaned)
        
        if english_chat:
            self.append_chat("AI", english_chat)
            if japanese_chat:
                self.append_chat("AI (訳)", japanese_chat)
        elif self.conversation_choice_frame.winfo_children():
            pass
        else:
             self.append_chat("AI", "(AI did not respond, please try again.)")

    # v21.0から変更なし
    def handle_ai_response(self, ai_response):
        if not ai_response:
            self.append_chat("System", "Error: No response from AI.")
            return

        ai_response_cleaned = self._parse_and_display_choices(ai_response)
        english_chat, japanese_chat = self._parse_translation(ai_response_cleaned)

        if english_chat:
            self.append_chat("AI", english_chat)
            if japanese_chat:
                self.append_chat("AI (訳)", japanese_chat)
        elif self.conversation_choice_frame.winfo_children():
            pass 
        else:
             self.append_chat("AI", "(AI did not respond, please try again.)")

    # v21.0から変更なし
    def send_message_event(self, _): self.send_message()
    
    # v21.0から変更なし
    def send_message(self):
        msg = self.user_input_entry.get().strip()
        if not msg: return
        self.append_chat("You", msg); self.user_input_entry.delete(0, tk.END)
        
        for w in self.conversation_choice_frame.winfo_children():
            w.destroy()
            
        if self.conversation_phase == "conversation":
            self.run_api_in_thread(self.api_continue_conversation, self.handle_ai_response,
                                   args=(msg,), message="AI is thinking...")
        else:
            print(f"Warning: Text input ignored during '{self.conversation_phase}' phase.")

    # v21.0から変更なし
    def go_to_story_quiz(self):
        self.conversation_phase = "quiz" 
        self.user_input_entry.config(state=tk.DISABLED)
        self.send_button.config(state=tk.DISABLED)
        self.go_to_story_button.pack_forget() 
        
        for w in self.conversation_choice_frame.winfo_children():
            w.destroy()
            
        self.switch_frame(self.content_frame)
        self.quiz_story_display_frame.pack(fill=tk.BOTH, expand=True) 
        self.continue_inquiry_frame.pack_forget() 
        
        self.current_quiz_results = []
        self.current_story_text = ""
        self.current_story_translation = ""
        
        try:
            self.run_api_in_thread(self.api_generate_story, self.handle_story_response,
                                   message="Generating English story (Gemini)...")
        except Exception as e:
            self.append_chat("System", f"[Error in story generation: {e}]")

    def show_theme_history_page(self):
        self.switch_frame(self.theme_frame)
        
        for widget in self.theme_frame.winfo_children():
            widget.destroy()
            
        tk.Label(self.theme_frame, text="Saved Themes", font=("", 16, "bold")).pack(pady=10)

        # [MOD] v21.1 (R2) self.theme_history はロード済み
        if not self.theme_history:
            tk.Label(self.theme_frame, text="No themes have been saved yet.").pack(pady=20)
            
            # [NEW] v21.1: もしテーマがなくても、設定画面（レベル選択）に戻らず
            # 新しい写真を選ぶためのボタンを表示する
            tk.Button(self.theme_frame, text="Start your first theme!",
                      font=("", 12, "bold"),
                      command=self.go_to_photo_selection).pack(pady=20)
            return

        self.theme_photo_references = []
        
        canvas = tk.Canvas(self.theme_frame)
        scrollbar = tk.Scrollbar(self.theme_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side="left", fill="both", expand=True, padx=10)
        scrollbar.pack(side="right", fill="y")

        for i, theme in enumerate(self.theme_history):
            theme_entry_frame = tk.Frame(scrollable_frame, relief=tk.RAISED, borderwidth=2, pady=10)
            theme_entry_frame.pack(fill=tk.X, expand=True, pady=5, padx=10)
            
            header_frame = tk.Frame(theme_entry_frame)
            header_frame.pack(fill=tk.X)
            
            try:
                # [MOD] v21.1 (R3) 
                # theme["image_data"] はロード時にバイナリに戻されている
                if "image_data" not in theme or not isinstance(theme["image_data"], bytes):
                    raise ValueError("Image data not found or invalid type")
                    
                img = PIL.Image.open(io.BytesIO(theme["image_data"]))
                img.thumbnail((100, 100))
                photo = ImageTk.PhotoImage(img)
                self.theme_photo_references.append(photo) 
                
                img_label = tk.Label(header_frame, image=photo, relief="solid")
                img_label.pack(side=tk.LEFT, padx=10)
            except Exception as e:
                print(f"Error loading theme image: {e}")
                tk.Label(header_frame, text="[Image Error]", relief="solid", width=10, height=5).pack(side=tk.LEFT, padx=10)

            tk.Label(header_frame, text=f"Theme: \"{theme['title']}\"", font=("", 14, "bold")).pack(side=tk.LEFT, anchor=tk.W, padx=10)

            tk.Frame(theme_entry_frame, height=2, bg="gray").pack(fill=tk.X, padx=10, pady=(10, 5)) 

            words_frame = tk.Frame(theme_entry_frame)
            words_frame.pack(fill=tk.X, padx=10)
            
            tk.Label(words_frame, text="Review Studied Words:", font=("", 11, "italic"), fg="gray").pack(anchor=tk.W)
            word_buttons_studied = tk.Frame(words_frame)
            word_buttons_studied.pack(fill=tk.X)
            
            # [MOD] v21.1: word_sessions が存在しない場合を考慮
            studied_words = theme.get("word_sessions", {}).keys()
            if not studied_words:
                tk.Label(word_buttons_studied, text="No words studied for this theme yet.", fg="gray").pack(anchor=tk.W, pady=2)
            else:
                for word in studied_words:
                    b = tk.Button(word_buttons_studied, text=word, fg="gray",
                                  command=lambda t=theme, w=word: self.show_review_page(t, w))
                    b.pack(side=tk.LEFT, padx=4, pady=4)

            tk.Label(words_frame, text="Start New Inquiry:", font=("", 11, "italic"), fg="green").pack(anchor=tk.W, pady=(10,0))
            word_buttons_new = tk.Frame(words_frame)
            word_buttons_new.pack(fill=tk.X)

            all_labels = set(self._build_words_from_labels(theme.get('all_labels', []), limit=10))
            available_words = all_labels - set(studied_words)
            
            if not available_words:
                tk.Label(word_buttons_new, text="No more new keywords available for this theme.", fg="gray").pack(anchor=tk.W, pady=2)
            else:
                for word in available_words:
                    b = tk.Button(word_buttons_new, text=word, fg="green",
                                  command=lambda t=theme, w=word: self.on_word_selected_from_theme_tab(t, w))
                    b.pack(side=tk.LEFT, padx=4, pady=4)

    # v21.0から変更なし
    def show_review_page(self, theme, word):
        try:
            session_data = theme["word_sessions"][word]
        except KeyError:
            messagebox.showerror("Error", "Could not find the session data for this word.")
            return

        win = Toplevel(self.master)
        win.title(f"Review: {theme['title']} - {word}")
        win.geometry("700x700") 
        
        main_frame = tk.Frame(win)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        canvas = tk.Canvas(main_frame)
        scrollbar = tk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas)
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scrollbar.pack(side="right", fill="y")

        tk.Label(scrollable_frame, text="Story:", font=("", 14, "bold")).pack(anchor=tk.W, pady=(5,0))
        story_text = session_data.get("story", "No story recorded.")
        tk.Label(scrollable_frame, text=story_text, wraplength=650, justify=tk.LEFT).pack(anchor=tk.W, pady=5)
        
        story_translation = session_data.get("story_translation", "(No translation recorded.)")
        tk.Label(scrollable_frame, text="日本語訳:", font=("", 12, "bold"), fg="blue").pack(anchor=tk.W, pady=(10,0))
        tk.Label(scrollable_frame, text=story_translation, wraplength=650, justify=tk.LEFT, fg="blue").pack(anchor=tk.W, pady=5)
        
        tk.Frame(scrollable_frame, height=2, bg="gray").pack(fill=tk.X, pady=10)

        tk.Label(scrollable_frame, text="Quizzes:", font=("", 14, "bold")).pack(anchor=tk.W, pady=5)
        quizzes = session_data.get("quizzes", [])
        user_answers = session_data.get("user_answers", [])
        
        if not quizzes:
            tk.Label(scrollable_frame, text="No quizzes recorded.").pack(anchor=tk.W)
        
        for i, quiz in enumerate(quizzes):
            q_text = f"{i+1}. {quiz['q']}"
            a_text = f"Correct Answer: {quiz['a']}"
            user_a = user_answers[i] if i < len(user_answers) else "N/A"
            
            if user_a.lower() == quiz['a'].lower():
                result_text = f"Your Answer: {user_a} (Correct)"
                result_color = "green"
            else:
                result_text = f"Your Answer: {user_a} (Incorrect)"
                result_color = "red"
                
            tk.Label(scrollable_frame, text=q_text, font=("", 12)).pack(anchor=tk.W, pady=(10,0))
            tk.Label(scrollable_frame, text=a_text, fg="blue").pack(anchor=tk.W)
            tk.Label(scrollable_frame, text=result_text, fg=result_color).pack(anchor=tk.W)

        tk.Frame(scrollable_frame, height=2, bg="gray").pack(fill=tk.X, pady=10)
        tk.Label(scrollable_frame, text="Summary Card:", font=("", 14, "bold")).pack(anchor=tk.W, pady=5)
        
        if "summary_card" in session_data:
            summary_data = session_data["summary_card"]
            
            def create_summary_field(parent, label_text, content_text, color_fg, color_bg):
                tk.Label(parent, text=label_text, font=("", 12, "bold"), fg=color_fg).pack(anchor=tk.W, pady=(5,0))
                tk.Label(parent, text=content_text or "(Not filled)", 
                         wraplength=650, justify=tk.LEFT, 
                         bg=color_bg, anchor=tk.W, relief=tk.SOLID, borderwidth=1, padx=5, pady=5).pack(fill=tk.X)

            create_summary_field(scrollable_frame, 
                                 "1. Facts you learned (学んだ「事実」):", 
                                 summary_data.get("field1"), 
                                 "#D2691E", "#FFF8DC")
            
            create_summary_field(scrollable_frame, 
                                 "2. Your feelings or solutions (感じた「気持ち」や「解決策」):", 
                                 summary_data.get("field2"), 
                                 "#4682B4", "#F0F8FF")

            create_summary_field(scrollable_frame, 
                                 "3. New perspectives or ideas (新しい「視点」や「考え」):", 
                                 summary_data.get("field3"), 
                                 "#DB7093", "#FFF0F5")
            
            create_summary_field(scrollable_frame, 
                                 "4. Where can you learn more? (「参考」や「もっと知りたいこと」):", 
                                 summary_data.get("field4"), 
                                 "#CD5C5C", "#FFF5EE")
            
            button_text = "[View/Edit Summary Card]"
        else:
            tk.Label(scrollable_frame, text="(No summary card created yet.)", fg="gray").pack(anchor=tk.W)
            button_text = "[Create Summary Card]"

        summary_button = tk.Button(win, text=button_text, font=("", 12, "bold"),
                                   command=lambda s=session_data: self.open_summary_creator(s))
        summary_button.pack(pady=10, side=tk.BOTTOM)

        win.transient(self.master)
        win.grab_set()

    # v21.0から変更なし
    def on_word_selected_from_theme_tab(self, theme, word):
        answer = messagebox.askyesno(
            "Confirm Action",
            f"You selected the new topic: \"{word}\"\n\n"
            "Do you want to take a NEW photo (or select a file) for this topic?\n\n"
            "(If you select 'No', you will use the photo already saved with this theme.)"
        )
        
        if answer:
            self.go_to_photo_selection()
        else:
            self.start_inquiry_from_theme(theme, word)

    # v21.0から変更なし
    def start_inquiry_from_theme(self, theme, word):
        self.switch_frame(self.conversation_frame)
        
        self.conversation_phase = "conversation"
        self.conversation_history = []
        self.chat_session = None
        self.quiz_data = []
        self.current_quiz_index = 0
        self.current_quiz_results = [] 
        self.current_story_text = ""
        self.current_story_translation = ""

        self.chat_history_text.config(state=tk.NORMAL)
        self.chat_history_text.delete('1.0', tk.END)
        self.chat_history_text.config(state=tk.DISABLED)
        self.user_input_entry.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL)
        self.go_to_story_button.pack(pady=10) 
        
        self.initial_image_data = theme['image_data']
        self.initial_image_labels = theme.get('all_labels', []) # [MOD] v21.1: .get()
        self.current_theme_title = theme['title']
        self.used_words_in_current_theme = set(theme.get("word_sessions", {}).keys()) # [MOD] v21.1
        
        self.set_display_photo(self.initial_image_data)
        
        self.selected_word = word
        self.used_words_in_current_theme.add(word) 
        self.append_chat("System", f"[Continuing theme '{self.current_theme_title}' with new keyword: '{word}']")
        
        self.run_api_in_thread(self.api_start_inquiry, self.handle_initial_ai_response,
                             args=(self.initial_image_data, word, self.initial_image_labels), 
                               message="Starting new topic (Gemini)...")

    # v21.0から変更なし
    def on_exit(self):
        print("Saving profile...")
        self.profile.save() 
        
        for p in self.temp_files:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
        self.master.quit()
        
    # v21.0から変更なし
    def handle_story_response(self, ai_response):
        if not ai_response:
            ai_story_full = "Error: No story generated."
            self.story_chat_history = self.conversation_history 
        else:
            ai_story_full, story_chat_history = ai_response
            self.story_chat_history = story_chat_history 
            
        try:
            if "[TRANSLATION]" in ai_story_full:
                parts = ai_story_full.split("[TRANSLATION]", 1)
                self.current_story_text = parts[0].strip()
                self.current_story_translation = parts[1].strip()
            else:
                self.current_story_text = ai_story_full.strip()
                self.current_story_translation = "(No translation provided by AI.)"
        except Exception as e:
            print(f"Error parsing story translation: {e}")
            self.current_story_text = ai_story_full
            self.current_story_translation = "(Error parsing translation.)"
            
        self.story_text_widget.config(state=tk.NORMAL)
        self.story_text_widget.delete('1.0', tk.END)
        self.story_text_widget.insert(tk.END, self.current_story_text) 
        self.story_text_widget.config(state=tk.DISABLED)
        
        self.append_chat("AI", f"OK, here is today's English story!\n\n{self.current_story_text}")
        
        self.start_quiz_button.pack(pady=10)

    # v21.0から変更なし
    def start_quizzes(self):
        self.start_quiz_button.pack_forget() 
        
        self.quiz_question_label.pack(pady=(5, 10), padx=20, anchor=tk.W)
        self.quiz_input_frame.pack(pady=5, padx=20)
        self.quiz_hint_label.pack(pady=5)
        self.quiz_feedback_label.pack(pady=10, padx=20)
        
        self.quiz_data = [] 
        self.current_quiz_index = 0
        
        self.run_api_in_thread(
            self.api_generate_quizzes_bulk,
            self.handle_quiz_bulk_response,
            kwargs={
                "story_chat_history": self.story_chat_history,
                "total_quizzes": self.total_quizzes_to_generate,
                "previous_quiz_questions": []
            },
            message=f"Creating {self.total_quizzes_to_generate} quizzes (bulk)..."
        )
    def handle_quiz_bulk_response(self, quizzes):
            if not quizzes:
                self.quiz_question_label.config(text="Error: No quiz generated.")
                return

            self.quiz_data = quizzes
            self.append_chat("AI", f"[Bulk quizzes generated: {len(quizzes)} questions]")
            self.show_current_quiz_question()

    
            
    # v21.0から変更なし
    def show_current_quiz_question(self):
        if self.current_quiz_index >= len(self.quiz_data):
            print("Error: show_current_quiz_question called but no more quizzes in data.")
            return
            
        self.quiz_feedback_label.config(text="")
        self.next_step_button.pack_forget()
        self.quiz_hint_label.config(text="") 
        
        self.quiz_answer_entry.config(state=tk.NORMAL)
        self.quiz_answer_entry.delete(0, tk.END)
        self.quiz_submit_button.config(state=tk.NORMAL)
        
        quiz = self.quiz_data[self.current_quiz_index]
        q_type = (quiz.get("type") or "").strip()
        type_tag = "T/F" if q_type.lower().startswith("true") else ("FIB" if q_type.lower().startswith("fill") else "")
        prefix = f"Q{self.current_quiz_index + 1}"
        if type_tag:
            prefix = f"{prefix} [{type_tag}]"
        self.quiz_question_label.config(text=f"{prefix}: {quiz['q']}")

        self.correct_answer = quiz["a"]
        
        if self.grade in ["小学生以下", "1-2年生", "3-4年生"]:
            hint_text = f"Choices: {', '.join(quiz['c'])}"
            self.quiz_hint_label.config(text=hint_text)
        else: 
            self.quiz_hint_label.config(text="")

    # v21.0から変更なし
    def check_quiz_answer(self):
        selected_choice = self.quiz_answer_entry.get().strip()
        normalized = selected_choice.strip().lower()
        if normalized in ("t", "true"):
            selected_choice = "True"
        elif normalized in ("f", "false"):
            selected_choice = "False"

        if not selected_choice: return 

        self.append_chat("You", selected_choice) 
        
        self.current_quiz_results.append(selected_choice)
        
        self.quiz_answer_entry.config(state=tk.DISABLED)
        self.quiz_submit_button.config(state=tk.DISABLED)

        if selected_choice.lower() == self.correct_answer.lower():
            feedback = "Correct! Great job! (+10 Coins 🪙)"
            feedback_color = "green"
            self.add_coins(10) 
        else:
            feedback = f"Sorry, the correct answer was: {self.correct_answer}"
            feedback_color = "red"

        self.quiz_feedback_label.config(text=feedback, fg=feedback_color)
        
        self.current_quiz_index += 1
        if self.current_quiz_index < self.total_quizzes_to_generate:
            self.next_step_button.config(text=f"Next Question ({self.current_quiz_index + 1}/{self.total_quizzes_to_generate})")

        else:
            self.next_step_button.config(text="Finish Quizzes")
            
        self.next_step_button.pack(pady=20)
        
        # v21.0から変更なし
    def on_next_quiz_step(self):
         if self.current_quiz_index < self.total_quizzes_to_generate:
            # すでに生成済みのクイズを進めるだけ（API呼び出しなし）
            self.next_step_button.pack_forget()
            self.quiz_feedback_label.config(text="")
            self.quiz_question_label.config(text="")
            self.quiz_hint_label.config(text="")
            self.show_current_quiz_question()
         else:
            self.save_or_update_theme()
            self.show_next_step_options()

    


    def save_or_update_theme(self):
        if not self.initial_image_data or not self.current_theme_title:
            print("Theme save skipped: No image data or title.")
            return
        if not self.selected_word:
            print("Theme save skipped: No word was selected for this session.")
            return

        existing_theme = None
        for theme in self.theme_history:
             # [MOD] v21.1 (R3) 
             # バイナリデータ（self.initial_image_data）と
             # 既存のテーマのバイナリデータ（theme["image_data"]）を比較
            if "image_data" in theme and theme["image_data"] == self.initial_image_data:
                existing_theme = theme
                break
        
        session_data = {
            # [MOD] v21.1 (R5) 会話履歴は保存しない
            # "history": self.conversation_history, 
            "story": self.current_story_text,
            "story_translation": self.current_story_translation, 
            "quizzes": self.quiz_data,
            "user_answers": self.current_quiz_results
        }
        
        if existing_theme:
            if self.selected_word in existing_theme["word_sessions"] and "summary_card" in existing_theme["word_sessions"][self.selected_word]:
                session_data["summary_card"] = existing_theme["word_sessions"][self.selected_word]["summary_card"]
            
            # [MOD] v21.1: word_sessions がない場合を考慮
            if "word_sessions" not in existing_theme:
                existing_theme["word_sessions"] = {}
                
            existing_theme["word_sessions"][self.selected_word] = session_data
            print(f"Theme '{existing_theme['title']}' updated with session for '{self.selected_word}'.")
        else:
            new_theme = {
                "title": self.current_theme_title, 
                "image_data": self.initial_image_data, # [MOD] v21.1: バイナリデータ
                "all_labels": self.initial_image_labels,
                "word_sessions": {
                    self.selected_word: session_data 
                }
            }
            self.theme_history.append(new_theme)
            print(f"New theme '{self.current_theme_title}' saved.")
        
        # [NEW] v21.1 (R4) プロファイル全体を保存
        self.profile.set("theme_history", self.theme_history)
        self.profile.save()
        
        self._get_next_daily_mission()

    # v21.0から変更なし
    def show_next_step_options(self):
        self.quiz_story_display_frame.pack_forget()
        self.continue_inquiry_frame.pack(fill=tk.BOTH, expand=True)
        for w in self.continue_inquiry_frame.winfo_children():
            w.destroy()
        self.content_word_picker_frame = None
        tk.Label(self.continue_inquiry_frame, text="What would you like to do next?", font=("", 14, "bold")).pack(pady=20)

        self.mission_frame = tk.Frame(self.continue_inquiry_frame)
        self.mission_frame.pack(pady=10, fill=tk.X)
        tk.Label(self.mission_frame, text="Next photo mission", font=("", 12, "bold")).pack()
        self.mission_question_label = tk.Label(self.mission_frame, text="Generating mission...", wraplength=700, justify=tk.LEFT)
        self.mission_question_label.pack(pady=4)
        self.mission_buttons_frame = tk.Frame(self.mission_frame); self.mission_buttons_frame.pack(pady=4)

        self.content_word_picker_frame = tk.Frame(self.continue_inquiry_frame)
        self.content_word_picker_frame.pack(pady=10)
        self.tag_question_label = tk.Label(self.content_word_picker_frame, text="Choose a new keyword to continue exploring this photo:", font=("",12))
        self.tag_question_label.pack(pady=6)
        self.tag_buttons_frame = tk.Frame(self.content_word_picker_frame); self.tag_buttons_frame.pack(pady=4)
        tk.Label(self.tag_buttons_frame, text="Loading tag suggestions...", fg="gray").pack()

        current_theme_used_words = set()
        for theme in self.theme_history:
            if "image_data" in theme and theme["image_data"] == self.initial_image_data:
                current_theme_used_words = set(theme.get("word_sessions", {}).keys())
                break
        self.current_theme_used_words = current_theme_used_words

        self.run_api_in_thread(self.api_generate_tag_choices, self.handle_tag_response,
                               message="Generating tag choices (Gemini)...")
        self.run_api_in_thread(self.api_generate_mission_choices, self.handle_mission_response,
                               message="Generating next mission (Gemini)...")

        tk.Frame(self.continue_inquiry_frame, height=2, bg="gray").pack(fill=tk.X, padx=50, pady=20)
        tk.Button(self.continue_inquiry_frame, text="Start a New Photo", font=("", 12, "bold"),
                  command=self.go_to_photo_selection).pack(pady=10)
    

    def handle_tag_response(self, ai_text):
        if not getattr(self, "tag_buttons_frame", None):
            return
        for w in self.tag_buttons_frame.winfo_children():
            w.destroy()

        question, choices = parse_question_choices(ai_text or "")
        if question:
            self.tag_question_label.config(text=question)

        if not choices:
            words = self._build_words_from_labels(self.initial_image_labels, limit=10)
            words = [w for w in words if w not in self.current_theme_used_words][:10]
            choices = words

        if not choices:
            tk.Label(self.tag_buttons_frame, text="No more keywords available for this photo.", fg="gray").pack()
            return

        for w in choices:
            b = tk.Button(self.tag_buttons_frame, text=w, width=18, command=lambda x=w: self.on_word_selected_from_content(x))
            b.pack(side=tk.LEFT, padx=4, pady=4)

    def handle_mission_response(self, ai_text):
        if not getattr(self, "mission_buttons_frame", None):
            return
        for w in self.mission_buttons_frame.winfo_children():
            w.destroy()

        question, choices = parse_question_choices(ai_text or "")
        if question:
            self.mission_question_label.config(text=question)
        else:
            self.mission_question_label.config(text="Which keyword would you like to photograph next?")

        if not choices:
            choices = re.findall(r"<([^>]+)>", self.current_story_text or "")
            choices = [c.strip() for c in choices if c.strip()]
            if not choices:
                choices = self._build_words_from_labels(self.initial_image_labels, limit=3)

        choices = list(dict.fromkeys(choices))
        if "ホームに戻る" not in choices:
            choices.append("ホームに戻る")

        for c in choices:
            b = tk.Button(self.mission_buttons_frame, text=c, width=18, command=lambda x=c: self.on_mission_choice(x))
            b.pack(side=tk.LEFT, padx=4, pady=4)

        self.temp_mission_data = {"question": question or "", "choices": choices}

    def on_mission_choice(self, choice):
        if "ホームに戻る" in choice:
            self.go_to_photo_selection()
            return
        self.current_daily_mission_word = choice
        if hasattr(self, 'home_button'):
            self.home_button.config(text=f'[Home (Target: "{self.current_daily_mission_word}")]')
        messagebox.showinfo("Next Mission", f'Next mission set to "{choice}"')

    # v21.0から変更なし
    def on_word_selected_from_content(self, word):
        self.switch_frame(self.conversation_frame)
        self.conversation_phase = "conversation"
        self.conversation_history = []
        self.chat_session = None
        self.quiz_data = []
        self.current_quiz_index = 0
        self.current_quiz_results = [] 
        self.current_story_text = ""
        self.current_story_translation = ""

        self.chat_history_text.config(state=tk.NORMAL)
        self.chat_history_text.delete('1.0', tk.END)
        self.chat_history_text.config(state=tk.DISABLED)
        self.user_input_entry.config(state=tk.NORMAL)
        self.send_button.config(state=tk.NORMAL)
        self.go_to_story_button.pack(pady=10) 
        
        self.selected_word = word
        current_theme_used_words = set()
        for theme in self.theme_history:
             if "image_data" in theme and theme["image_data"] == self.initial_image_data:
                current_theme_used_words = set(theme.get("word_sessions", {}).keys())
                break
        self.used_words_in_current_theme = current_theme_used_words
        self.used_words_in_current_theme.add(word) 
        
        self.append_chat("System", f"[Continuing with new keyword: '{word}']")
        
        self.run_api_in_thread(self.api_start_inquiry, self.handle_initial_ai_response,
                             args=(self.initial_image_data, word, self.initial_image_labels), 
                               message="Starting new topic (Gemini)...")
                               
    # v21.0から変更なし
    def clear_content_frame(self):
        self.story_text_widget.config(state=tk.NORMAL)
        self.story_text_widget.delete('1.0', tk.END)
        self.story_text_widget.config(state=tk.DISABLED)
        
        self.start_quiz_button.pack_forget()
        self.quiz_question_label.pack_forget()
        self.quiz_input_frame.pack_forget()
        self.quiz_hint_label.pack_forget()
        self.quiz_feedback_label.pack_forget()
        self.next_step_button.pack_forget()
        
        self.quiz_question_label.config(text="")
        self.quiz_feedback_label.config(text="")
        self.quiz_hint_label.config(text="")
        self.quiz_answer_entry.config(state=tk.NORMAL)
        self.quiz_answer_entry.delete(0, tk.END)
        self.quiz_submit_button.config(state=tk.NORMAL)
        
        self.continue_inquiry_frame.pack_forget()
        if self.content_word_picker_frame:
            for w in self.content_word_picker_frame.winfo_children():
                w.destroy()
        self.quiz_story_display_frame.pack(fill=tk.BOTH, expand=True)

    # v2t.0から変更なし
    def open_summary_creator(self, session_data):
        if self.summary_creator_window and self.summary_creator_window.winfo_exists():
            self.summary_creator_window.lift()
            return
            
        win = Toplevel(self.master)
        win.title(f"Create Summary Card for: {self.selected_word}")
        win.geometry("900x600")
        
        self.summary_creator_window = win 
        win.protocol("WM_DELETE_WINDOW", lambda: setattr(self, 'summary_creator_window', None) or win.destroy())

        main_pane = tk.PanedWindow(win, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        card_frame = tk.Frame(main_pane, relief=tk.RIDGE, borderwidth=2, padx=10, pady=10)
        main_pane.add(card_frame, width=500)

        tk.Label(card_frame, text=f"Theme: {self.current_theme_title} (Topic: {self.selected_word})", font=("", 14, "bold")).pack(anchor=tk.W)
        
        tk.Label(card_frame, text="1. Facts you learned (学んだ「事実」):", font=("", 12, "bold"), fg="#D2691E").pack(anchor=tk.W, pady=(10,0))
        self.card_field_1_text = tk.Text(card_frame, height=4, width=60, font=("", 10), relief=tk.SOLID, borderwidth=1, bg="#FFF8DC")
        self.card_field_1_text.pack(fill=tk.X, expand=True)
        
        tk.Label(card_frame, text="2. Your feelings or solutions (感じた「気持ち」や「解決策」):", font=("", 12, "bold"), fg="#4682B4").pack(anchor=tk.W, pady=(10,0))
        self.card_field_2_text = tk.Text(card_frame, height=4, width=60, font=("", 10), relief=tk.SOLID, borderwidth=1, bg="#F0F8FF")
        self.card_field_2_text.pack(fill=tk.X, expand=True)

        tk.Label(card_frame, text="3. New perspectives or ideas (新しい「視点」や「考え」):", font=("", 12, "bold"), fg="#DB7093").pack(anchor=tk.W, pady=(10,0))
        self.card_field_3_text = tk.Text(card_frame, height=4, width=60, font=("", 10), relief=tk.SOLID, borderwidth=1, bg="#FFF0F5")
        self.card_field_3_text.pack(fill=tk.X, expand=True)
        
        tk.Label(card_frame, text="4. Where can you learn more? (「参考」や「もっと知りたいこと」):", font=("", 12, "bold"), fg="#CD5C5C").pack(anchor=tk.W, pady=(10,0))
        self.card_field_4_text = tk.Text(card_frame, height=4, width=60, font=("", 10), relief=tk.SOLID, borderwidth=1, bg="#FFF5EE")
        self.card_field_4_text.pack(fill=tk.X, expand=True)

        if "summary_card" in session_data:
            saved_summary = session_data["summary_card"]
            self.card_field_1_text.insert("1.0", saved_summary.get("field1", ""))
            self.card_field_2_text.insert("1.0", saved_summary.get("field2", ""))
            self.card_field_3_text.insert("1.0", saved_summary.get("field3", ""))
            self.card_field_4_text.insert("1.0", saved_summary.get("field4", ""))

        tk.Button(card_frame, text="Save & Close", font=("", 12, "bold"), 
                  command=lambda s=session_data, w=win: self.save_summary_card(s, w)).pack(pady=20) 

        ai_frame = tk.Frame(main_pane, relief=tk.RIDGE, borderwidth=2, padx=10, pady=10, bg="#F5F5F5")
        main_pane.add(ai_frame, width=400)
        
        tk.Label(ai_frame, text="AI Assistant", font=("", 14, "bold"), bg="#F5F5F5").pack(pady=5)
        
        self.summary_guidance_text = tk.Text(ai_frame, wrap=tk.WORD, state=tk.DISABLED, font=("", 11), bg="#F5F5F5", relief=tk.FLAT)
        self.summary_guidance_text.pack(fill=tk.BOTH, expand=True)
        
        self.run_api_in_thread(
            self.api_generate_summary_guidance, 
            self.handle_summary_guidance_response,
            args=(session_data,), 
            message="Loading AI assistant..."
        )

        win.transient(self.master)
        win.grab_set()

    def save_summary_card(self, session_data, window):
        try:
            field1 = self.card_field_1_text.get("1.0", tk.END).strip()
            # [FIX] v21.1 (R1) "1.to" -> "1.0"
            field2 = self.card_field_2_text.get("1.0", tk.END).strip()
            field3 = self.card_field_3_text.get("1.0", tk.END).strip()
            field4 = self.card_field_4_text.get("1.0", tk.END).strip()
            
            summary_data = {
                "field1": field1,
                "field2": field2,
                "field3": field3,
                "field4": field4
            }
            
            session_data["summary_card"] = summary_data
            
            self.add_coins(50) 
            print("Summary card data saved. +50 Coins.")
            
            # [NEW] v21.1 (R4) サマリーを保存したら、プロファイル全体も保存
            self.profile.save()
            
            messagebox.showinfo("Saved", "Summary card saved successfully! (+50 Coins 🪙)")
            self.evaluate_session_and_adjust_level(session_data)
            window.destroy()
            self.summary_creator_window = None
    
            

        except Exception as e:
            print(f"Error saving summary card: {e}")
            messagebox.showerror("Error", f"Could not save summary card:\n{e}")

    def evaluate_session_and_adjust_level(self, session_data):
        try:
            # --- 1) 指標 ---
            total = len(self.current_quiz_results)
            correct = 0
            for i, ans in enumerate(self.current_quiz_results):
                if i < len(self.quiz_data):
                    try:
                        if isinstance(ans, str) and ans.lower() == self.quiz_data[i]['a'].lower():
                            correct += 1
                    except Exception:
                        pass
            accuracy = (correct / total) if total > 0 else 0.0

            summary = session_data.get("summary_card", {}) or {}
            filled_fields = sum(1 for f in ("field1","field2","field3","field4") if (summary.get(f) or "").strip())

            # --- 2) レベル判定 ---
            levels = ["CEFR Pre-A1", "CEFR A1", "CEFR A2"]
            current = self.profile.get("current_level") or "CEFR A1"
            if current not in levels:
                current = "CEFR A1"
            idx = levels.index(current)
            new_level = current

            if accuracy >= 0.8 and filled_fields >= 3 and idx < len(levels) - 1:
                new_level = levels[idx + 1]
            elif accuracy < 0.5 and idx > 0:
                new_level = levels[idx - 1]

            # --- 3) 更新と表示 ---
            if new_level != current:
                self.profile.set("current_level", new_level)
                self.profile.save()
                self.student_level = new_level
                self.master.title(f"Inquiry English App (v21.1 — Profile: {new_level})")
                messagebox.showinfo("Level Updated", f"次回のレベルを {current} → {new_level} に更新しました。")
                self.update_status_bar()

            else:
                print("Level unchanged — remains at", current)

        except Exception as e:
            print(f"Error during level evaluation: {e}")
    
    # v21.0から変更なし
    def handle_summary_guidance_response(self, ai_text):
        if not self.summary_creator_window or not self.summary_creator_window.winfo_exists():
            print("Summary window was closed before AI could respond.")
            return

        if not ai_text:
            ai_text = "エラー：AIがヒントを生成できませんでした。覚えていることをもとに、カードを埋めてみてください。"
            
        try:
            self.summary_guidance_text.config(state=tk.NORMAL)
            self.summary_guidance_text.delete('1.0', tk.END)
            self.summary_guidance_text.insert(tk.END, ai_text)
            self.summary_guidance_text.config(state=tk.DISABLED)
        except tk.TclError as e:
            print(f"Error updating summary guidance text (window might be closed): {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = InquiryApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_exit)
    root.mainloop()
