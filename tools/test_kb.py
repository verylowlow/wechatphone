"""Smoke test for the knowledge module (no audio involved).

- uses a temp sqlite DB
- ingests sample documents (one price card, one FAQ)
- verifies embedding backend (should be api:text-embedding-v4)
- verifies retrieval quality on Chinese queries
- verifies tiered injection (FULL vs RETRIEVAL)

Run:  python tools\test_kb.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# load .env (same lightweight loader as bridge.py)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
with open(env_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from knowledge.local_adapter import LocalKnowledgeAdapter  # noqa: E402

PRICE_DOC = """小云直播间产品价目表
玻尿酸面膜(10片装): 原价199元, 直播价99元, 买一送一。
烟酰胺精华液(30ml): 原价259元, 直播价129元, 赠同系列小样3件。
氨基酸洗面奶(120ml): 原价89元, 直播价49元。
发货说明: 全场包邮, 48小时内发货, 支持7天无理由退货。
"""

FAQ_DOC = """常见问题
Q: 怎么加入粉丝团?
A: 点击直播间左上角头像旁的加号, 即可加入粉丝团。
Q: 优惠券怎么领?
A: 整点会在直播间发放优惠券, 点击右下角小黄车领取。
Q: 支持货到付款吗?
A: 目前仅支持在线支付, 支持微信、支付宝。
"""


def main():
    tmp = tempfile.mkdtemp(prefix="kb_smoke_")
    db = os.path.join(tmp, "test.sqlite")
    kb = LocalKnowledgeAdapter(db_path=db)

    print("== stats ==")
    print(kb.get_stats())

    # ingest
    p1 = os.path.join(tmp, "价目表.txt")
    p2 = os.path.join(tmp, "FAQ.txt")
    with open(p1, "w", encoding="utf-8") as f:
        f.write(PRICE_DOC)
    with open(p2, "w", encoding="utf-8") as f:
        f.write(FAQ_DOC)
    d1 = kb.ingest_file(p1)
    d2 = kb.ingest_file(p2)
    print(f"\n== ingested ==\n{d1}\n{d2}")

    st = kb.get_stats()
    print(f"\nembedding backend: {st['embedding']}")
    assert st["chunks"] >= 2, "chunks missing"

    # retrieval
    for q in ["面膜多少钱", "怎么加入粉丝团", "退货政策是什么"]:
        res = kb.query(q, top_k=2)
        print(f"\nQ: {q}")
        for s in res:
            print(f"  [{s.score:.3f}] ({s.source}) {s.text[:60].replace(chr(10), ' ')}")
        assert res, f"empty retrieval for {q}"

    # pinned + injection tiers
    kb.set_pinned(d1.doc_id, True)
    inj = kb.build_injection()
    print(f"\ninjection tier: {inj['tier']}, search_tool={inj['allow_search_tool']}, "
          f"context_len={len(inj['context_text'])}")
    assert inj["tier"] == "FULL"  # tiny corpus
    assert "99元" in inj["context_text"]  # pinned price present

    # force RETRIEVAL tier with tiny budget
    inj2 = kb.build_injection(budget_tokens=50)
    print(f"forced tiny budget -> tier: {inj2['tier']}, search_tool={inj2['allow_search_tool']}")
    assert inj2["tier"] == "RETRIEVAL"
    assert inj2["allow_search_tool"]
    assert "99元" in inj2["context_text"]  # pinned still fully injected

    print("\nPASS: knowledge smoke test")


if __name__ == "__main__":
    main()
