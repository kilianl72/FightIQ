import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "enrich_rankings_cito.py"
SPEC = importlib.util.spec_from_file_location("enrich_rankings_cito", MODULE_PATH)
ranking = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = ranking
SPEC.loader.exec_module(ranking)


def fighter(fighter_id, name, **values):
    return {
        "id": fighter_id,
        "fightiq_id": f"fiq_{fighter_id}",
        "display_name": name,
        "first_name": name.split()[0],
        "last_name": " ".join(name.split()[1:]),
        "slug": values.pop("slug", None),
        "cito_slug": values.pop("cito_slug", None),
        "ufcstats_id": values.pop("ufcstats_id", None),
        "cito_id": values.pop("cito_id", None),
        "ufc_rank": values.pop("ufc_rank", None),
        "p4p_rank": values.pop("p4p_rank", None),
        "champion_status": values.pop("champion_status", None),
        "interim_champion": values.pop("interim_champion", False),
        "current_division": values.pop("current_division", None),
        **values,
    }


class RankingIdentityTests(unittest.TestCase):
    def test_exact_slug_resolves_before_name(self):
        fighters = [
            fighter("a", "Shared Name", slug="first-profile"),
            fighter("b", "Shared Name", slug="second-profile"),
        ]
        resolved, match_type = ranking.resolve_fighter(
            {"fighterName": "Shared Name", "fighterSlug": "second-profile"},
            ranking.build_indexes(fighters),
        )
        self.assertEqual(match_type, "slug")
        self.assertEqual(resolved["id"], "b")

    def test_division_and_p4p_rows_are_combined(self):
        fighters = [fighter("a", "Alex Example", slug="alex-example")]
        rows = [
            {
                "fighterName": "Alex Example",
                "fighterSlug": "alex-example",
                "division": "Lightweight",
                "rank": 3,
            }
            for _ in range(5)
        ] + [
            {
                "fighterName": "Alex Example",
                "fighterSlug": "alex-example",
                "division": "Pound-for-Pound",
                "rank": 5,
            }
            for _ in range(5)
        ]
        updates, report = ranking.plan_rankings(rows, fighters, "now")
        self.assertEqual(report["unmatched"], [])
        patch = dict(updates)["a"]
        self.assertEqual(patch["ufc_rank"], 3)
        self.assertEqual(patch["p4p_rank"], 5)
        self.assertEqual(patch["current_division"], "Lightweight")


class RankingSafetyTests(unittest.TestCase):
    def test_unmatched_row_skips_stale_ranking_cleanup(self):
        fighters = [
            fighter("current", "Current Fighter", slug="current-fighter"),
            fighter("stale", "Former Ranked", ufc_rank=12),
        ]
        rows = [
            {
                "fighterName": "Current Fighter",
                "fighterSlug": "current-fighter",
                "division": "Lightweight",
                "rank": 4,
            }
            for _ in range(9)
        ]
        rows.append({"fighterName": "Unknown Ranking Name", "rank": 15})
        updates, report = ranking.plan_rankings(rows, fighters, "now")
        self.assertTrue(report["stale_cleanup_skipped"])
        self.assertNotIn("stale", dict(updates))
        self.assertEqual(fighters[1]["ufc_rank"], 12)

    def test_complete_snapshot_clears_stale_ranking(self):
        fighters = [
            fighter("current", "Current Fighter", slug="current-fighter"),
            fighter("stale", "Former Ranked", ufc_rank=12),
        ]
        rows = [
            {
                "fighterName": "Current Fighter",
                "fighterSlug": "current-fighter",
                "division": "Lightweight",
                "rank": 4,
            }
            for _ in range(10)
        ]
        updates, report = ranking.plan_rankings(rows, fighters, "now")
        self.assertFalse(report["stale_cleanup_skipped"])
        stale_patch = dict(updates)["stale"]
        self.assertIsNone(stale_patch["ufc_rank"])
        self.assertNotIn("interim_champion", stale_patch)

    def test_low_coverage_snapshot_never_clears_previous_rankings(self):
        fighters = [
            fighter("current", "Current Fighter", slug="current-fighter")
        ] + [
            fighter(f"stale-{index}", f"Former Ranked {index}", ufc_rank=index)
            for index in range(1, 21)
        ]
        rows = [
            {
                "fighterName": "Current Fighter",
                "fighterSlug": "current-fighter",
                "division": "Lightweight",
                "rank": 4,
            }
            for _ in range(10)
        ]
        updates, report = ranking.plan_rankings(rows, fighters, "now")
        self.assertTrue(report["stale_cleanup_skipped"])
        self.assertLess(report["ranking_coverage_ratio"], 0.75)
        update_ids = set(dict(updates))
        self.assertFalse(any(item.startswith("stale-") for item in update_ids))


if __name__ == "__main__":
    unittest.main()
