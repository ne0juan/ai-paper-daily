import json
import unittest

import yaml

from radar import sources
from radar.feeds import parse_date, parse_feed, strip_html
from radar.llm import LLM, extract_json
from radar.models import Item, SourceHit, arxiv_id_from, canonical_url, make_id
from radar.scoring import final_score, rule_score, select
from tests.helpers import TEST_CONFIG, FIX, NOW, fake_http, fake_llm_transport, fx
from radar.http import FakeHttp, HttpError

CFG = yaml.safe_load((FIX.parent.parent / "config" / "sources.yaml").read_text("utf-8"))
TEST_FEEDS = [{"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "authority": .9, "kind": "official"},
              {"name": "Lil'Log", "url": "https://lilianweng.github.io/index.xml", "authority": .9, "kind": "opinion",
               "who": "OpenAI 研究员", "who_name": "Lilian Weng"}]
TEST_LIST = {"name": "Anthropic", "url": "https://www.anthropic.com/research", "base": "https://www.anthropic.com",
             "pattern": "^/(research|news)/[a-z0-9-]{6,}$", "authority": .9}


TEST_CFG = yaml.safe_load(TEST_CONFIG.read_text("utf-8"))


class ModelTests(unittest.TestCase):
    def test_arxiv_id(self):
        for s in ["https://arxiv.org/abs/2609.01234v3", "https://arxiv.org/pdf/2609.01234", "arXiv:2609.01234",
                  "2609.01234", "https://arxiv.org/html/2609.01234v1#sec"]:
            self.assertEqual(arxiv_id_from(s), "2609.01234", s)
        self.assertIsNone(arxiv_id_from("https://example.com/2609"))

    def test_canonical_and_id(self):
        a = canonical_url("http://www.Example.com/post/?utm_source=hn&x=1#top")
        self.assertEqual(a, "https://example.com/post?x=1")
        self.assertEqual(make_id("https://example.com/post/"), make_id("https://www.example.com/post?utm_medium=x"))
        self.assertEqual(make_id("https://arxiv.org/abs/2609.01234v2"), "arxiv-2609.01234")

    def test_merge_prefers_paper_and_keeps_sources(self):
        hn = Item("arxiv-2609.1", "article", "HN headline", "https://x", sources=[SourceHit("Hacker News", .5, "u1")])
        hf = Item("arxiv-2609.1", "paper", "Real Title", "https://arxiv.org/abs/2609.1", abstract="abs",
                  sources=[SourceHit("HF Daily Papers", .8, "u2")])
        hn.merge(hf)
        self.assertEqual((hn.kind, hn.title, hn.abstract), ("paper", "Real Title", "abs"))
        self.assertEqual(len(hn.sources), 2)
        hn.merge(hf)
        self.assertEqual(len(hn.sources), 2, "merge must be idempotent")

    def test_merge_keeps_strongest_hit_per_source(self):
        a = Item("web-1", "article", "t", "u", sources=[SourceHit("Hacker News", .5, "u1", "273 pts", 273)])
        a.merge(Item("web-1", "article", "t", "u", sources=[SourceHit("Hacker News", .5, "u2", "1647 pts", 1647)]))
        self.assertEqual([(s.url, s.metric) for s in a.sources], [("u2", 1647)])

    def test_roundtrip(self):
        it = Item("a", "paper", "t", "u", sources=[SourceHit("s", .5)])
        self.assertEqual(Item.from_dict(json.loads(json.dumps(it.to_dict()))).sources[0].name, "s")


class FeedTests(unittest.TestCase):
    def test_rss(self):
        e = parse_feed(fx("openai_rss.xml"))
        self.assertEqual(len(e), 2)
        self.assertEqual(e[0]["title"], "Introducing a new reasoning model")
        self.assertNotIn("<b>", e[0]["summary"])
        self.assertEqual(e[0]["published"].isoformat(), "2026-09-22T17:00:00+00:00")

    def test_atom(self):
        e = parse_feed(fx("lilian_atom.xml"))
        self.assertEqual(e[0]["link"], "https://lilianweng.github.io/posts/2026-09-21-think/")
        self.assertEqual(e[0]["authors"], ["Lilian Weng"])
        self.assertIn("test-time compute", e[0]["summary"])

    def test_malformed_feed_falls_back_and_strips_invisible_chars(self):
        bad = ('<?xml version="1.0"?><rss><channel><item><title><![CDATA[OpenAI & 微软\u200b\u2063 新合作]]></title>'
               '<link>https://www.jiqizhixin.com/articles/1?a=1&b=2</link><description>一段 & 摘要</description>'
               '<pubDate>Tue, 22 Sep 2026 17:00:00 GMT</pubDate></item></channel></rss>')
        e = parse_feed(bad)
        self.assertEqual(e[0]["title"], "OpenAI & 微软 新合作")
        self.assertEqual(e[0]["link"], "https://www.jiqizhixin.com/articles/1?a=1&b=2")
        self.assertIsNotNone(e[0]["published"])

    def test_dates_and_html(self):
        self.assertIsNotNone(parse_date("2026-09-22"))
        self.assertIsNone(parse_date("not a date"))
        self.assertEqual(strip_html("<p>a &amp; <script>x</script>b</p>"), "a & b")


class SourceTests(unittest.TestCase):
    def test_hf_daily(self):
        items = sources.fetch_hf_daily(fake_http(), TEST_CFG["hf_daily"], NOW)
        ids = {i.id for i in items}
        self.assertIn("arxiv-2609.01234", ids)
        self.assertNotIn("arxiv-2609.05678", ids, "below min_upvotes")
        top = next(i for i in items if i.id == "arxiv-2609.01234")
        self.assertEqual(top.orgs, ["Google DeepMind"])
        self.assertEqual(top.pdf_url, "https://arxiv.org/pdf/2609.01234")
        self.assertEqual(top.sources[0].metric, 212)

    def test_hn_filters_non_ai(self):
        items = sources.fetch_hackernews(fake_http(), TEST_CFG["hackernews"], NOW)
        titles = [i.title for i in items]
        self.assertFalse(any("sourdough" in t for t in titles))
        self.assertTrue(any(i.kind == "article" and "agents" in i.title for i in items))
        self.assertTrue(any(i.id == "arxiv-2609.09999" for i in items))

    def test_rss_lookback(self):
        items = sources.fetch_rss(fake_http(), TEST_FEEDS, NOW, 72)
        titles = [i.title for i in items]
        self.assertIn("Introducing a new reasoning model", titles)
        self.assertNotIn("Old announcement", titles)
        self.assertIn("Why We Think", titles)
        lil = next(i for i in items if i.title == "Why We Think")
        self.assertEqual((lil.category, lil.who), ("观点", "OpenAI 研究员"))

    def test_ai_filter_for_general_media(self):
        feed = [{"name": "36氪", "url": "https://36kr.com/feed", "lang": "zh", "kind": "news", "filter": "ai"}]
        xml = ('<rss><channel><item><title>OpenAI 发布新模型</title><link>https://36kr.com/p/1</link>'
               '<pubDate>Tue, 22 Sep 2026 17:00:00 GMT</pubDate></item><item><title>咖啡连锁开出第一万家店</title>'
               '<link>https://36kr.com/p/2</link><pubDate>Tue, 22 Sep 2026 17:00:00 GMT</pubDate></item></channel></rss>')
        items = sources.fetch_rss(FakeHttp({"36kr.com": xml}), feed, NOW, 72)
        self.assertEqual([(i.title, i.lang, i.category) for i in items], [("OpenAI 发布新模型", "zh", "资讯")])

    def test_html_list_bootstrap_then_incremental(self):
        state = {}
        lists = [TEST_LIST]
        first = sources.fetch_html_lists(fake_http(), lists, state, NOW)
        self.assertEqual(len(first), 2, "bootstrap only takes the top 2 links")
        self.assertEqual(first[0].title, "Tracing the thoughts of a model")
        self.assertNotIn("/careers", json.dumps(state))
        again = sources.fetch_html_lists(fake_http(), lists, state, NOW)
        self.assertEqual(again, [], "already known links are not re-emitted")

    def test_arxiv_enrich(self):
        it = sources.paper_item("2609.09999", "HN title")
        it.extra["needs_arxiv_meta"] = True
        sources.enrich_arxiv(fake_http(), [it])
        self.assertTrue(it.title.startswith("Why Transformers Learn In-Context"))
        self.assertEqual(it.orgs, ["MIT"])
        self.assertIn("cs.LG", it.extra["categories"])

    def test_source_failure_is_isolated(self):
        http = FakeHttp({"hn.algolia.com": HttpError("503")})
        self.assertEqual(sources.fetch_hackernews(http, CFG["hackernews"], NOW), [])

    def test_collect_merges_duplicates(self):
        items = sources.collect(fake_http(), TEST_CFG, {}, NOW)
        ids = [i.id for i in items]
        self.assertEqual(len(ids), len(set(ids)))
        top = next(i for i in items if i.id == "arxiv-2609.01234")
        self.assertEqual({s.name for s in top.sources}, {"HF Daily Papers", "Hacker News"})


class ScoringTests(unittest.TestCase):
    def test_rule_score_orders_sensibly(self):
        items = {i.id: i for i in sources.collect(fake_http(), TEST_CFG, {}, NOW)}
        big = rule_score(items["arxiv-2609.01234"], NOW)      # HF 212 + HN 420 + DeepMind
        small = rule_score(items["arxiv-2609.04567"], NOW)    # HF 35
        self.assertGreater(big, small)
        self.assertTrue(0 <= small <= big <= 1)

    def test_final_and_select(self):
        a = Item("a", "paper", "A", "u", sources=[SourceHit("HF Daily Papers", .8)], score_rule=.6, score_llm=9)
        b = Item("b", "paper", "B", "u", sources=[SourceHit("HF Daily Papers", .8)], score_rule=.6, score_llm=2)
        c = Item("c", "article", "C", "u", sources=[SourceHit("OpenAI", .95)], score_rule=.7, relevant=False)
        for i in (a, b, c):
            i.score = final_score(i)
        self.assertGreater(a.score, b.score)
        picked = select([a, b, c], per_run=5, min_score=.4, max_per_source=1)
        self.assertEqual([i.id for i in picked], ["a"])


class SelectionTests(unittest.TestCase):
    def mk(self, i, cat, lang, score, src):
        it = Item(f"i{i}", "article", f"t{i}", "u", sources=[SourceHit(src, .8)], category=cat, lang=lang, score_rule=score)
        it.score = final_score(it)
        return it

    def test_quotas(self):
        items = [self.mk(i, "资讯", "en", .9, f"E{i}") for i in range(6)]
        items += [self.mk(10 + i, "资讯", "zh", .6, f"Z{i}") for i in range(8)]
        items += [self.mk(20, "观点", "en", .4, "Altman"), self.mk(21, "观点", "zh", .4, "宝玉")]
        items += [Item("p1", "paper", "p", "u", sources=[SourceHit("HF", .7, metric=50)], category="论文", score_rule=.9),
                  Item("p2", "paper", "p", "u", sources=[SourceHit("HF2", .7, metric=300)], category="论文", score_rule=.5)]
        for p in items[-2:]:
            p.score = final_score(p)
        picked = select(items, per_run=10, min_score=.35, max_per_source=4, zh_ratio=.7, papers_left=1, min_opinions=2)
        cats = [i.category for i in picked]
        self.assertEqual(cats.count("观点"), 2, "opinions are guaranteed")
        self.assertEqual([i.id for i in picked if i.category == "论文"], ["p2"], "hottest paper, one per issue")
        news = [i for i in picked if i.category != "论文"]
        self.assertEqual(len(news), 10)
        self.assertGreaterEqual(sum(i.lang == "zh" for i in news) / len(news), .6)


class TranslateTests(unittest.TestCase):
    def test_edge_batch_passthrough_chinese(self):
        from radar.translate import translate_many, lead
        http = fake_http()
        self.assertEqual(translate_many(http, ["Hello", "已经是中文", ""]), ["机器译文", "已经是中文", ""])
        self.assertEqual(lead("A b. C d! E f? G h."), "A b. C d!")

    def test_falls_back_to_google_when_edge_down(self):
        from radar.translate import translate_many
        http = FakeHttp({"edge.microsoft.com": HttpError("403"),
                         "translate.googleapis.com": '[[["你好，",null],["世界。",null]],null,"en"]'})
        self.assertEqual(translate_many(http, ["Hello, world."]), ["你好，世界。"])

    def test_machine_translate_skips_llm_items_and_survives_outage(self):
        from radar.translate import machine_translate
        a = Item("a", "paper", "Title", "u", abstract="One. Two. Three.")
        b = Item("b", "paper", "Title", "u", score_llm=8, title_zh="大模型写的")
        self.assertEqual(machine_translate(fake_http(), [a, b]), 1)
        self.assertEqual((a.title_zh, a.summary_zh, a.mt), ("机器译文", "机器译文", True))
        self.assertEqual(b.title_zh, "大模型写的")
        c = Item("c", "paper", "Title", "u")
        self.assertEqual(machine_translate(FakeHttp({}), [c]), 0)
        self.assertEqual(c.title_zh, "")


class LLMTests(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(extract_json('```json\n[{"a":1}]\n```'), [{"a": 1}])
        self.assertEqual(extract_json('Sure! Here: [{"a":2}] hope it helps'), [{"a": 2}])

    def test_judge_enriches_and_sanitises(self):
        items = sources.collect(fake_http(), TEST_CFG, {}, NOW)
        llm = LLM(transport=fake_llm_transport)
        n = llm.judge(items, batch=3)
        self.assertEqual(n, len(items))
        it = items[0]
        self.assertEqual(it.score_llm, 8)
        self.assertEqual(it.tags, ["推理"], "unknown tags must be dropped")
        self.assertTrue(it.title_zh.startswith("中文"))

    def test_judge_survives_garbage(self):
        items = [Item("x", "paper", "t", "u")]
        llm = LLM(transport=lambda s, u: "I cannot comply")
        self.assertEqual(llm.judge(items), 0)
        self.assertIsNone(items[0].score_llm)

    def test_provider_detection(self):
        self.assertEqual(LLM(api_key="sk-ant-xxx").provider, "anthropic")
        l = LLM(api_key="sk-xxx", provider="openai", base_url="https://api.deepseek.com/v1/", model="deepseek-chat")
        self.assertEqual((l.provider, l.base_url), ("openai", "https://api.deepseek.com/v1"))


if __name__ == "__main__":
    unittest.main()
