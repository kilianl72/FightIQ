import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "sync_fighters.py"
SPEC = importlib.util.spec_from_file_location("sync_fighters", MODULE_PATH)
sync = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def make_state(fighters=None, sources=None, resolutions=None):
    return sync.PlannedState.from_rows(
        fighters or [],
        sources or [],
        resolutions or [],
        "2026-08-17T00:00:00+00:00",
    )


class NormalizationTests(unittest.TestCase):
    def test_normalizes_accents_and_punctuation(self):
        self.assertEqual(sync.normalize_name("José  Aldo Jr."), "jose aldo jr")

    def test_power_slap_is_excluded(self):
        profile = {
            "id": "slap-1",
            "name": "Example Athlete",
            "profileUrl": "https://powerslap.com/athlete/example",
            "raw": {"league": "Power Slap"},
        }
        self.assertEqual(sync.non_mma_reason(profile), "non_mma_power_slap")

    def test_ufc_profile_is_mma_evidence(self):
        profile = {
            "id": "mma-1",
            "name": "Example Fighter",
            "profileUrl": "https://www.ufc.com/athlete/example-fighter",
        }
        self.assertEqual(
            sync.has_documented_mma_evidence(profile),
            (True, "official_ufc_athlete_profile"),
        )

    def test_name_variants_cover_full_inverted_and_nickname_forms(self):
        variants = sync.identity_name_variants(
            None, "José", "Aldo", "Junior"
        )
        self.assertIn("jose aldo", variants)
        self.assertIn("aldo jose", variants)
        self.assertIn("junior", variants)
        self.assertIn("jose junior aldo", variants)

    def test_place_matching_requires_more_than_a_country(self):
        self.assertEqual(
            sync.place_compatibility("Makhachkala, Russia", "Makhachkala"),
            "shared_specific_component",
        )
        self.assertEqual(
            sync.place_compatibility("Brasil", "Brazil"),
            "broad_only",
        )

    def test_birthplace_change_marks_cito_profile_as_evolved(self):
        before = {"id": "cito-1", "name": "Example", "placeOfBirth": "Paris"}
        after = {"id": "cito-1", "name": "Example", "placeOfBirth": "Lyon"}
        self.assertNotEqual(
            sync.cito_fingerprint(before),
            sync.cito_fingerprint(after),
        )


class IdentityTests(unittest.TestCase):
    def test_later_override_registry_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / "historical.json"
            overrides = root / "overrides.json"
            historical.write_text(
                json.dumps(
                    {
                        "links": [
                            {
                                "cito_id": "cito-1",
                                "ufcstats_id": "ufc-1",
                            }
                        ]
                    }
                )
            )
            overrides.write_text(
                json.dumps(
                    {
                        "exclusions": [
                            {
                                "cito_id": "cito-1",
                                "resolution_reason": "manual_exclusion",
                            }
                        ]
                    }
                )
            )
            registry = sync.load_resolution_registry(
                f"{historical},{overrides}"
            )
        self.assertNotIn("cito-1", registry.links)
        self.assertIn("cito-1", registry.exclusions)

    def test_backfills_missing_primary_source_mappings(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_existing",
                    "display_name": "Existing Fighter",
                    "ufcstats_id": "ufc-existing",
                    "cito_id": "cito-existing",
                }
            ]
        )
        sync.backfill_existing_source_mappings(state)
        self.assertEqual(
            state.sources[("ufcstats", "ufc-existing")]["fightiq_id"],
            "fiq_existing",
        )
        self.assertEqual(
            state.sources[("cito", "cito-existing")]["fightiq_id"],
            "fiq_existing",
        )

    def test_ufcstats_update_preserves_existing_fightiq_id(self):
        fighter = {
            "fightiq_id": "fiq_stable",
            "display_name": "Old Name",
            "ufcstats_id": "source-1",
            "cito_id": None,
        }
        state = make_state(
            [fighter],
            [
                {
                    "fightiq_id": "fiq_stable",
                    "source": "ufcstats",
                    "source_id": "source-1",
                    "source_name": "Old Name",
                    "is_primary": True,
                }
            ],
        )
        sync.apply_ufcstats_plan(
            state,
            [
                {
                    "ufcstats_id": "source-1",
                    "display_name": "Corrected Name",
                }
            ],
        )
        self.assertEqual(set(state.fighters), {"fiq_stable"})
        self.assertEqual(
            state.fighters["fiq_stable"]["display_name"], "Corrected Name"
        )

    def test_exact_name_and_dob_match_existing_fighter(self):
        fighter = {
            "fightiq_id": "fiq_abc",
            "display_name": "Alex Example",
            "first_name": "Alex",
            "last_name": "Example",
            "date_of_birth": "1990-02-03",
            "ufcstats_id": "abc",
            "cito_id": None,
        }
        state = make_state(
            [fighter],
            [
                {
                    "fightiq_id": "fiq_abc",
                    "source": "ufcstats",
                    "source_id": "abc",
                    "source_name": "Alex Example",
                    "is_primary": True,
                }
            ],
        )
        profile = {
            "id": "cito-abc",
            "name": "Alex Example",
            "firstName": "Alex",
            "lastName": "Example",
            "birthDate": "1990-02-03",
        }
        match, reason, _ = sync.automatic_identity_match(profile, state)
        self.assertEqual(reason, "automatic_evidence_match")
        self.assertEqual(match["fightiq_id"], "fiq_abc")

    def test_name_only_does_not_force_match(self):
        fighter = {
            "fightiq_id": "fiq_abc",
            "display_name": "John Smith",
            "first_name": "John",
            "last_name": "Smith",
            "ufcstats_id": "abc",
            "cito_id": None,
        }
        state = make_state(
            [fighter],
            [
                {
                    "fightiq_id": "fiq_abc",
                    "source": "ufcstats",
                    "source_id": "abc",
                    "source_name": "John Smith",
                    "is_primary": True,
                }
            ],
        )
        profile = {"id": "new-cito", "name": "John Smith"}
        match, reason, _ = sync.automatic_identity_match(profile, state)
        self.assertIsNone(match)
        self.assertTrue(reason.startswith("insufficient_score"))

    def test_different_alias_links_on_same_fights_and_compatible_results(self):
        fighter = {
            "fightiq_id": "fiq_alias",
            "display_name": "Muhammad Aliyev",
            "date_of_birth": "1992-04-05",
            "ufcstats_id": "ufc-alias",
            "cito_id": None,
        }
        state = make_state(
            [fighter],
            [
                {
                    "fightiq_id": "fiq_alias",
                    "source": "ufcstats",
                    "source_id": "ufc-alias",
                    "source_name": "Muhammad Aliyev",
                    "is_primary": True,
                }
            ],
        )
        profile = {
            "id": "cito-alias",
            "name": "Magomed Aliev",
            "slug": "magomed-aliev",
            "birthDate": "1992-04-05",
        }
        cito_history = [
            {
                "bout_id": "bout-111111",
                "event_name": "ufc example one",
                "event_date": "2025-01-01",
                "opponent": "first opponent",
                "outcome": "W",
            },
            {
                "bout_id": "bout-222222",
                "event_name": "ufc example two",
                "event_date": "2025-06-01",
                "opponent": "second opponent",
                "outcome": "L",
            },
        ]
        ufc_histories = {
            "ufc-alias": [dict(row) for row in cito_history]
        }
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            ufc_histories,
            cito_history,
        )
        self.assertEqual(reason, "automatic_evidence_match")
        self.assertEqual(match["fightiq_id"], "fiq_alias")
        self.assertIn(
            "fight_history_decisive",
            evidence[0]["reasons"],
        )
        self.assertEqual(evidence[0]["fight_history"]["matched_bouts"], 2)
        self.assertIn(
            "date_of_birth",
            evidence[0]["biographical_evidence"]["independent_signals"],
        )

    def test_same_fights_without_independent_bio_never_links_alias(self):
        fighter = {
            "fightiq_id": "fiq_no_bio",
            "display_name": "Muhammad Aliyev",
            "ufcstats_id": "ufc-no-bio",
        }
        state = make_state([fighter])
        profile = {"id": "cito-no-bio", "name": "Magomed Aliev"}
        history = [
            {
                "bout_id": "bout-111111",
                "event_name": "ufc example one",
                "opponent": "first opponent",
                "outcome": "W",
            },
            {
                "bout_id": "bout-222222",
                "event_name": "ufc example two",
                "opponent": "second opponent",
                "outcome": "L",
            },
        ]
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-no-bio": [dict(row) for row in history]},
            history,
        )
        self.assertIsNone(match)
        self.assertEqual(reason, "no_candidate")
        self.assertTrue(evidence[0]["rejected"])
        self.assertIn(
            "fight_history_without_independent_bio",
            evidence[0]["reasons"],
        )

    def test_alias_same_fights_and_specific_birthplace_links(self):
        fighter = {
            "fightiq_id": "fiq_place",
            "display_name": "Canonical Name",
            "place_of_birth": "Makhachkala, Dagestan, Russia",
            "ufcstats_id": "ufc-place",
        }
        state = make_state([fighter])
        profile = {
            "id": "cito-place",
            "name": "Different Alias",
            "placeOfBirth": "Makhachkala, Russia",
        }
        history = [
            {
                "bout_id": "place-bout-1",
                "opponent": "first opponent",
                "outcome": "W",
            },
            {
                "bout_id": "place-bout-2",
                "opponent": "second opponent",
                "outcome": "W",
            },
        ]
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-place": [dict(row) for row in history]},
            history,
        )
        self.assertEqual(reason, "automatic_evidence_match")
        self.assertEqual(match["fightiq_id"], "fiq_place")
        self.assertIn(
            "place_of_birth",
            evidence[0]["biographical_evidence"]["independent_signals"],
        )

    def test_alias_same_fights_and_two_physical_signals_links(self):
        fighter = {
            "fightiq_id": "fiq_physical",
            "display_name": "Canonical Name",
            "height_cm": 180.0,
            "reach_cm": 190.0,
            "ufcstats_id": "ufc-physical",
        }
        state = make_state([fighter])
        profile = {
            "id": "cito-physical",
            "name": "Different Alias",
            "heightInches": 70.87,
            "reachInches": 74.8,
        }
        history = [
            {
                "bout_id": "physical-bout-1",
                "opponent": "first opponent",
                "outcome": "W",
            },
            {
                "bout_id": "physical-bout-2",
                "opponent": "second opponent",
                "outcome": "L",
            },
        ]
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-physical": [dict(row) for row in history]},
            history,
        )
        self.assertEqual(reason, "automatic_evidence_match")
        self.assertEqual(match["fightiq_id"], "fiq_physical")
        self.assertIn(
            "physical_profile",
            evidence[0]["biographical_evidence"]["independent_signals"],
        )

    def test_country_only_does_not_confirm_history_alias(self):
        fighter = {
            "fightiq_id": "fiq_country",
            "display_name": "Canonical Name",
            "place_of_birth": "Brazil",
            "ufcstats_id": "ufc-country",
        }
        state = make_state([fighter])
        profile = {
            "id": "cito-country",
            "name": "Different Alias",
            "placeOfBirth": "Brasil",
        }
        history = [
            {
                "bout_id": "country-bout-1",
                "opponent": "first opponent",
                "outcome": "W",
            },
            {
                "bout_id": "country-bout-2",
                "opponent": "second opponent",
                "outcome": "L",
            },
        ]
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-country": [dict(row) for row in history]},
            history,
        )
        self.assertIsNone(match)
        self.assertEqual(reason, "no_candidate")
        self.assertEqual(
            evidence[0]["biographical_evidence"]["place_relation"],
            "broad_only",
        )

    def test_conflicting_birth_dates_block_automatic_match(self):
        fighter = {
            "fightiq_id": "fiq_dob_conflict",
            "display_name": "Same Name",
            "date_of_birth": "1990-01-01",
            "ufcstats_id": "ufc-dob-conflict",
        }
        state = make_state([fighter])
        profile = {
            "id": "cito-dob-conflict",
            "name": "Same Name",
            "birthDate": "1990-02-02",
        }
        match, reason, evidence = sync.automatic_identity_match(profile, state)
        self.assertIsNone(match)
        self.assertEqual(reason, "no_candidate")
        self.assertIn("date_of_birth_conflict", evidence[0]["reasons"])

    def test_same_record_without_common_fights_never_links_alias(self):
        fighter = {
            "fightiq_id": "fiq_record",
            "display_name": "Different Person",
            "ufcstats_id": "ufc-record",
            "cito_record_wins": 10,
            "cito_record_losses": 2,
            "cito_record_draws": 0,
            "cito_record_nc": 0,
        }
        state = make_state([fighter])
        profile = {
            "id": "cito-record",
            "name": "Unknown Alias",
            "recordWins": 10,
            "recordLosses": 2,
            "recordDraws": 0,
        }
        match, reason, _ = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-record": []},
            [],
        )
        self.assertIsNone(match)
        self.assertEqual(reason, "no_candidate")

    def test_conflicting_result_blocks_history_alias_match(self):
        fighter = {
            "fightiq_id": "fiq_conflict",
            "display_name": "Candidate Fighter",
            "ufcstats_id": "ufc-conflict",
        }
        state = make_state([fighter])
        profile = {"id": "cito-conflict", "name": "Candidate Fighter"}
        cito_history = [
            {
                "bout_id": "bout-conflict",
                "opponent": "same opponent",
                "event_name": "same event",
                "outcome": "W",
            }
        ]
        ufc_history = [
            {
                "bout_id": "bout-conflict",
                "opponent": "same opponent",
                "event_name": "same event",
                "outcome": "L",
            }
        ]
        match, reason, evidence = sync.automatic_identity_match(
            profile,
            state,
            {"ufc-conflict": ufc_history},
            cito_history,
        )
        self.assertIsNone(match)
        self.assertEqual(reason, "no_candidate")
        self.assertTrue(evidence[0]["rejected"])
        self.assertIn("fight_history_conflict", evidence[0]["reasons"])

    def test_ambiguous_profile_is_quarantined_outside_fighters(self):
        state = make_state()
        profile = {"id": "unknown-1", "name": "Unknown Person"}
        review = sync.plan_cito_profiles(
            state,
            [profile],
            sync.ResolutionRegistry(),
        )
        self.assertEqual(len(review), 1)
        self.assertEqual(
            state.resolutions["unknown-1"]["resolution_status"],
            "quarantined",
        )
        self.assertIn(
            "_fightiq_quarantine",
            state.resolutions["unknown-1"]["raw_json"],
        )
        self.assertEqual(state.fighters, {})

    def test_quarantine_does_not_block_safe_profile_update(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_safe",
                    "display_name": "Safe Fighter",
                    "ufcstats_id": "ufc-safe",
                    "cito_id": None,
                }
            ],
            [
                {
                    "fightiq_id": "fiq_safe",
                    "source": "ufcstats",
                    "source_id": "ufc-safe",
                    "source_name": "Safe Fighter",
                    "is_primary": True,
                }
            ],
        )
        quarantine = sync.plan_cito_profiles(
            state,
            [
                {
                    "id": "cito-safe",
                    "ufcStatsId": "ufc-safe",
                    "name": "Safe Fighter",
                    "division": "Lightweight",
                },
                {"id": "cito-unknown", "name": "Unknown Person"},
            ],
            sync.ResolutionRegistry(),
        )
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(
            state.fighters["fiq_safe"]["cito_id"],
            "cito-safe",
        )
        self.assertEqual(
            state.fighters["fiq_safe"]["current_division"],
            "Lightweight",
        )
        self.assertNotIn("fiq_cito_citounknown", state.fighters)

    def test_curated_create_blocks_name_only_collision(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_existing",
                    "display_name": "Kerry Vera",
                    "ufcstats_id": None,
                    "cito_id": None,
                }
            ]
        )
        profile = {"id": "cito-kerry", "name": "Kerry Kasik"}
        with self.assertRaisesRegex(RuntimeError, "name collision"):
            sync.process_create_override(
                state,
                {
                    "display_name": "Kerry Vera",
                    "canonical_cito_id": "cito-kerry",
                    "cito_ids": ["cito-kerry"],
                    "cito_names": ["Kerry Kasik"],
                    "resolution_reason": "verified_identity",
                },
                {"cito-kerry": profile},
            )
        self.assertEqual(set(state.fighters), {"fiq_existing"})
        self.assertNotIn(("cito", "cito-kerry"), state.sources)

    def test_secondary_cito_alias_does_not_overwrite_primary_profile(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_primary",
                    "display_name": "Primary Fighter",
                    "ufcstats_id": "ufc-1",
                    "cito_id": "cito-primary",
                    "cito_slug": "primary-fighter",
                    "current_division": "Lightweight",
                }
            ],
            [
                {
                    "fightiq_id": "fiq_primary",
                    "source": "ufcstats",
                    "source_id": "ufc-1",
                    "source_name": "Primary Fighter",
                    "is_primary": True,
                },
                {
                    "fightiq_id": "fiq_primary",
                    "source": "cito",
                    "source_id": "cito-primary",
                    "source_name": "Primary Fighter",
                    "is_primary": True,
                },
                {
                    "fightiq_id": "fiq_primary",
                    "source": "cito",
                    "source_id": "cito-alias",
                    "source_name": "Localized Alias",
                    "is_primary": False,
                },
            ],
        )
        review = sync.plan_cito_profiles(
            state,
            [
                {
                    "id": "cito-alias",
                    "name": "Localized Alias",
                    "slug": "localized-alias",
                    "division": "Heavyweight",
                }
            ],
            sync.ResolutionRegistry(),
        )
        self.assertEqual(review, [])
        fighter = state.fighters["fiq_primary"]
        self.assertEqual(fighter["cito_slug"], "primary-fighter")
        self.assertEqual(fighter["current_division"], "Lightweight")

    def test_admin_terminal_classification_does_not_reappear(self):
        resolution = {
            "cito_id": "cito-slap",
            "name": "Reviewed Profile",
            "resolution_status": "quarantined",
            "resolution_reason": "manual_review",
            "review_status": "approved",
            "review_classification": "power_slap",
            "admin_action": "exclude",
            "raw_json": {"id": "cito-slap", "name": "Reviewed Profile"},
        }
        state = make_state(resolutions=[resolution])
        profile = {"id": "cito-slap", "name": "Reviewed Profile"}
        self.assertEqual(
            sync.plan_cito_profiles(
                state, [profile], sync.ResolutionRegistry()
            ),
            [],
        )
        stored = state.resolutions["cito-slap"]
        self.assertEqual(stored["resolution_status"], "excluded")
        self.assertEqual(stored["review_status"], "applied")

        next_state = sync.PlannedState.from_rows(
            [], [], list(state.resolutions.values()), state.now
        )
        changed_profile = {
            "id": "cito-slap",
            "name": "Reviewed Profile Updated",
        }
        self.assertEqual(
            sync.plan_cito_profiles(
                next_state,
                [changed_profile],
                sync.ResolutionRegistry(),
            ),
            [],
        )
        self.assertEqual(
            next_state.resolutions["cito-slap"]["resolution_status"],
            "excluded",
        )

    def test_admin_duplicate_profile_links_as_secondary_alias(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_target",
                    "display_name": "Canonical Fighter",
                    "ufcstats_id": "ufc-target",
                    "cito_id": "cito-primary",
                }
            ],
            [
                {
                    "fightiq_id": "fiq_target",
                    "source": "ufcstats",
                    "source_id": "ufc-target",
                    "source_name": "Canonical Fighter",
                    "is_primary": True,
                },
                {
                    "fightiq_id": "fiq_target",
                    "source": "cito",
                    "source_id": "cito-primary",
                    "source_name": "Canonical Fighter",
                    "is_primary": True,
                },
            ],
            [
                {
                    "cito_id": "cito-duplicate",
                    "name": "Localized Alias",
                    "resolution_status": "quarantined",
                    "review_status": "approved",
                    "review_classification": "duplicate_cito_profile",
                    "admin_action": "link",
                    "target_fightiq_id": "fiq_target",
                    "raw_json": {
                        "id": "cito-duplicate",
                        "name": "Localized Alias",
                    },
                }
            ],
        )
        review = sync.plan_cito_profiles(
            state,
            [{"id": "cito-duplicate", "name": "Localized Alias"}],
            sync.ResolutionRegistry(),
        )
        self.assertEqual(review, [])
        source = state.sources[("cito", "cito-duplicate")]
        self.assertEqual(source["fightiq_id"], "fiq_target")
        self.assertFalse(source["is_primary"])
        self.assertEqual(
            state.resolutions["cito-duplicate"]["review_status"],
            "applied",
        )


class FightHistoryTests(unittest.TestCase):
    def test_normalizes_common_cito_fight_history_shape(self):
        rows = sync.normalize_cito_fight_history(
            {
                "fights": [
                    {
                        "boutId": "ufc-abcdef1234567890",
                        "opponent": {"name": "Other Fighter"},
                        "event": {
                            "name": "UFC Example",
                            "date": "2026-08-15",
                        },
                        "result": "Win",
                        "method": "Decision - Unanimous",
                    }
                ]
            },
            {"id": "cito-1", "name": "Example Fighter"},
        )
        self.assertEqual(rows[0]["bout_id"], "abcdef1234567890")
        self.assertEqual(rows[0]["opponent"], "other fighter")
        self.assertEqual(rows[0]["event_name"], "ufc example")
        self.assertEqual(rows[0]["outcome"], "W")

    def test_builds_ufcstats_history_with_event_date_and_outcomes(self):
        histories = sync.build_ufc_fight_histories(
            [
                {
                    "EVENT": "UFC Test",
                    "BOUT": "Alpha Fighter vs. Beta Fighter",
                    "OUTCOME": "W/L",
                    "METHOD": "Decision - Unanimous",
                    "URL": "http://ufcstats.com/fight-details/abcdef1234567890",
                }
            ],
            [{"EVENT": "UFC Test", "DATE": "August 15, 2026"}],
            [
                {"ufcstats_id": "alpha-id", "display_name": "Alpha Fighter"},
                {"ufcstats_id": "beta-id", "display_name": "Beta Fighter"},
            ],
        )
        self.assertEqual(histories["alpha-id"][0]["opponent"], "beta fighter")
        self.assertEqual(histories["alpha-id"][0]["outcome"], "W")
        self.assertEqual(histories["beta-id"][0]["outcome"], "L")
        self.assertEqual(
            histories["alpha-id"][0]["event_date"], "2026-08-15"
        )

    def test_homonymous_ufcstats_names_are_not_guessed(self):
        histories = sync.build_ufc_fight_histories(
            [
                {
                    "EVENT": "UFC Test",
                    "BOUT": "Same Name vs. Opponent",
                    "OUTCOME": "W/L",
                    "URL": "http://ufcstats.com/fight-details/abcdef1234567890",
                }
            ],
            [],
            [
                {"ufcstats_id": "same-1", "display_name": "Same Name"},
                {"ufcstats_id": "same-2", "display_name": "Same Name"},
            ],
        )
        self.assertNotIn("same-1", histories)
        self.assertNotIn("same-2", histories)

    def test_admin_report_contains_full_pending_queue(self):
        state = make_state(
            resolutions=[
                {
                    "cito_id": "pending-1",
                    "name": "Pending Fighter",
                    "resolution_status": "quarantined",
                    "resolution_reason": "ambiguous",
                    "raw_json": {
                        "_fightiq_quarantine": {
                            "candidates": [{"fightiq_id": "fiq-1"}],
                            "fight_history": {"bouts": 2},
                        }
                    },
                },
                {
                    "cito_id": "done-1",
                    "resolution_status": "excluded",
                },
            ]
        )
        report = sync.build_admin_quarantine_report(state)
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["profiles"][0]["review_status"], "pending")
        self.assertEqual(
            report["profiles"][0]["candidates"][0]["fightiq_id"],
            "fiq-1",
        )


class ValidationTests(unittest.TestCase):
    def test_three_category_model_passes(self):
        fighters = [
            {
                "fightiq_id": "fiq_both",
                "display_name": "Both",
                "ufcstats_id": "u1",
                "cito_id": "c1",
            },
            {
                "fightiq_id": "fiq_ufc",
                "display_name": "UFC",
                "ufcstats_id": "u2",
                "cito_id": None,
            },
            {
                "fightiq_id": "fiq_cito",
                "display_name": "Cito",
                "ufcstats_id": None,
                "cito_id": "c2",
            },
        ]
        sources = [
            {"fightiq_id": "fiq_both", "source": "ufcstats", "source_id": "u1"},
            {"fightiq_id": "fiq_both", "source": "cito", "source_id": "c1"},
            {"fightiq_id": "fiq_ufc", "source": "ufcstats", "source_id": "u2"},
            {"fightiq_id": "fiq_cito", "source": "cito", "source_id": "c2"},
        ]
        resolutions = [
            {
                "cito_id": "c2",
                "resolution_status": "created_new_fighter",
                "matched_fightiq_id": "fiq_cito",
            }
        ]
        report = sync.validate_state(
            make_state(fighters, sources, resolutions), minimum_fighters=3
        )
        self.assertEqual(report["ufcstats_cito"], 1)
        self.assertEqual(report["ufcstats_only"], 1)
        self.assertEqual(report["cito_only"], 1)
        self.assertEqual(report["unresolved"], 0)
        self.assertEqual(report["quarantined"], 0)

    def test_explicit_quarantine_is_valid_but_counted(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_u",
                    "display_name": "UFC",
                    "ufcstats_id": "u1",
                    "cito_id": None,
                }
            ],
            [{"fightiq_id": "fiq_u", "source": "ufcstats", "source_id": "u1"}],
            [{"cito_id": "unknown", "resolution_status": "quarantined"}],
        )
        report = sync.validate_state(state, minimum_fighters=1)
        self.assertEqual(report["unresolved"], 0)
        self.assertEqual(report["quarantined"], 1)

    def test_unresolved_row_fails(self):
        state = make_state(
            [
                {
                    "fightiq_id": "fiq_u",
                    "display_name": "UFC",
                    "ufcstats_id": "u1",
                    "cito_id": None,
                }
            ],
            [{"fightiq_id": "fiq_u", "source": "ufcstats", "source_id": "u1"}],
            [{"cito_id": "unknown", "resolution_status": None}],
        )
        with self.assertRaisesRegex(RuntimeError, "unresolved Cito rows"):
            sync.validate_state(state, minimum_fighters=1)


if __name__ == "__main__":
    unittest.main()
