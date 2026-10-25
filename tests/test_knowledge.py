import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.knowledge.retriever import chunk_text, normalize_persian, KnowledgeBase
import tempfile

def test_chunk():
    text = "سلام. " * 500
    chunks = chunk_text(text, size=200, overlap=30)
    assert len(chunks) > 1
    print(f"chunk OK: {len(chunks)} pieces")

def test_normalize():
    assert normalize_persian("ي ك") == "ی ک"
    print("normalize OK")

def test_kb_search():
    # با پوشه موقت
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        (p / "doc.txt").write_text("بیمه شخص ثالث اجباری است و خسارت جانی را پوشش می‌دهد", encoding="utf-8")
        kb = KnowledgeBase(folder=p)
        res = kb.search("شخص ثالث چیست", top_k=2)
        assert len(res) > 0
        assert "شخص ثالث" in res[0]["content"]
        print("kb search OK", res[0]["score"])

if __name__ == "__main__":
    test_chunk()
    test_normalize()
    test_kb_search()
    print("knowledge tests passed")
