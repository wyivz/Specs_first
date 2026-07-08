from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.pipeline import SpecsFirstPipeline
from collectors.http import FetchResult, SearchResult
from collectors.real import RealCollector


class FakeHttp:
    def __init__(self) -> None:
        self.searches = {
            "Zeiss 50mm Lens official specifications": [
                SearchResult(
                    "Zeiss Makro-Planar T* 50mm f/2 official specifications",
                    "https://zeiss.example/specs",
                    "Official specifications manual",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 official specifications manual": [
                SearchResult("Manual", "https://zeiss.example/specs", "Official manual")
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:bilibili.com 评测 缺点 问题 翻车 体验": [
                SearchResult(
                    "B站评测：Zeiss 50mm 缺点汇总",
                    "https://www.bilibili.com/video/BVmock",
                    "评测中提到缺陷、卡顿和劝退点",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:youtube.com review defect issue problem quality": [
                SearchResult(
                    "YouTube review: Zeiss 50mm chromatic aberration",
                    "https://www.youtube.com/watch?v=mock-yt-zeiss",
                    "Purple fringing and focus ring issues discussed in review.",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:chiphell.com 缺点 品控 翻车 问题 体验": [
                SearchResult(
                    "Chiphell Zeiss 50mm 讨论",
                    "https://www.chiphell.com/thread-mock.html",
                    "产品质量问题和品控讨论",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:reddit.com defect issue quality problem review": [],
            "Zeiss Makro-Planar T* 50mm f/2 site:jd.com 到手价 优惠券 百亿补贴": [
                SearchResult(
                    "JD Zeiss 50mm",
                    "https://item.jd.com/mock-zeiss.html",
                    "标价 5999 元，优惠券 500，补贴 600，到手价 4899",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:taobao.com OR site:tmall.com 到手价 券后": [
                SearchResult(
                    "Tmall Zeiss 50mm",
                    "https://detail.tmall.com/item.htm?id=22334455",
                    "规格参数 详情参数",
                )
            ],
        }
        self.pages = {
            "https://zeiss.example/specs": """
                <html><title>Zeiss 50mm Official Specs</title>
                参数A: 50mm
                参数B: f/2
                重量: 530g
                参数C: 6 groups / 8 elements
                参数D: 0.24m
                参数E: 67mm</html>
            """,
            "https://www.bilibili.com/video/BVmock": "评测结论：有明显缺陷，操作卡顿，体验劝退。",
            "https://www.youtube.com/watch?v=mock-yt-zeiss": """
                <html><script>var ytInitialPlayerResponse = {"captions":{"playerCaptionsTracklistRenderer":{"captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?v=mock-yt-zeiss&lang=en","languageCode":"en"}]}}};</script></html>
            """,
            "https://www.youtube.com/api/timedtext?v=mock-yt-zeiss&lang=en": """
                <transcript><text start="0" dur="2">There is obvious purple fringing wide open.</text></transcript>
            """,
            "https://www.chiphell.com/thread-mock.html": "第887楼：产品质量问题，个体差异明显，疑似品控翻车。",
            "https://item.jd.com/mock-zeiss.html": "标价 5999 元，优惠券 500，补贴 600，到手价 4899",
            "https://item.jd.com/mock-specs.html": """
                <html><body>
                <script>window.__descApi="//api.m.jd.com/getdesc?sku=123"</script>
                <table>
                  <tr><th>重量</th><td>530g</td></tr>
                  <tr><th>兼容性</th><td>Sony E</td></tr>
                </table>
                <img data-src="https://img10.360buyimg.com/detail.jpg" />
                </body></html>
            """,
            "https://api.m.jd.com/getdesc?sku=123": """
                <html><body>
                <img original="https://img10.360buyimg.com/spec-1.jpg" />
                <table><tr><th>功耗</th><td>12W</td></tr></table>
                </body></html>
            """,
            "https://detail.tmall.com/item.htm?id=22334455": """
                <html><body>
                <script>var desc="//h5api.m.taobao.com/h5/mtop.taobao.detail.getdesc/6.0/?id=22334455";</script>
                <table><tr><th>电池容量</th><td>80瓦时</td></tr></table>
                </body></html>
            """,
            "https://h5api.m.taobao.com/h5/mtop.taobao.detail.getdesc/6.0/?id=22334455": """
                <html><body>
                <table><tr><th>重量</th><td>530克</td></tr></table>
                <img data-lazyload="https://img.alicdn.com/spec-taobao.jpg" />
                </body></html>
            """,
        }

    def search(self, query: str, max_results: int = 8):
        return self.searches.get(query, [])[:max_results]

    def fetch(self, url: str, *, platform: str = "", extra_headers=None):
        text = self.pages.get(url)
        if text is None:
            return FetchResult(url=url, status=404, text="", content_type="text/html", error="not found")
        return FetchResult(url=url, status=200, text=text, content_type="text/html")


class RealCollectorTest(unittest.TestCase):
    def test_real_collector_pipeline_with_fake_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            collector = RealCollector(http=FakeHttp())  # type: ignore[arg-type]
            result = SpecsFirstPipeline(collector=collector, vault_path=Path(tmp)).run("Zeiss 50mm", "Lens")

            self.assertEqual(result.state, "DONE")
            self.assertEqual(result.assets[0].price_real_world_min, 4899)
            self.assertTrue(result.assets[0].official_specs)
            self.assertGreaterEqual(len(result.assets[0].real_world_findings), 2)
            self.assertTrue(all(finding.evidence[0].url.startswith("https://") for finding in result.assets[0].real_world_findings))
            self.assertIn("dataview", "".join(path.read_text(encoding="utf-8") for path in result.output_paths).lower())

    def test_ecommerce_parameter_block_is_ingested_before_price(self) -> None:
        fake = FakeHttp()
        fake.searches["Zeiss Makro-Planar T* 50mm f/2 site:jd.com 到手价 优惠券 百亿补贴"] = [
            SearchResult(
                "JD Zeiss 50mm parameter page",
                "https://item.jd.com/mock-specs.html",
                "规格参数 详情参数",
            )
        ]
        collector = RealCollector(http=fake)  # type: ignore[arg-type]
        candidate = collector.discover_candidates("Zeiss 50mm", "Lens")[0]
        specs, highlights = collector.collect_official_specs(candidate)
        names = {spec.name for spec in specs}
        self.assertIn("weight", names)
        self.assertTrue(any("parameter block" in item for item in highlights))
        self.assertTrue(any("12" in spec.value and "w" in spec.value.lower() for spec in specs))
        self.assertTrue(any("530 g" in spec.value for spec in specs))
        self.assertTrue(any("80 Wh" in spec.value for spec in specs))


if __name__ == "__main__":
    unittest.main()
