import os, google.generativeai as genai
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

m = genai.GenerativeModel("models/gemini-2.5-flash")  # ← list_modelsに出た名称をそのまま使う
r = m.generate_content("say ok")
print(r.text)
