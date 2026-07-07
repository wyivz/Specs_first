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
            "Zeiss Makro-Planar T* 50mm f/2 site:bilibili.com 评测 紫边 对焦 卡顿": [
                SearchResult(
                    "B站评测：Zeiss 50mm 紫边明显",
                    "https://www.bilibili.com/video/BVmock",
                    "大光圈紫边明显，对焦环阻尼偶发卡顿",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:youtube.com review chromatic aberration focus ring issue": [],
            "Zeiss Makro-Planar T* 50mm f/2 site:chiphell.com 色散 阻尼 品控 翻车": [
                SearchResult(
                    "Chiphell Zeiss 50mm 翻车记录",
                    "https://www.chiphell.com/thread-mock.html",
                    "对焦环阻尼不均，疑似品控问题",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:reddit.com chromatic aberration focus ring copy variation": [],
            "Zeiss Makro-Planar T* 50mm f/2 site:jd.com 到手价 优惠券 百亿补贴": [
                SearchResult(
                    "JD Zeiss 50mm",
                    "https://item.jd.com/mock-zeiss.html",
                    "标价 5999 元，优惠券 500，补贴 600，到手价 4899",
                )
            ],
            "Zeiss Makro-Planar T* 50mm f/2 site:taobao.com OR site:tmall.com 到手价 券后": [],
        }
        self.pages = {
            "https://zeiss.example/specs": """
                <html><title>Zeiss 50mm Official Specs</title>
                Focal Length: 50mm. Maximum Aperture: f/2.
                Weight: 530g. Optical Structure: 6 groups / 8 elements.
                Minimum Focus Distance: 0.24m. Filter Thread: 67mm.</html>
            """,
            "https://www.bilibili.com/video/BVmock": "这支镜头大光圈紫边明显，对焦环阻尼偶尔卡顿。",
            "https://www.chiphell.com/thread-mock.html": "第887楼：对焦环阻尼不均，疑似品控问题。",
            "https://item.jd.com/mock-zeiss.html": "标价 5999 元，优惠券 500，补贴 600，到手价 4899",
        }

    def search(self, query: str, max_results: int = 8):
        return self.searches.get(query, [])[:max_results]

    def fetch(self, url: str):
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


if __name__ == "__main__":
    unittest.main()
