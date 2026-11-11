# -*- coding: utf-8 -*-
"""Market review support for the Taiwan (``tw``) region.

Taiwan is already a first-class market for stock data (``2330.TW``), portfolio,
decision signals and ``get_main_indices("tw")`` (``^TWII`` / ``^TWOII``), but
``MARKET_REVIEW_REGION`` only accepted ``cn/hk/us/jp/kr/both``: setting ``tw``
was silently rewritten to ``cn`` with a warning, so TW users got an A-share
recap.
"""

from __future__ import annotations

import unittest

from src.utils.market_review_region import (
    MARKET_REVIEW_REGION_ALL,
    MARKET_REVIEW_REGION_SET,
    normalize_market_review_region_lenient,
    normalize_market_review_region_strict,
)


class TwMarketReviewRegionTest(unittest.TestCase):
    def test_tw_is_a_valid_region(self) -> None:
        self.assertIn("tw", MARKET_REVIEW_REGION_SET)
        self.assertEqual(normalize_market_review_region_lenient("tw"), "tw")
        self.assertEqual(normalize_market_review_region_lenient(" TW "), "tw")
        self.assertEqual(normalize_market_review_region_strict("tw"), "tw")

    def test_tw_combines_with_other_regions(self) -> None:
        self.assertEqual(
            normalize_market_review_region_lenient("tw,us"),
            "us,tw",
        )
        self.assertEqual(
            normalize_market_review_region_strict("tw,cn"),
            "cn,tw",
        )

    def test_both_expands_to_every_supported_region_including_tw(self) -> None:
        self.assertEqual(MARKET_REVIEW_REGION_ALL, "cn,hk,us,jp,kr,tw")
        self.assertEqual(normalize_market_review_region_lenient("both"), MARKET_REVIEW_REGION_ALL)


class TwMarketReviewWiringTest(unittest.TestCase):
    def test_config_keeps_tw_instead_of_falling_back_to_cn(self) -> None:
        from src.config import Config

        self.assertEqual(Config._parse_market_review_region("tw"), "tw")

    def test_market_review_resolves_tw(self) -> None:
        from src.core.market_review import (
            _market_review_region_metadata,
            _resolve_market_review_regions,
        )

        self.assertEqual(_resolve_market_review_regions("tw"), ["tw"])
        self.assertEqual(_market_review_region_metadata("tw"), "[dsa-market-region]: # (tw)\n\n")

    def test_market_review_heading_is_localized(self) -> None:
        from src.core.market_review import _get_market_review_market_heading

        self.assertEqual(_get_market_review_market_heading("zh", "tw"), "台股大盘复盘")
        self.assertEqual(_get_market_review_market_heading("en", "tw"), "Taiwan Market Recap")

    def test_profile_and_blueprint_exist_for_tw(self) -> None:
        from src.core.market_profile import get_profile
        from src.core.market_strategy import get_market_strategy_blueprint

        profile = get_profile("tw")
        self.assertEqual(profile.region, "tw")
        self.assertEqual(profile.mood_index_code, "TWII")
        # Taiwan has no equivalent of A-share 涨跌家数 / 涨停统计 in the data layer.
        self.assertFalse(profile.has_market_stats)
        self.assertFalse(profile.has_sector_rankings)

        blueprint = get_market_strategy_blueprint("tw")
        self.assertEqual(blueprint.region, "tw")
        self.assertIn("TAIEX", blueprint.to_prompt_block())

    def test_market_analyzer_accepts_tw_region(self) -> None:
        from src.market_analyzer import MarketAnalyzer

        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.region = "tw"
        analyzer.profile = __import__(
            "src.core.market_profile", fromlist=["get_profile"]
        ).get_profile("tw")
        analyzer.config = None

        self.assertEqual(analyzer._get_market_scope_name("zh"), "台股市场")
        self.assertEqual(analyzer._get_market_scope_name("en"), "Taiwan market")
        self.assertEqual(analyzer._get_turnover_unit_label(), "十亿新台币")
        self.assertEqual(analyzer._format_turnover_value(2.5e11), "250.00")


if __name__ == "__main__":
    unittest.main()
