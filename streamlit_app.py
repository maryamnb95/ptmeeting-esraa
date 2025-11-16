# streamlit_app.py
# نظام أولياء الأمور - نسخة ويب بـ Streamlit:
# - تسجيل بيانات ولي الأمر
# - اختيار الصف ثم الطالبة
# - إدخال ملاحظات ولي الأمر لكل مادة
# - توليد تقرير PDF (مع تقسيم الملاحظات إلى أسطر)
# - لوحة تحكم للإدارة مع تسجيل دخول وتقرير أولياء الأمور

import os, re, unicodedata, sqlite3, io, base64
import streamlit as st
import pandas as pd
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import arabic_reshaper
from bidi.algorithm import get_display

# ================= إعدادات عامة =================
SCHOOL_NAME = "ثانوية الإسراء بنات"
INSTAGRAM_URL = "https://www.instagram.com/alesraa_highschool/"  # عدّلي هذا بالرابط الصحيح
INSTAGRAM_HANDLE = "@alesraa_highschool"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_LOGO = os.path.join(BASE_DIR, "assets", "logo.PNG")
DB_PATH = os.path.join(BASE_DIR, "data", "school.db")

# كلمة مرور الإدارة (غيّريها كما تريدين أو استخدمي متغير بيئة)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "israa123")

FONT_CANDIDATES = [
    os.path.join(BASE_DIR, "fonts", "NotoNaskhArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "fonts", "Amiri-Regular.ttf"),
    os.path.join(BASE_DIR, "fonts", "DUBAI-BOLD.TTF"),
    os.path.join(BASE_DIR, "fonts", "Dubai-Regular.ttf"),
]

def resolve_font_path() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("أضيفي خطًا عربيًا داخل fonts/ مثل NotoNaskhArabic-Regular.ttf أو Amiri-Regular.ttf")

def ensure_pdf_font():
    font_path = resolve_font_path()
    if "ARFont" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ARFont", font_path))
    return "ARFont", font_path

# ================= أدوات عربية =================
SAFE_REPLACE = {
    "✅": "✔", "⭐": "★",
    "•": "", "·": "",
    "—": "-", "–": "-", "‒": "-",
    "\u00A0": " ", "\u200f": "", "\u200e": "", "\u200b": "",
}

def sanitize_text(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKC", str(t))
    for k, v in SAFE_REPLACE.items():
        t = t.replace(k, v)
    return t

def ar_shape(text: str) -> str:
    if not text:
        return ""
    return get_display(arabic_reshaper.reshape(sanitize_text(text)))

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def strip_invisibles(s) -> str:
    if s is None:
        return ""
    s = str(s)
    return s.replace("\u200f","").replace("\u200e","").replace("\u200b","")\
            .replace("\u2066","").replace("\u2067","").replace("\u2068","").replace("\u2069","")\
            .replace("\u202A","").replace("\u202B","").replace("\u202C","")

def normalize_sid(s: str) -> str:
    s = "" if s is None else str(s)
    s = strip_invisibles(s).strip().translate(AR_DIGITS)
    only_digits = re.sub(r"\D+", "", s)
    return only_digits or s

def normalize_class(c):
    """توحيد شكل اسم الصف (إزالة مسافات إضافية وما حول /)."""
    if not c:
        return ""
    c = str(c).strip()
    c = re.sub(r"\s+", " ", c)
    c = c.replace(" / ", "/").replace(" /", "/").replace("/ ", "/")
    return c

def wrap_ar_lines(text: str, max_width: float, font_name: str, font_size: int, c: canvas.Canvas):
    """
    تقسيم الملاحظة إلى أسطر، كل سطر يحتوي 5 كلمات بالحد الأقصى.
    نعتمد على عدد الكلمات (ليس عرض الصفحة) لسهولة القراءة.
    """
    text = (text or "").strip()
    if not text:
        return []

    words = text.split()
    lines = []
    current = []

    for w in words:
        current.append(w)
        if len(current) == 4:   # 👈 خمس كلمات
            lines.append(" ".join(current))
            current = []

    if current:
        lines.append(" ".join(current))

    return lines

# ================= بيانات من SQLite =================
_STUDENTS_INDEX = {}   # sid -> {name, class}
_CLASS_MAP = {}        # class -> list[{subject, teacher}]
_OVERRIDES = {}        # (sid, subject) -> teacher
_DB_PARENT_NOTES = {}  # (sid, subject) -> note

def load_school_data_from_sqlite(db_path: str):
    _STUDENTS_INDEX.clear(); _CLASS_MAP.clear(); _OVERRIDES.clear(); _DB_PARENT_NOTES.clear()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # students
    cur.execute("SELECT student_id, name, class FROM students")
    for r in cur.fetchall():
        sid = normalize_sid(r["student_id"])
        name = strip_invisibles((r["name"] or "").strip())
        cls_raw = strip_invisibles((r["class"] or "").strip())
        cls = normalize_class(cls_raw)
        if sid and name and cls:
            _STUDENTS_INDEX[sid] = {"name": name, "class": cls}

    # class_subjects
    cur.execute("SELECT class, subject, teacher FROM class_subjects ORDER BY class, subject")
    for r in cur.fetchall():
        cls_raw = strip_invisibles((r["class"] or "").strip())
        cls = normalize_class(cls_raw)
        subject = strip_invisibles((r["subject"] or "").strip())
        teacher = strip_invisibles((r["teacher"] or "").strip())
        if cls and subject and teacher:
            _CLASS_MAP.setdefault(cls, []).append({"subject": subject, "teacher": teacher})

    # student_subjects (اختياري)
    try:
        cur.execute("SELECT student_id, subject, teacher FROM student_subjects")
        for r in cur.fetchall():
            sid = normalize_sid(r["student_id"])
            subj = strip_invisibles((r["subject"] or "").strip())
            teacher = strip_invisibles((r["teacher"] or "").strip())
            _OVERRIDES[(sid, subj)] = teacher
    except sqlite3.OperationalError:
        pass

    # parent_notes (اختياري)
    try:
        cur.execute("SELECT student_id, subject, note FROM parent_notes")
        for r in cur.fetchall():
            sid = normalize_sid(r["student_id"])
            subj = strip_invisibles((r["subject"] or "").strip())
            note = (r["note"] or "").strip()
            _DB_PARENT_NOTES[(sid, subj)] = note
    except sqlite3.OperationalError:
        pass

    conn.close()

def build_student_record_from_db(student_id: str):
    si = _STUDENTS_INDEX.get(student_id)
    if not si:
        return None
    cls_key = normalize_class(si["class"])
    subjects = []
    for row in _CLASS_MAP.get(cls_key, []):
        subj = row["subject"]
        teacher = _OVERRIDES.get((student_id, subj), row["teacher"])
        note = _DB_PARENT_NOTES.get((student_id, subj), "")
        subjects.append({"subject": subj, "teacher": teacher, "parent_note": note})
    return {"name": si["name"], "class": cls_key, "subjects": subjects}

def save_subject_note_to_db(student_id: str, subject: str, note: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_notes(
            student_id TEXT,
            subject TEXT,
            note TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, subject)
        )
    """)
    cur.execute("""
        INSERT INTO parent_notes(student_id, subject, note)
        VALUES (?, ?, ?)
        ON CONFLICT(student_id, subject) DO UPDATE SET
          note = excluded.note,
          updated_at = CURRENT_TIMESTAMP
    """, (student_id, subject, note))
    conn.commit()
    conn.close()

def load_parent_notes_df():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_notes(
            student_id TEXT,
            subject TEXT,
            note TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, subject)
        )
    """)
    cur.execute("SELECT student_id, subject, note, updated_at FROM parent_notes ORDER BY updated_at DESC")
    rows = cur.fetchall()
    conn.close()

    records = []
    for sid_raw, subject, note, updated_at in rows:
        sid_norm = normalize_sid(sid_raw)
        info = _STUDENTS_INDEX.get(sid_norm, {})
        records.append({
            "student_id": sid_norm,
            "student_name": info.get("name", ""),
            "class": info.get("class", ""),
            "subject": subject,
            "note": note,
            "updated_at": updated_at
        })
    if not records:
        return pd.DataFrame(columns=["student_id", "student_name", "class", "subject", "note", "updated_at"])
    return pd.DataFrame(records)

# ===== تسجيل زيارات أولياء الأمور =====
def log_parent_visit(student_id: str, parent_name: str, parent_relation: str):
    """تسجيل اسم ولي الأمر + صلة القرابة + الطالبة في جدول مستقل."""
    if not parent_name.strip():
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_visits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            parent_name TEXT,
            parent_relation TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "INSERT INTO parent_visits(student_id, parent_name, parent_relation) VALUES (?,?,?)",
        (student_id, parent_name.strip(), parent_relation.strip())
    )
    conn.commit()
    conn.close()

def load_parent_visits_df():
    """تحميل تقرير أولياء الأمور الذين سجّلوا."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_visits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            parent_name TEXT,
            parent_relation TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        SELECT student_id, parent_name, parent_relation, created_at
        FROM parent_visits
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    records = []
    for sid, parent_name, parent_rel, created_at in rows:
        sid_norm = normalize_sid(sid)
        info = _STUDENTS_INDEX.get(sid_norm, {})
        records.append({
            "student_id": sid_norm,
            "student_name": info.get("name", ""),
            "class": info.get("class", ""),
            "parent_name": parent_name,
            "parent_relation": parent_rel,
            "created_at": created_at
        })
    if not records:
        return pd.DataFrame(columns=["student_id", "student_name", "class", "parent_name", "parent_relation", "created_at"])
    return pd.DataFrame(records)

# ================= توليد PDF =================
def export_report_A4_pdf_bytes(
    student_id: str,
    student: dict,
    logo_path: str = None,
    school_name: str = "",
    parent_name: str | None = None,
    parent_relation: str | None = None
) -> bytes:
    font_name, _ = ensure_pdf_font()
    page_w, page_h = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    MARGIN = 36
    x_right = page_w - MARGIN
    TOP_Y = page_h - MARGIN
    FOOTER_H = 60
    ROW_LEADING = 22
    NOTE_LEADING = 22
    ROW_GAP = 20
    HEADER_GAP = 14
    LINE_THIN = 0.8
    LINE_BOLD = 1.2

    y = TOP_Y

    # الشعار
    if logo_path and os.path.exists(logo_path):
        try:
            im = Image.open(logo_path)
            w0, h0 = im.size
            target_w = 110
            ratio = target_w / float(w0)
            target_h = h0 * ratio
            c.drawImage(
                logo_path,
                (page_w - target_w) / 2,
                y - target_h,
                width=target_w,
                height=target_h,
                preserveAspectRatio=True,
                mask='auto'
            )
            y -= (target_h + 12)
        except Exception:
            c.setFont(font_name, 10)
            c.setFillColorRGB(0.6, 0.1, 0.1)
            c.drawCentredString(page_w/2, y, ar_shape("تعذّر تحميل الشعار"))
            c.setFillColorRGB(0, 0, 0)
            y -= 14

    # اسم المدرسة والعنوان
    c.setFont(font_name, 18)
    c.setFillColorRGB(0,0,0)
    c.drawCentredString(page_w/2, y, ar_shape(sanitize_text(school_name)))
    y -= 18

    c.setFont(font_name, 12)
    c.setFillColorRGB(0.35, 0.37, 0.40)
    c.drawCentredString(page_w/2, y, ar_shape("تقرير ملاحظات ولي الأمر"))
    y -= 16

    # بيانات ولي الأمر
    if parent_name or parent_relation:
        parent_line = ""
        if parent_name:
            parent_line += f"ولي الأمر: {parent_name}"
        if parent_relation:
            if parent_line:
                parent_line += "  |  "
            parent_line += f"صلة القرابة: {parent_relation}"
        c.setFont(font_name, 11)
        c.setFillColorRGB(0.25, 0.25, 0.25)
        c.drawCentredString(page_w/2, y, ar_shape(parent_line))
        y -= 14

    c.setFillColorRGB(0,0,0)
    y -= HEADER_GAP

    # خط فاصل
    c.setLineWidth(LINE_THIN)
    c.setStrokeColorRGB(0.82, 0.84, 0.88)
    c.line(MARGIN, y, page_w - MARGIN, y)
    y -= 14

    # بيانات الطالبة
    c.setFont(font_name, 12)
    c.drawRightString(x_right, y, ar_shape(f"الطالب: {student['name']}")); y -= ROW_LEADING
    c.drawRightString(x_right, y, ar_shape(f"الرقم: {student_id}"));       y -= (ROW_LEADING - 2)
    c.drawRightString(x_right, y, ar_shape(f"الصف: {student['class']}"));  y -= ROW_LEADING

    c.line(MARGIN, y, page_w - MARGIN, y); y -= 14

    # رؤوس الأعمدة
    c.setFont(font_name, 12)
    col_subject_x = x_right
    col_teacher_x = x_right - 240
    x_note_left   = MARGIN + 10
    note_width    = col_teacher_x - 14 - x_note_left

    c.drawRightString(col_subject_x, y, ar_shape("المادة"))
    c.drawRightString(col_teacher_x, y, ar_shape("المعلم / المعلمة"))
    c.drawString(x_note_left, y, ar_shape("ملاحظات ولي الأمر"))
    y -= 8
    c.setLineWidth(LINE_BOLD)
    c.setStrokeColorRGB(0,0,0)
    c.line(MARGIN, y, page_w - MARGIN, y)
    y -= 10

    # صفوف المواد
    c.setFont(font_name, 11)
    for sub in student.get("subjects", []):
        subject = sanitize_text(sub.get("subject",""))
        teacher = sanitize_text(sub.get("teacher",""))
        pnote   = sanitize_text(sub.get("parent_note","")).strip()

        c.setFillColorRGB(0,0,0)
        c.drawRightString(col_subject_x, y, ar_shape(subject))
        c.drawRightString(col_teacher_x, y, ar_shape(teacher))

        c.setFillColorRGB(0.1, 0.3, 0.8)
        if pnote:
            lines = wrap_ar_lines(pnote, note_width, font_name, 11, c)
            for ln in lines:
                c.drawString(x_note_left, y, ar_shape(ln))
                y -= NOTE_LEADING

            y -= 10
            c.setFillColorRGB(0, 0, 0)
        else:
            y -= (ROW_LEADING + ROW_GAP)

        c.setLineWidth(0.6)
        c.setStrokeColorRGB(0.90, 0.90, 0.90)
        c.line(MARGIN, y, page_w - MARGIN, y)
        y -= 10

        if y < (MARGIN + FOOTER_H + 40):
            _draw_footer(c, page_w, page_h, font_name, MARGIN)
            c.showPage()
            y = TOP_Y

            c.setFont(font_name, 12)
            c.setFillColorRGB(0,0,0)
            c.drawRightString(x_right, y, ar_shape(
                f"الطالب: {student['name']}  |  الرقم: {student_id}  |  الصف: {student['class']}"))
            y -= ROW_LEADING

            c.setLineWidth(LINE_THIN)
            c.setStrokeColorRGB(0.82, 0.84, 0.88)
            c.line(MARGIN, y, page_w - MARGIN, y)
            y -= 14

            c.setFont(font_name, 12)
            col_subject_x = x_right
            col_teacher_x = x_right - 240
            x_note_left   = MARGIN + 10
            note_width    = col_teacher_x - 14 - x_note_left

            c.drawRightString(col_subject_x, y, ar_shape("المادة"))
            c.drawRightString(col_teacher_x, y, ar_shape("المعلم / المعلمة"))
            c.drawString(x_note_left, y, ar_shape("ملاحظات ولي الأمر"))
            y -= 8
            c.setLineWidth(LINE_BOLD)
            c.setStrokeColorRGB(0,0,0)
            c.line(MARGIN, y, page_w - MARGIN, y)
            y -= 10

            c.setFont(font_name, 11)

    _draw_footer(c, page_w, page_h, font_name, MARGIN)
    c.save()
    buf.seek(0)
    return buf.getvalue()

def _draw_footer(c: canvas.Canvas, page_w: float, page_h: float, font_name: str, MARGIN: float):
    y = 30
    x_left   = MARGIN
    x_center = page_w / 2
    x_right  = page_w - MARGIN
    c.setFont(font_name, 11)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.drawString(x_left, y,   ar_shape("مديرة المدرسة: أ. مريم المزين"))
    c.drawCentredString(x_center, y, ar_shape("المديرة المساعدة: أ. أماني الملا"))
    c.drawRightString(x_right, y,   ar_shape("تصميم النظام قسم الحاسوب أ. مريم بورسلي"))
    c.setLineWidth(0.5)
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(MARGIN, y + 14, page_w - MARGIN, y + 14)

def export_parent_visits_pdf(
    visits_df: pd.DataFrame,
    logo_path: str = None,
    school_name: str = ""
) -> bytes:

    font_name, _ = ensure_pdf_font()
    page_w, page_h = A4
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    MARGIN = 36
    TOP_Y = page_h - MARGIN
    ROW_HEIGHT = 22
    HEADER_BG = (0.23, 0.47, 0.96)   # أزرق ملكي للرأس
    BORDER_COLOR = (0.75, 0.75, 0.75)

    # أعمدة الجدول
    columns = [
        ("ولي الأمر", 140),
        ("صلة القرابة", 100),
        ("الطالبة", 160),
        ("الصف", 80),
    ]

    total_width = sum(width for _, width in columns)
    x_start = (page_w - total_width) / 2

    def draw_header(y):
        c.setFillColorRGB(*HEADER_BG)
        c.rect(x_start, y, total_width, ROW_HEIGHT, fill=1)

        c.setFillColorRGB(1, 1, 1)
        c.setFont(font_name, 11)

        x = x_start + total_width
        for title, width in columns:
            x -= width
            c.drawRightString(x + width - 6, y + 6, ar_shape(title))

        c.setFillColorRGB(*BORDER_COLOR)
        c.setLineWidth(1)
        c.rect(x_start, y, total_width, ROW_HEIGHT, fill=0)

    def draw_row(y, row):
        c.setFillColorRGB(1, 1, 1)
        c.rect(x_start, y, total_width, ROW_HEIGHT, fill=1)

        c.setFillColorRGB(0, 0, 0)
        c.setFont(font_name, 10)

        x = x_start + total_width
        values = [
            sanitize_text(row["parent_name"]),
            sanitize_text(row["parent_relation"]),
            sanitize_text(row["student_name"]),
            sanitize_text(row["class"]),
        ]

        for (title, width), value in zip(columns, values):
            x -= width
            c.drawRightString(x + width - 6, y + 6, ar_shape(value))

        c.setFillColorRGB(*BORDER_COLOR)
        c.rect(x_start, y, total_width, ROW_HEIGHT, fill=0)

    # ---------- رأس الصفحة ----------
    y = TOP_Y

    if logo_path and os.path.exists(logo_path):
        try:
            im = Image.open(logo_path)
            w0, h0 = im.size
            target_w = 110
            ratio = target_w / float(w0)
            target_h = h0 * ratio
            c.drawImage(
                logo_path,
                (page_w - target_w) / 2,
                y - target_h,
                width=target_w,
                height=target_h,
                preserveAspectRatio=True,
                mask='auto'
            )
            y -= (target_h + 15)
        except:
            y -= 20

    c.setFont(font_name, 16)
    c.setFillColorRGB(0,0,0)
    c.drawCentredString(page_w/2, y, ar_shape(school_name))
    y -= 20

    c.setFont(font_name, 12)
    c.setFillColorRGB(0.3,0.3,0.3)
    c.drawCentredString(page_w/2, y, ar_shape("تقرير زيارات أولياء الأمور"))
    y -= 25

    # ---------- جدول ----------
    draw_header(y)
    y -= ROW_HEIGHT

    for _, row in visits_df.iterrows():
        if y < 80:
            c.showPage()
            y = TOP_Y
            draw_header(y)
            y -= ROW_HEIGHT

        draw_row(y, row)
        y -= ROW_HEIGHT

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()



# ================= واجهة Streamlit =================
st.set_page_config(page_title="نظام أولياء الأمور", page_icon="📄", layout="centered")

# تحميل قاعدة البيانات
try:
    load_school_data_from_sqlite(DB_PATH)
except Exception as e:
    st.error(f"خطأ في تحميل قاعدة البيانات: {e}")

# ========== CSS ==========
st.markdown("""
<style>


    .stApp {
        background-color: #f7f8fc;
        font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .card {
        background: transparent !important;
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        box-shadow: none !important;
    }

    .student-card {
        background: #3b82f6;
        padding: 1.4rem;
        border-radius: 18px;
        box-shadow: 0px 3px 8px rgba(0,0,0,0.15);
        color: #ffffff;
        margin-bottom: 1.5rem;
        text-align: center;
        max-width: 680px;
        margin-left: auto;
        margin-right: auto;
    }
    .student-card h3 {
        margin: 0 0 4px 0;
        font-weight: 800;
        font-size: 1.1rem;
    }
    .student-card p {
        margin: 0;
        font-size: 0.95rem;
    }

    .subject-row {
        display: flex;
        flex-direction: row-reverse;
        align-items: center;
        justify-content: center;
        gap: 0.7rem;
        margin-bottom: 0.4rem;
        flex-wrap: wrap;
    }

    .subject-title,
    .teacher-title {
        background: #3b82f6;
        padding: 6px 10px;
        border-radius: 10px;
        font-weight: 600;
        color: #ffffff;
        white-space: nowrap;
        font-size: 0.9rem;
    }

    textarea {
        background: #ffffff !important;
        border: 1px solid #d1d5db !important;
        border-radius: 10px !important;
        color: #1e1e2f !important;
    }

    .stButton>button[kind="primary"] {
        background: #3b82f6;
        color: white;
        padding: 0.6rem 1.2rem;
        border-radius: 10px;
        border: none;
        font-size: 1rem;
    }
    .stButton>button[kind="primary"]:hover {
        background: #2563eb;
    }

    .stButton>button[kind="secondary"] {
        background: #0BAF9A;
        color: #ffffff;
        padding: 0.5rem 1rem;
        border-radius: 10px;
        border: none;
        font-size: 0.95rem;
    }
    .stButton>button[kind="secondary"]:hover {
        background: #089E8A;
    }

    .title-main {
        font-size: 1.7rem;
        font-weight: 800;
        text-align: center;
        color: #1e1e2f;
        margin-top: -12px;
    }
    .subtitle-main {
        text-align: center;
        color: #6b7280;
        margin-bottom: 1.5rem;
    }

    /* 📱 تنسيق خاص للشاشات الصغيرة (موبايل) */
    @media (max-width: 768px) {
        .student-card {
            padding: 1rem 0.9rem;
            border-radius: 16px;
            margin-bottom: 1rem;
        }
        .student-card h3 {
            font-size: 1rem;
        }
        .student-card p {
            font-size: 0.85rem;
        }

        .subject-row {
            gap: 0.4rem;
        }
        .subject-title,
        .teacher-title {
            font-size: 0.8rem;
            padding: 5px 8px;
        }

        .stButton>button[kind="primary"],
        .stButton>button[kind="secondary"] {
            width: 100%;
            font-size: 0.9rem;
        }

        textarea {
            font-size: 0.85rem !important;
        }

        .title-main {
            font-size: 1.3rem;
        }
        .subtitle-main {
            font-size: 0.9rem;
        }
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
.footer-fixed {
    position: fixed;
    bottom: 12px;
    left: 50%;
    transform: translateX(-50%);
    text-align: center;
    font-size: 0.9rem;
    color: #4b5563;
    opacity: 0.95;
    z-index: 9999;
}

.footer-fixed a {
    text-decoration: none;
    color: #8b5cf6;
    font-weight: 600;
}

.footer-fixed img {
    width: 18px;
    vertical-align: middle;
    margin-left: 6px;
}

/* 📱 تصغير الفوتر على الشاشات الصغيرة */
@media (max-width: 768px) {
    .footer-fixed {
        font-size: 0.8rem;
        bottom: 8px;
    }
    .footer-fixed img {
        width: 16px;
        margin-left: 4px;
    }
}
</style>

<div class="footer-fixed">
    <img src="https://upload.wikimedia.org/wikipedia/commons/a/a5/Instagram_icon.png">
    <a href="https://www.instagram.com/alesraa_highschool" target="_blank">
        @alesraa_highschool
    </a>
    <br>
    تصميم النظام قسم الحاسوب — أ. مريم بورسلي
</div>
""", unsafe_allow_html=True)


# ===== حالة الجلسة =====
if "step" not in st.session_state:
    st.session_state.step = 1
if "parent_name" not in st.session_state:
    st.session_state.parent_name = ""
if "parent_relation" not in st.session_state:
    st.session_state.parent_relation = ""
if "selected_sid" not in st.session_state:
    st.session_state.selected_sid = None
if "current_record" not in st.session_state:
    st.session_state.current_record = None
if "logged_visit_sid" not in st.session_state:
    st.session_state.logged_visit_sid = None
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False

# الشعار والعنوان
def load_base64_image(path):
    with open(path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode()

if os.path.exists(ASSETS_LOGO):
    base64_logo = load_base64_image(ASSETS_LOGO)
    st.markdown(f"""
        <div style="text-align:center;">
            <img src="data:image/png;base64,{base64_logo}" width="130">
        </div>
    """, unsafe_allow_html=True)

st.markdown(f"<div class='title-main'>نظام عرض بيانات الطالب ومستوى الأداء</div>", unsafe_allow_html=True)
st.markdown(f"<div class='subtitle-main'>{SCHOOL_NAME}</div>", unsafe_allow_html=True)
st.caption(f"👩‍🎓 عدد الطالبات المحملة من القاعدة: {len(_STUDENTS_INDEX)}")

# وضع الاستخدام
with st.sidebar:
    st.markdown("### وضع الاستخدام")
    mode = st.radio("اختر:", ["ولي الأمر", "الإدارة"], label_visibility="collapsed")

    st.markdown("### بيانات ولي الأمر")
    st.write(f"**الاسم:** {st.session_state.get('parent_name', '—')}")
    st.write(f"**صلة القرابة:** {st.session_state.get('parent_relation', '—')}")

    if mode == "ولي الأمر" and st.session_state.step >= 3:
        if st.button("⬅️ تغيير الطالبة", type="primary"):
            st.session_state.step = 2
            st.session_state.selected_sid = None
            st.session_state.current_record = None
            st.session_state.logged_visit_sid = None
            st.rerun()

# ================= وضع ولي الأمر =================
if mode == "ولي الأمر":

    # -------- الخطوة 1: بيانات ولي الأمر --------
    if st.session_state.step == 1:
        st.subheader("بيانات ولي الأمر")

        parent_name = st.text_input("اسم ولي الأمر", value=st.session_state.parent_name)

        base_relations = ["أب", "أم", "أخ", "أخت", "ولي أمر آخر"]

        if st.session_state.parent_relation in base_relations:
            default_index = base_relations.index(st.session_state.parent_relation)
        elif st.session_state.parent_relation:
            default_index = base_relations.index("ولي أمر آخر")
        else:
            default_index = 0

        relation_choice = st.selectbox(
            "صلة القرابة بالطالبة",
            base_relations,
            index=default_index
        )

        custom_relation = ""
        if relation_choice == "ولي أمر آخر":
            prev = st.session_state.parent_relation
            default_custom = prev if (prev and prev not in base_relations) else ""
            custom_relation = st.text_input("اكتبي صلة القرابة", value=default_custom)

        if st.button("متابعة ➜ اختيار الطالبة", type="primary"):
            if not parent_name.strip():
                st.warning("الرجاء كتابة اسم ولي الأمر")
            elif relation_choice == "ولي أمر آخر" and not custom_relation.strip():
                st.warning("الرجاء كتابة صلة القرابة في الحقل المخصص")
            else:
                st.session_state.parent_name = parent_name.strip()
                if relation_choice == "ولي أمر آخر":
                    st.session_state.parent_relation = custom_relation.strip()
                else:
                    st.session_state.parent_relation = relation_choice

                st.session_state.step = 2
                st.success("تم تسجيل بيانات ولي الأمر. يمكنك الآن اختيار الصف والطالبة.")
                st.rerun()

    # -------- الخطوة 2: اختيار الصف والطالبة --------
    elif st.session_state.step == 2:
        st.subheader("اختيار الطالبة")

        st.write("عدد الطالبات في القاعدة:", len(_STUDENTS_INDEX))

        all_classes = sorted({normalize_class(info["class"]) for info in _STUDENTS_INDEX.values()})
        class_placeholder = "-- اختر الصف --"

        selected_class = st.selectbox(
            "الصف",
            [class_placeholder] + all_classes if all_classes else [class_placeholder],
            index=0,
            key="class_select",
        )

        students_options = []
        sid_for_label = {}
        student_placeholder = "-- اختر الطالبة --"

        if selected_class != class_placeholder and all_classes:
    for sid_key, info in _STUDENTS_INDEX.items():
        if normalize_class(info["class"]) == selected_class:
            # 👇 بدون الرقم المدني
            label = info["name"]

            # لو تكرّر الاسم، نضيف الصف للتمييز (بدون إظهار الرقم)
            if label in sid_for_label:
                label = f"{info['name']} - {info['class']}"

            students_options.append(label)
            sid_for_label[label] = sid_key


        selected_student_label = st.selectbox(
            "اسم الطالبة",
            [student_placeholder] + students_options if students_options else [student_placeholder],
            index=0,
            key="student_select",
        )

        if st.button("متابعة ➜ عرض المواد", type="primary"):
            if selected_class == class_placeholder or selected_student_label == student_placeholder:
                st.warning("الرجاء اختيار الصف والطالبة")
            else:
                sid = sid_for_label[selected_student_label]
                rec = build_student_record_from_db(sid)
                if not rec:
                    st.error("لا توجد بيانات لهذه الطالبة في قاعدة البيانات.")
                else:
                    st.session_state.selected_sid = sid
                    st.session_state.current_record = rec
                    st.session_state.step = 3
                    st.session_state.logged_visit_sid = None
                    st.rerun()

    # -------- الخطوة 3: المواد + الملاحظات + تقرير --------
    elif st.session_state.step >= 3:
        if not st.session_state.selected_sid or not st.session_state.current_record:
            st.error("لم يتم اختيار طالبة بعد. الرجاء الرجوع للخطوة السابقة.")
        else:
            sid = st.session_state.selected_sid
            rec = st.session_state.current_record

            # تسجيل زيارة ولي الأمر للطالبة (مرة واحدة لكل طالبة في الجلسة)
            if st.session_state.logged_visit_sid != sid:
                log_parent_visit(
                    sid,
                    st.session_state.get("parent_name", ""),
                    st.session_state.get("parent_relation", "")
                )
                st.session_state.logged_visit_sid = sid

            st.markdown(
                f"""
                <div class="student-card">
                    <h3>الطالبة: {rec['name']}</h3>
                    <p>الصف: {rec['class']} — الرقم: {sid}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.write("### المواد وملاحظات وليّ الأمر")
            updated_notes = []

            for i, row in enumerate(rec.get("subjects", [])):
                st.markdown(
                    f"""
                    <div class="subject-row">
                        <div class="subject-title">المادة: {row['subject']}</div>
                        <div class="teacher-title">المعلمـة: {row['teacher']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                note_key = f"pnote_{i}"
                default_note = row.get("parent_note", "")
                new_note = st.text_area(
                    "ملاحظة وليّ الأمر",
                    value=default_note,
                    key=note_key,
                    label_visibility="collapsed",
                    height=80
                )
                updated_notes.append(new_note)

                col_save, _ = st.columns([1, 5])
                with col_save:
                    if st.button("حفظ هذه المادة", key=f"save_{i}", use_container_width=True, type="secondary"):
                        row["parent_note"] = new_note
                        save_subject_note_to_db(sid, row["subject"], new_note)
                        st.success("تم حفظ ملاحظة هذه المادة")

            if st.button("💾 حفظ جميع الملاحظات", type="primary"):
                for i, row in enumerate(rec.get("subjects", [])):
                    row["parent_note"] = updated_notes[i]
                    if updated_notes[i].strip():
                        save_subject_note_to_db(sid, row["subject"], updated_notes[i])
                st.success("تم حفظ جميع الملاحظات")

            rec_for_pdf = {
                "name": rec["name"],
                "class": rec["class"],
                "subjects": []
            }
            for i, row in enumerate(rec.get("subjects", [])):
                row_copy = row.copy()
                row_copy["parent_note"] = updated_notes[i]
                rec_for_pdf["subjects"].append(row_copy)

            pdf_bytes = export_report_A4_pdf_bytes(
                sid,
                rec_for_pdf,
                logo_path=ASSETS_LOGO,
                school_name=SCHOOL_NAME,
                parent_name=st.session_state.get("parent_name"),
                parent_relation=st.session_state.get("parent_relation")
            )

            st.download_button(
                "⬇️ تنزيل التقرير PDF",
                data=pdf_bytes,
                file_name=f"{sid}_{rec['name']}_report.pdf",
                mime="application/pdf"
            )

# ================= وضع الإدارة =================
elif mode == "الإدارة":
    # شاشة تسجيل الدخول للإدارة
    if not st.session_state.admin_logged_in:
        st.subheader("تسجيل دخول الإدارة")
        pwd = st.text_input("كلمة مرور الإدارة", type="password")
        if st.button("دخول", type="primary"):
            if pwd == ADMIN_PASSWORD:
                st.session_state.admin_logged_in = True
                st.success("تم تسجيل الدخول بنجاح.")
                st.rerun()
            else:
                st.error("كلمة المرور غير صحيحة.")
        st.info("هذه الصفحة مخصصة لإدارة المدرسة فقط.")
    else:
        st.subheader("لوحة تحكم الإدارة")

        tab1, tab2 = st.tabs(["📋 ملاحظات أولياء الأمور", "👨‍👩‍👧 تقرير أولياء الأمور الذين سجّلوا"])

        with tab1:
            df = load_parent_notes_df()
            if df.empty:
                st.info("لا توجد ملاحظات مُدخلة حتى الآن.")
            else:
                classes = ["الكل"] + sorted(df["class"].dropna().unique().tolist())
                selected_class = st.selectbox("فلتر حسب الصف", classes, key="notes_class_filter")

                subjects = ["الكل"] + sorted(df["subject"].dropna().unique().tolist())
                selected_subject = st.selectbox("فلتر حسب المادة", subjects, key="notes_subject_filter")

                filtered = df.copy()
                if selected_class != "الكل":
                    filtered = filtered[filtered["class"] == selected_class]
                if selected_subject != "الكل":
                    filtered = filtered[filtered["subject"] == selected_subject]

                st.write(f"عدد السجلات: {len(filtered)}")
                st.dataframe(filtered, use_container_width=True)

                csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "تنزيل كملف Excel (CSV)",
                    data=csv_bytes,
                    file_name="parent_notes_export.csv",
                    mime="text/csv"
                )

        with tab2:
            visits_df = load_parent_visits_df()  # افتراضيًا عندك دالة ترجع الزيارات

            if visits_df.empty:
                st.info("لا توجد زيارات مسجّلة حتى الآن.")
            else:
                # قائمة الصفوف الموجودة في الزيارات
                class_options = ["الكل"] + sorted(
                    visits_df["class"].dropna().unique().tolist()
                )

                selected_class = st.selectbox("فلتر حسب الصف", class_options, key="visits_class_filter")

                # فلترة حسب الصف
                filtered = visits_df.copy()
                if selected_class != "الكل":
                    filtered = filtered[filtered["class"] == selected_class]

                st.write(f"عدد السجلات: {len(filtered)}")
                st.dataframe(filtered, use_container_width=True)

                # تنزيل كـ Excel/CSV
                csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "تنزيل زيارات أولياء الأمور كـ Excel (CSV)",
                    data=csv_bytes,
                    file_name="parent_visits_filtered.csv",
                    mime="text/csv"
                )

                # 🔹 تنزيل كـ PDF
                pdf_bytes = export_parent_visits_pdf(
                    filtered,
                    logo_path=ASSETS_LOGO,
                    school_name=SCHOOL_NAME
                )

                st.download_button(
                    "⬇️ تنزيل تقرير زيارات أولياء الأمور (PDF)",
                    data=pdf_bytes,
                    file_name="parent_visits_report.pdf",
                    mime="application/pdf"
                )

        # زر خروج من لوحة الإدارة
        if st.button("🚪 تسجيل خروج الإدارة"):
            st.session_state.admin_logged_in = False
            st.rerun()


