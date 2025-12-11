import streamlit as st
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import json
import os
from pathlib import Path
import re 
import time
import datetime # 날짜 모듈 추가

# --- 1. 환경 설정 ---
st.set_page_config(page_title="Gemini RPG (Save Fix)", layout="wide")

BASE_DIR = Path(__file__).parent
SETTINGS_DIR = BASE_DIR / "settings"

SETTINGS_DIR.mkdir(exist_ok=True)

# --- 2. CSS 스타일 ---
@st.cache_resource
def inject_custom_css():
    st.markdown("""
    <style>
    .floating-hud {
        position: fixed; top: 4rem; right: 1.5rem; width: 380px;
        background-color: rgba(13, 17, 23, 0.95); border: 1px solid #30363d;
        border-radius: 8px; z-index: 99999;
        font-family: 'Pretendard', sans-serif; overflow: hidden;
    }
    .hud-header {
        background-color: #21262d; color: #58a6ff; padding: 10px 15px;
        font-weight: bold; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between;
    }
    .hud-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    .hud-table td { padding: 6px 12px; border-bottom: 1px solid #21262d; vertical-align: middle; }
    .hud-key { color: #8b949e; width: 30%; background-color: rgba(255,255,255,0.02); white-space: nowrap; }
    .hud-val { color: #c9d1d9; font-weight: 600; text-align: right; }
    .stChatMessage { background-color: transparent; }
    .stChatMessage[data-testid="user-message"] { background-color: rgba(59, 130, 246, 0.1); border-left: 3px solid #3B82F6; }
    .stChatMessage[data-testid="assistant-message"] { background-color: rgba(100, 116, 139, 0.1); border-left: 3px solid #64748B; }
    .stButton button { width: 100%; }
    </style>
    """, unsafe_allow_html=True)

# --- 3. 핵심 로직 ---

@st.cache_resource
def get_model(api_key):
    genai.configure(api_key=api_key)
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    generation_config = {
        "temperature": 1.0,
        "response_mime_type": "application/json",
    }
    return genai.GenerativeModel("gemini-2.0-flash", generation_config=generation_config, safety_settings=safety_settings)

@st.cache_data
def load_local_settings(version_trigger=0):
    files_content = {}
    filenames = ["world", "player", "opening", "npcs", "rules", "events", "secrets"]
    for name in filenames:
        file_path = SETTINGS_DIR / f"{name}.txt"
        if file_path.exists():
            files_content[name] = file_path.read_text(encoding="utf-8")
        else:
            files_content[name] = "설정 없음"
    return files_content

def parse_status_string(text):
    parsed = {}
    if "|" in text: 
        for line in text.split('\n'):
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 2 and "---" not in line:
                key, val = parts[0].replace("**", "").replace(":", ""), parts[1]
                if "STATUS" not in key and "Key" not in key: parsed[key] = val
        if parsed: return parsed

    for item in re.split(r'[,\n]', text):
        if ":" in item:
            key, val = item.split(":", 1)
            parsed[key.strip().replace("**", "")] = val.strip()
    return parsed if parsed else {"Info": text}

def render_hud_html(status_data):
    if not status_data: return ""
    final_data = parse_status_string(status_data) if isinstance(status_data, str) else status_data
    if not isinstance(final_data, dict): return ""
    
    content = "".join([f"<tr><td class='hud-key'>{str(k).replace('**','')}</td><td class='hud-val'>{str(v).replace('|','')}</td></tr>" for k, v in final_data.items()])
    return f"""<div class="floating-hud"><div class="hud-header"><span>📊 STATUS</span><span style="font-size:0.8em; color:#8b949e;">Live</span></div><table class="hud-table">{content}</table></div>"""

def get_save_data_json():
    """현재 게임 상태를 JSON 문자열로 변환"""
    if "chat" not in st.session_state:
        return None
        
    raw_history = [{"role": m.role, "parts": m.parts[0].text} for m in st.session_state.chat.history if m.parts]
    save_data = {
        "raw_history": raw_history,
        "story_log": st.session_state.story_log,
        "current_status": st.session_state.current_status,
        "last_choices": st.session_state.last_choices
    }
    return json.dumps(save_data, ensure_ascii=False, indent=2)

def load_game_from_json(json_file, model):
    """업로드된 JSON 데이터를 로드"""
    try:
        # 파일 포인터를 처음으로 되돌림 (중요)
        json_file.seek(0)
        data = json.load(json_file)
        
        st.session_state.story_log = data.get("story_log", [])
        st.session_state.current_status = data.get("current_status", {})
        st.session_state.last_choices = data.get("last_choices", [])
        
        history = [{"role": m["role"], "parts": [m["parts"]]} for m in data["raw_history"]]
        st.session_state.chat = model.start_chat(history=history)
        return True
    except Exception as e:
        st.error(f"로드 실패: {e}")
        return False

def build_system_prompt(files_content):
    return f"""
    당신은 TRPG 게임 마스터입니다. 설정을 시뮬레이션하세요.
    [설정]: {files_content}
    [규칙]
    1. JSON 포맷 필수.
    2. 'story': 소설 형식 서술 (5~10문장).
    3. 'status_hud': 필수 Key("Time", "Location", "Condition", "Stats", "Quest", "Relations", "Skills") 포함 JSON 객체.
    4. 'choices': 선택지 3~4개.
    JSON 양식: {{ "story": "...", "status_hud": {{ ... }}, "choices": ["..."] }}
    """

# --- 4. 메인 실행 ---

inject_custom_css()

# 세션 초기화
if "story_log" not in st.session_state:
    st.session_state.story_log = []
    st.session_state.current_status = {}
    st.session_state.last_choices = []
    st.session_state.api_key = ""
    st.session_state.settings_ver = 0

# 모델 로드 (API 키가 있을 때만)
model = None
if st.session_state.api_key:
    try:
        model = get_model(st.session_state.api_key)
    except Exception as e:
        st.error(f"오류: {e}")

# --- 사이드바 ---
with st.sidebar:
    st.title("⚙️ 메뉴")
    api_input = st.text_input("API Key", value=st.session_state.api_key, type="password")
    if api_input: st.session_state.api_key = api_input
    
    st.markdown("---")
    show_hud = st.toggle("📊 상태창", value=True)
    
    st.markdown("---")
    st.subheader("💾 파일 관리")
    
    # 1. 저장 (다운로드 버튼)
    save_json = get_save_data_json()
    if save_json:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        st.download_button(
            label="📥 세이브 파일 저장 (다운로드)",
            data=save_json,
            file_name=f"rpg_save_{timestamp}.json",
            mime="application/json",
        )
    else:
        st.button("📥 세이브 파일 저장", disabled=True)

    # 2. 로드 (파일 업로더 + 적용 버튼)
    st.markdown("---")
    uploaded_file = st.file_uploader("📤 세이브 파일 불러오기", type=["json"])
    
    # [수정된 로직] 파일이 있고 + 버튼을 눌러야 로드됨
    if uploaded_file is not None:
        if st.button("📂 파일 내용 적용하기 (Load)", type="primary"):
            if model:
                if load_game_from_json(uploaded_file, model):
                    st.toast("✅ 세이브 파일이 성공적으로 로드되었습니다!", icon="🎉")
                    time.sleep(0.5) # 잠시 대기 후 리런
                    st.rerun()
            else:
                st.error("API Key를 먼저 입력해주세요.")

    st.markdown("---")
    if st.button("🗑️ 초기화 (재시작)"):
        st.session_state.clear()
        st.session_state.settings_ver += 1
        st.rerun()

# API 키 확인
if not st.session_state.api_key:
    st.info("API Key를 입력하세요.")
    st.stop()

# 게임 루프 (로드된 것이 없고, 채팅도 없으면 새로 시작)
if "chat" not in st.session_state:
    files = load_local_settings(st.session_state.settings_ver)
    st.session_state.chat = model.start_chat(history=[{"role": "user", "parts": build_system_prompt(files)}])
    
    with st.spinner("🚀 오프닝 생성 중..."):
        try:
            resp = st.session_state.chat.send_message(f"오프닝: {files['opening']}")
            data = json.loads(resp.text)
            st.session_state.story_log.append({"role": "ai", "content": data["story"]})
            st.session_state.current_status = data.get("status_hud", {})
            st.session_state.last_choices = data.get("choices", [])
        except Exception as e:
            st.error(f"오류: {e}")
            st.stop()

# UI 렌더링
if show_hud: st.markdown(render_hud_html(st.session_state.current_status), unsafe_allow_html=True)
st.title("⚔️ Gemini RPG")

for msg in st.session_state.story_log:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

st.markdown("---")
cols = st.columns(len(st.session_state.last_choices))
user_action = None
for idx, choice in enumerate(st.session_state.last_choices):
    if cols[idx].button(choice, key=f"btn_{len(st.session_state.story_log)}_{idx}"): user_action = choice

input_text = st.chat_input("직접 입력...")
if input_text: user_action = input_text

if user_action:
    st.session_state.story_log.append({"role": "user", "content": user_action})
    with st.spinner("진행 중..."):
        try:
            response = st.session_state.chat.send_message(f"행동: {user_action}")
            new_data = json.loads(response.text)
            st.session_state.story_log.append({"role": "ai", "content": new_data["story"]})
            st.session_state.current_status = new_data.get("status_hud", st.session_state.current_status)
            st.session_state.last_choices = new_data.get("choices", [])
            st.rerun()
        except Exception as e:
            st.error(f"통신 오류: {e}")