"""
بازیابی دانش (RAG سبک)
- بدون نیاز به سرویس برداری خارجی برای فاز اول
- استفاده از TF-IDF + Cosine Similarity (scikit-learn)
- قابل ارتقا به embeddings (OpenAI/Gemini) بدون تغییر اینترفیس
"""
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple
from ..config import KNOWLEDGE_DIR

log = logging.getLogger(__name__)

# تلاش برای import sklearn، اگر نبود fallback به جستجوی ساده متنی
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    log.warning("scikit-learn not installed, using simple text search")

CHUNK_SIZE = 800  # کاراکتر
CHUNK_OVERLAP = 150

def normalize_persian(text: str) -> str:
    """نرمال‌سازی ساده فارسی برای جستجو بهتر"""
    if not text:
        return ""
    # حذف اعراب، یکسان‌سازی ی/ک
    text = re.sub(r"[ًٌٍَُِّْ]", "", text)
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def chunk_text(text: str, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP) -> List[str]:
    """تقسیم متن به قطعات با هم‌پوشانی"""
    text = text.strip()
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end]
        # سعی کن در مرز جمله ببری
        if end < len(text):
            last_dot = chunk.rfind("۔")
            if last_dot == -1:
                last_dot = chunk.rfind(".")
            if last_dot == -1:
                last_dot = chunk.rfind("؟")
            if last_dot > size * 0.5:
                chunk = chunk[:last_dot+1]
                end = start + last_dot + 1
        chunks.append(chunk.strip())
        start = end - overlap
        if start < 0:
            start = 0
        if chunks and len(chunks[-1]) < 50:
            break
    return [c for c in chunks if len(c.strip()) > 30]

def extract_text_from_file(path: Path) -> str:
    """استخراج متن از PDF/DOCX/TXT/MD/CSV"""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from PyPDF2 import PdfReader
            reader = PdfReader(str(path))
            return "\n".join([p.extract_text() or "" for p in reader.pages])
        elif suffix in (".docx",):
            from docx import Document
            doc = Document(str(path))
            return "\n".join([p.text for p in doc.paragraphs])
        elif suffix in (".txt", ".md", ".csv"):
            return path.read_text(encoding="utf-8", errors="ignore")
        else:
            # تلاش عمومی
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        log.warning("extract failed %s: %s", path, e)
        return ""

class KnowledgeBase:
    def __init__(self, folder: Path = KNOWLEDGE_DIR):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.chunks: List[Dict] = []  # {id, content, source, normalized}
        self.vectorizer = None
        self.tfidf_matrix = None
        self._load()

    def _load(self):
        """بارگذاری همه اسناد و ساخت ایندکس"""
        self.chunks = []
        # از دیتابیس هم بخوان (اگر knowledge_chunks پر است)
        try:
            from ..database import get_conn
            conn = get_conn()
            cur = conn.execute("SELECT kc.content, kc.metadata, ks.title, ks.is_official FROM knowledge_chunks kc JOIN knowledge_sources ks ON kc.source_id=ks.id WHERE ks.is_active=1")
            for row in cur.fetchall():
                content = row["content"]
                if content and len(content.strip()) > 20:
                    self.chunks.append({
                        "content": content,
                        "source": row["title"] or "منبع تاییدشده",
                        "is_official": bool(row["is_official"]),
                        "normalized": normalize_persian(content),
                    })
            conn.close()
            if self.chunks:
                log.info("Loaded %d chunks from DB", len(self.chunks))
        except Exception as e:
            log.warning("DB knowledge load failed: %s", e)

        # اگر DB خالی بود، از فایل‌ها بخوان
        if not self.chunks:
            for p in self.folder.glob("*.*"):
                if p.name in ("README.md", ".gitkeep", "faq-taeedshode.csv"):
                    # CSV FAQ جداگانه مدیریت می‌شود، README را نادیده بگیر
                    if p.name == "faq-taeedshode.csv":
                        # CSV را هم به عنوان دانش ایندکس کن ولی نه README
                        pass
                    else:
                        continue
                if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md", ".csv"):
                    text = extract_text_from_file(p)
                    if not text.strip():
                        continue
                    # اگر CSV است، هر سطر را جداگانه
                    if p.suffix.lower() == ".csv" and p.name == "faq-taeedshode.csv":
                        # ساده: هر خط را یک chunk
                        lines = text.splitlines()[1:]  # هدر را بگذر
                        for line in lines:
                            if line.strip():
                                self.chunks.append({
                                    "content": line.strip(),
                                    "source": p.name,
                                    "is_official": True,
                                    "normalized": normalize_persian(line.strip()),
                                })
                        continue
                    for i, ch in enumerate(chunk_text(text)):
                        self.chunks.append({
                            "content": ch,
                            "source": p.name,
                            "is_official": True,
                            "normalized": normalize_persian(ch),
                        })
            # فایل‌های نمونه
            # اگر هنوز خالی است، یک دانش پیش‌فرض بساز
            if not self.chunks:
                defaults = [
                    ("بیمه شخص ثالث چیست؟", "بیمه شخص ثالث بیمه‌ای اجباری است که خسارت‌های جانی و مالی وارد به اشخاص ثالث در حوادث رانندگی را جبران می‌کند. داشتن آن برای تمام وسایل نقلیه موتوری الزامی است و بدون آن وسیله نقلیه توقیف می‌شود."),
                    ("مدارک لازم بیمه بدنه", "برای صدور بیمه بدنه نیاز به کارت خودرو، کارت ملی مالک، و بازدید اولیه خودرو است. در صورت داشتن تخفیف عدم خسارت، بیمه‌نامه قبلی نیز ارائه شود."),
                    ("پوشش بیمه آتش‌سوزی", "بیمه آتش‌سوزی خسارت‌های ناشی از آتش‌سوزی، صاعقه، انفجار، زلزله، سیل و ترکیدگی لوله آب را پوشش می‌دهد. این بیمه برای منازل مسکونی و واحدهای تجاری قابل صدور است."),
                    ("بیمه درمان تکمیلی", "بیمه درمان تکمیلی هزینه‌های بستری، جراحی، پاراکلینیک، دندانپزشکی و زایمان را تا سقف تعهدات پوشش می‌دهد. دوره انتظار برای برخی پوشش‌ها 3 تا 9 ماه است."),
                ]
                for title, content in defaults:
                    self.chunks.append({
                        "content": f"{title}\n{content}",
                        "source": "دانش پیش‌فرض",
                        "is_official": True,
                        "normalized": normalize_persian(content),
                    })
            log.info("Loaded %d chunks from files/defaults", len(self.chunks))

        # ساخت ایندکس TF-IDF
        if HAS_SKLEARN and self.chunks:
            try:
                texts = [c["normalized"] for c in self.chunks]
                self.vectorizer = TfidfVectorizer(max_features=5000, ngram_range=(1,2))
                self.tfidf_matrix = self.vectorizer.fit_transform(texts)
                log.info("TF-IDF index built (%d docs)", len(texts))
            except Exception as e:
                log.warning("TF-IDF build failed: %s", e)
                self.vectorizer = None

    def reload(self):
        self._load()

    def search(self, query: str, top_k: int = 3, min_score: float = 0.15) -> List[Dict]:
        """جستجوی مرتبط‌ترین قطعات"""
        if not self.chunks:
            return []
        q_norm = normalize_persian(query)
        if not q_norm:
            return []

        if HAS_SKLEARN and self.vectorizer is not None:
            try:
                q_vec = self.vectorizer.transform([q_norm])
                scores = cosine_similarity(q_vec, self.tfidf_matrix).flatten()
                ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
                results = []
                for idx, score in ranked[:top_k]:
                    if score < min_score:
                        continue
                    ch = self.chunks[idx]
                    results.append({
                        "content": ch["content"],
                        "source": ch["source"],
                        "score": float(score),
                        "is_official": ch.get("is_official", True),
                    })
                return results
            except Exception as e:
                log.warning("TF-IDF search failed: %s", e)

        # fallback: جستجوی ساده کلمه‌ای
        q_words = set(q_norm.split())
        scored = []
        for ch in self.chunks:
            words = set(ch["normalized"].split())
            overlap = len(q_words & words)
            score = overlap / max(len(q_words), 1)
            if score > 0:
                scored.append((ch, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for ch, score in scored[:top_k]:
            if score < 0.1:
                continue
            results.append({
                "content": ch["content"],
                "source": ch["source"],
                "score": float(score),
                "is_official": ch.get("is_official", True),
            })
        return results

    def add_document(self, file_path: Path, title: str = None, is_official: bool = True):
        """افزودن سند جدید و ذخیره در DB"""
        import uuid, json
        from datetime import datetime
        from ..database import get_conn
        text = extract_text_from_file(file_path)
        if not text.strip():
            raise ValueError("متن قابل استخراج نیست")
        chunks = chunk_text(text)
        conn = get_conn()
        try:
            sid = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            conn.execute("INSERT INTO knowledge_sources(id, title, file_path, file_type, is_official, chunk_count, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (sid, title or file_path.name, str(file_path), file_path.suffix, 1 if is_official else 0, len(chunks), now, now))
            for i, ch in enumerate(chunks):
                cid = str(uuid.uuid4())[:8]
                conn.execute("INSERT INTO knowledge_chunks(id, source_id, chunk_index, content, token_count, metadata) VALUES(?,?,?,?,?,?)",
                             (cid, sid, i, ch, len(ch.split()), json.dumps({"source": file_path.name}, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        self.reload()
        return len(chunks)

# نمونه سراسری
_kb = None
def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
