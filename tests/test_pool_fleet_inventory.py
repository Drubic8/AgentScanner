"""Model-only user inventory stays complete and never becomes firmware evidence."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PoolFleetInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads((ROOT / "tests" / "fixtures" /
                                    "pool_fleet_models.json").read_text(encoding="utf-8"))
        cls.models = cls.inventory["models"]
        cls.labels = [label for model in cls.models for label in model["labels"]]

    def test_all_23_models_and_40_variants_are_retained(self):
        self.assertEqual(len(self.models), 23)
        self.assertEqual(len({model["model"] for model in self.models}), 23)
        self.assertEqual(len(self.labels), 40)
        self.assertEqual(len(set(self.labels)), 40)
        expected = {
            "Antminer D9_1770G", "Antminer KS5_20T", "Antminer KS5_21T",
            "Antminer L7_8550M", "Antminer L7_8800M", "Antminer L7_9050M",
            "Antminer L9_15G", "Antminer L9_16.5G", "Antminer L9_16G", "Antminer L9_17G",
            "Antminer S19k Pro_120T", "Antminer S21 Pro_220T", "Antminer S21 Pro_234T",
            "Antminer S21_175T", "Antminer S21_188T", "Antminer S21_195T", "Antminer S21_200T",
            "Antminer S21+_235T", "Antminer S21++_216th",
            "Antminer T21_180T", "Antminer T21_186T", "Antminer T21_190T",
            "Antminer Z11_135kSol/s", "Antminer Z15 Pro_840kSol/s", "Antminer Z15 Pro_860kSol/s",
            "Antminer Z15_420kSol/s", "Antminer Z15e_200", "AvalonMiner1346-110",
            "Elphapex DG1_11G", "Elphapex DG1+_13,6G", "Elphapex DG1+_14,4G", "Elphapex DG1+_14G",
            "Whatsminer М60_176Т", "Whatsminer М60S+_206Т", "Whatsminer М61S+_236Т",
            "Whatsminer M50_118T", "Whatsminer M50_122T", "Whatsminer M50S++",
            "Whatsminer M61_202T", "Whatsminer M61S+_238T",
        }
        self.assertEqual(set(self.labels), expected)

    def test_inventory_does_not_assert_installed_firmware_or_hardware_acceptance(self):
        self.assertEqual(self.inventory["scope"], "hardware_models_only")
        self.assertEqual(self.inventory["source"], "user_model_inventory")
        self.assertIsNone(self.inventory["firmware"])
        self.assertIsNone(self.inventory["firmware_version"])
        self.assertIs(self.inventory["device_verified_pool_changes"], False)
        for model in self.models:
            self.assertEqual(set(model), {"make", "model", "stock_pool_contract", "labels"})

    def test_stock_candidate_inventory_matches_only_existing_model_maps(self):
        definitions = json.loads((ROOT / "miner_scanner" / "profiles" /
                                  "stock_interfaces.json").read_text(encoding="utf-8"))["miner"]
        mapped = {model for definition in definitions.values() for model in definition["modes"]}
        candidates = []
        for row in self.models:
            with self.subTest(model=row["model"]):
                name = row["model"].removeprefix("Antminer ")
                is_candidate = row["make"] == "Bitmain" and name in mapped
                self.assertEqual(row["stock_pool_contract"], "candidate" if is_candidate else "missing")
                if is_candidate:
                    candidates.append(row)
        self.assertEqual(len(candidates), 10)
        self.assertEqual(sum(len(row["labels"]) for row in candidates), 23)

    def test_similar_hardware_names_are_not_combined(self):
        by_model = {row["model"]: row for row in self.models}
        for name in ("Antminer S21++", "Antminer Z11", "Antminer Z15", "Antminer Z15e"):
            self.assertEqual(by_model[name]["stock_pool_contract"], "missing")
        self.assertEqual(by_model["Antminer S19k Pro"]["stock_pool_contract"], "candidate")
        self.assertEqual(by_model["Antminer S21+"]["stock_pool_contract"], "candidate")
        self.assertEqual(by_model["Antminer Z15 Pro"]["stock_pool_contract"], "candidate")
        self.assertEqual(by_model["Whatsminer M61S+"]["labels"],
                         ["Whatsminer М61S+_236Т", "Whatsminer M61S+_238T"])

    def test_fleet_document_retains_every_label_and_separate_firmware_branches(self):
        document = (ROOT / "docs" / "development" /
                    "pool_configuration_fleet.md").read_text(encoding="utf-8")
        for label in self.labels:
            with self.subTest(label=label):
                self.assertIn("`" + label + "`", document)
        self.assertIn("| Stock / штатный API | VNish | PitBit | Другие кастомные API |", document)
        self.assertIn("прошивка, её версия и интерфейс API для каждой строки неизвестны", document)


if __name__ == "__main__":
    unittest.main()
