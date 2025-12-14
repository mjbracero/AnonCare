import os
import re
import difflib
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from docx import Document

app = FastAPI()

# ----------------- CORS (for Flutter / Web / Emulator) -----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- GROQ INIT -----------------
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
if not os.getenv("GROQ_API_KEY"):
    raise RuntimeError("Missing GROQ_API_KEY env var. Set it first.")

# ----------------- DOC PATH -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_PATH = os.path.join(BASE_DIR, "Possible-Empathic-Responding-for-Anoncare-App-Users.docx")

# ----------------- PROMPTS & RULES -----------------
SYSTEM_PROMPT = """
You are AnonCare, a supportive mental health companion for students.
You provide emotional support and low-risk coping suggestions.
You are NOT a licensed mental health professional.

STRICT LIMITATIONS:
- Do NOT diagnose mental health conditions.
- Do NOT provide professional therapy/counseling or treatment plans.
- Do NOT recommend or adjust medications.
- If asked for diagnosis/treatment, briefly refuse and offer safe coping steps + encourage professional help.
- Keep replies warm, relevant, and concise.
- Stay on the user's exact situation. Do NOT change the topic.
"""

OUT_OF_SCOPE_REPLY = (
    "I can only help with mental health and stress-related concerns. "
    "I can’t answer general topics. "
    "If you share what you’re feeling or what’s stressing you out, I’ll support you."
)

CRISIS_KEYWORDS = [
    "suicide", "kill myself", "end my life", "self harm", "self-harm", "hurt myself",
    "magpakamatay", "mamatay", "want to disappear",
    "patyon nako akong kaugalingon", "magpatay ko", "maglaslas", "laslas",
    "i want to die"
]

CRISIS_REPLY = (
    "I'm really sorry you’re feeling this way. You don’t have to face this alone.\n\n"
    "If you’re in immediate danger or might hurt yourself, please seek help **now**:\n"
    "- Call your local emergency number, or go to the nearest ER/hospital.\n"
    "- If you can, reach out to a trusted person (friend/family/teacher) and stay with them.\n\n"
    "Are you in immediate danger right now?"
)

# ----------------- APPROVED BREATHING TECHNIQUES -----------------
BREATHING_TECHNIQUES_TEXT = (
    "Here are two gentle breathing techniques you can try right now:\n\n"
    "**Box Breathing**\n"
    "Inhale slowly through your nose for a count of four.\n"
    "Hold your breath for four seconds.\n"
    "Exhale slowly through your mouth for four seconds.\n"
    "Hold your breath for four seconds.\n"
    "Repeat.\n\n"
    "**4-7-8 Breathing**\n"
    "Place the tip of your tongue against the roof of your mouth.\n"
    "Exhale completely through your mouth with a whooshing sound.\n"
    "Close your mouth and inhale quietly through your nose for a count of four.\n"
    "Hold your breath for a count of seven.\n"
    "Exhale completely through your mouth with a whooshing sound for a count of eight.\n"
    "Repeat the cycle three more times."
)

def user_wants_breathing(user_text: str) -> bool:
    t = user_text.lower()
    triggers = [
        "breathing", "breathe", "box breathing", "4-7-8", "478",
        "panic attack", "panic", "help me calm", "calm down", "hyperventilating"
    ]
    return any(k in t for k in triggers)

# ----------------- ✅ ANXIETY 3-3-3 RULE (NEW) -----------------
ANXIETY_KEYWORDS = [
    "anxiety attack", "anxiety", "panic attack", "panic", "overthinking",
    "can't breathe", "cant breathe", "hirap huminga", "di ko makaginawa", "dili ko makaginawa",
    "grabe akong kaba", "sobrang kaba", "heart is racing", "nahihilo ako sa kaba"
]

ANXIETY_333_TEXT = (
    "Okay, I’m here with you. Let’s try a quick grounding exercise called the **3–3–3 Rule**:\n\n"
    "1) Identify **3 things you can see**\n"
    "2) Identify **3 things you can hear**\n"
    "3) Move **3 parts of your body** (fingers, shoulders, toes)\n\n"
    "Do it slowly. When you’re ready, tell me: which 3 things can you see right now?"
)

def user_wants_anxiety_help(user_text: str) -> bool:
    t = user_text.lower()
    return any(k in t for k in ANXIETY_KEYWORDS)

# ----------------- LANGUAGE -----------------
def looks_cebuano(text: str) -> bool:
    t = text.lower()
    markers = ["unsa", "ngano", "lagi", "kaayo", "nimo", "nako", "karon", "diay", "mao", "kay", "gikapoy", "palihog"]
    hits = sum(1 for m in markers if m in t)
    return hits >= 2

def language_instruction(user_text: str) -> str:
    if looks_cebuano(user_text):
        return (
            "Reply in SIMPLE Cebuano/Bisaya (easy for students).\n"
            "Rules:\n"
            "- Gamita ra simple words.\n"
            "- Short sentences.\n"
            "- Pwede mix ug English (bisag Taglish).\n"
            "- Ayaw deep/poetic words.\n"
            "- Dili lecture. Murag friend nga caring.\n"
        )
    return "Reply in English."

# ----------------- TEXT NORMALIZATION (FOR MATCHING ONLY) -----------------
def norm_text(s: str) -> str:
    s = s.lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def strip_leading_numbering(s: str) -> str:
    return re.sub(r"^\s*\d+\s*[\.\)]\s*", "", s).strip()

def normalize_user_for_match(s: str) -> str:
    t = " " + norm_text(s) + " "
    repl = {
        " acads ": " academics ",
        " acad ": " academic ",
        " bf ": " boyfriend ",
        " gf ": " girlfriend ",
        " prof ": " professor ",
        " req ": " requirement ",
        " reqs ": " requirements ",
        " im ": " i am ",
        " i'm ": " i am ",
        " cant ": " can't ",
        " dont ": " don't ",
    }
    for k, v in repl.items():
        t = t.replace(k, v)
    return t.strip()

# ----------------- DOCX PARSER (FIXED FOR 'a. “quote”' FORMAT) -----------------
def load_doc_library(doc_path: str):
    doc = Document(doc_path)

    raw_lines = []
    for p in doc.paragraphs:
        if not p.text:
            continue
        for part in p.text.splitlines():
            line = part.strip()
            if line:
                raw_lines.append(line)

    def is_quoted_or_bulleted_quote(s: str) -> bool:
        s = s.strip()
        if (s.startswith("“") and s.endswith("”")) or (s.startswith('"') and s.endswith('"')):
            return True
        return bool(re.match(r'^[a-zA-Z]\.\s*(“.*”|".*")\s*$', s))

    def extract_quote_text(s: str) -> str:
        s = s.strip()
        s = re.sub(r"^[a-zA-Z]\.\s*", "", s).strip()
        return s.strip('“”"').strip()

    entries = []
    idx = 1
    current_category = None

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()

        if (not is_quoted_or_bulleted_quote(line)) and (i + 1 < len(raw_lines)) and re.match(r"^\d+\.", raw_lines[i + 1].strip()):
            current_category = line
            i += 1
            continue

        if (not is_quoted_or_bulleted_quote(line)) and (i + 1 < len(raw_lines)) and is_quoted_or_bulleted_quote(raw_lines[i + 1]):
            subcategory = line
            response = extract_quote_text(raw_lines[i + 1])

            if not current_category:
                current_category = "General"

            entries.append({
                "id": str(idx),
                "category": current_category,
                "subcategory": subcategory,
                "response": response
            })
            idx += 1
            i += 2
            continue

        i += 1

    if not entries:
        raise RuntimeError("No subcategory-response pairs found. Check your DOCX formatting.")

    id_to_entry = {e["id"]: e for e in entries}
    return entries, id_to_entry

ENTRIES, ID_TO_ENTRY = load_doc_library(DOC_PATH)

SUBCAT_INDEX = []
for e in ENTRIES:
    sub_clean = strip_leading_numbering(e["subcategory"])
    sub_norm = norm_text(sub_clean)
    SUBCAT_INDEX.append((e["id"], sub_norm, set(sub_norm.split())))

def match_docx_by_text(user_text: str) -> Optional[str]:
    u_norm = norm_text(normalize_user_for_match(user_text))
    u_tokens = set(u_norm.split())
    if not u_norm:
        return None

    for _id, sub_norm, _ in SUBCAT_INDEX:
        if u_norm in sub_norm or sub_norm in u_norm:
            return _id

    best_id = None
    best_score = 0.0
    for _id, _, sub_tokens in SUBCAT_INDEX:
        if not sub_tokens:
            continue
        inter = len(u_tokens & sub_tokens)
        union = len(u_tokens | sub_tokens)
        score = inter / union if union else 0.0
        if score > best_score:
            best_score = score
            best_id = _id

    if best_score >= 0.35:
        return best_id

    best_id = None
    best_ratio = 0.0
    for _id, sub_norm, _ in SUBCAT_INDEX:
        ratio = difflib.SequenceMatcher(None, u_norm, sub_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_id = _id

    return best_id if best_ratio >= 0.55 else None

# ----------------- SCOPE CHECK -----------------
def is_in_scope(user_text: str) -> bool:
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system",
             "content": (
                 "Is the user's message about mental health, stress, emotions, wellbeing, coping, "
                 "relationships affecting wellbeing, school pressure, anxiety/panic feelings, low mood, or seeking emotional support?\n"
                 "Answer ONLY YES or NO."
             )},
            {"role": "user", "content": user_text},
        ],
        temperature=0,
        max_tokens=1,
    )
    return (resp.choices[0].message.content or "").strip().upper() == "YES"

# ----------------- SIMPLE MEMORY (per user_id, user+assistant) -----------------
MAX_MEMORY_TURNS = 8
memory: Dict[str, List[Dict[str, str]]] = {}

def add_to_memory(user_id: str, role: str, text: str):
    memory.setdefault(user_id, [])
    memory[user_id].append({"role": role, "text": text.strip()})
    if len(memory[user_id]) > MAX_MEMORY_TURNS:
        memory[user_id].pop(0)

def memory_context_text(user_id: str) -> str:
    msgs = memory.get(user_id, [])
    if not msgs:
        return "No prior context."
    lines = []
    for m in msgs:
        prefix = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{prefix}: {m['text']}")
    return "\n".join(lines)

def generate_final_reply(user_id: str, user_text: str) -> str:
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": language_instruction(user_text)},
            {"role": "system", "content": (
                "Use the conversation context to avoid repeating the same question.\n"
                "Be responsive to what the user already said.\n"
                "Do not ignore details.\n"
                "If user asks for 'treatment/diagnosis', refuse briefly and give safe coping steps.\n"
                "Keep it short and relevant."
            )},
            {"role": "system", "content": "Conversation context:\n" + memory_context_text(user_id)},
            {"role": "user", "content": user_text},
        ],
        temperature=0.3,
        max_tokens=220,
    )
    return (resp.choices[0].message.content or "").strip()

# ----------------- API SCHEMA -----------------
class ChatRequest(BaseModel):
    user_id: str
    text: str

class ChatResponse(BaseModel):
    reply: str
    used_docx: bool
    category: str

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    user_text = (req.text or "").strip()
    user_id = (req.user_id or "").strip() or "anonymous"

    add_to_memory(user_id, "user", user_text)

    # crisis
    if any(k in user_text.lower() for k in CRISIS_KEYWORDS):
        add_to_memory(user_id, "assistant", CRISIS_REPLY)
        return ChatResponse(reply=CRISIS_REPLY, used_docx=False, category="Crisis Support")

    # scope
    if not is_in_scope(user_text):
        add_to_memory(user_id, "assistant", OUT_OF_SCOPE_REPLY)
        return ChatResponse(reply=OUT_OF_SCOPE_REPLY, used_docx=False, category="Out of Scope")

    # ✅ anxiety shortcut (3-3-3)
    if user_wants_anxiety_help(user_text):
        add_to_memory(user_id, "assistant", ANXIETY_333_TEXT)
        return ChatResponse(reply=ANXIETY_333_TEXT, used_docx=False, category="Anxiety Support")

    # breathing shortcut
    if user_wants_breathing(user_text):
        add_to_memory(user_id, "assistant", BREATHING_TECHNIQUES_TEXT)
        return ChatResponse(reply=BREATHING_TECHNIQUES_TEXT, used_docx=False, category="Breathing Technique")

    # DOCX match
    doc_id = match_docx_by_text(user_text)
    if doc_id and doc_id in ID_TO_ENTRY and ID_TO_ENTRY[doc_id].get("response"):
        entry = ID_TO_ENTRY[doc_id]
        reply = entry["response"]
        category = entry.get("category", "DOCX")
        add_to_memory(user_id, "assistant", reply)
        return ChatResponse(reply=reply, used_docx=True, category=category)

    # AI fallback
    reply = generate_final_reply(user_id, user_text)
    add_to_memory(user_id, "assistant", reply)
    return ChatResponse(reply=reply, used_docx=False, category="AI Generated")

@app.get("/health")
def health():
    return {"ok": True}

# ✅ IMPORTANT: Railway uses PORT env var
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port)
