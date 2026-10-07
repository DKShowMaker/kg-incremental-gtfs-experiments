import csv
import tempfile
import unittest
from pathlib import Path

from partitioning import partition_for_trip_id
from run_distributed_update import conflict_barrier_participants, partition_delta


class PartitionDeltaSubpartitionTest(unittest.TestCase):
    def test_conflict_barrier_uses_nonempty_tasks_and_worker_limit(self):
        unsplit = [{"rows": 10}, {"rows": 3}, {"rows": 0}, {"rows": 0}]
        split = [{"rows": 3}, {"rows": 3}, {"rows": 3},
                 {"rows": 2}, {"rows": 3}, {"rows": 0}, {"rows": 0}]
        self.assertEqual(conflict_barrier_participants(unsplit, 4), 2)
        self.assertEqual(conflict_barrier_participants(split, 4), 4)

    def test_hot_partition_is_split_into_disjoint_balanced_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delta = root / "delta.csv"
            buckets = {0: [], 1: []}
            candidate = 0
            while len(buckets[0]) < 9 or len(buckets[1]) < 1:
                trip_id = str(candidate)
                buckets[partition_for_trip_id(trip_id, 2)].append(trip_id)
                candidate += 1
            trip_ids = buckets[0][:9] + buckets[1][:1]
            with delta.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=("trip_id", "stop_sequence"))
                writer.writeheader()
                for seq, trip_id in enumerate(trip_ids):
                    writer.writerow({"trip_id": trip_id, "stop_sequence": seq})

            counts, duplicates, tasks = partition_delta(
                delta, 2, root / "parts", subpartition_threshold=1.7,
                subpartition_factor=2)

            self.assertEqual(counts, [9, 1])
            self.assertEqual(duplicates, 0)
            self.assertEqual(len(tasks), 3)
            split = [task for task in tasks if task["partition_id"] == 0]
            self.assertEqual([task["rows"] for task in split], [5, 4])
            self.assertTrue(all(task["subpartition_id"] is not None for task in split))
            self.assertIsNone(next(task for task in tasks
                                   if task["partition_id"] == 1)["subpartition_id"])

            key_sets = []
            for task in tasks:
                task_dir = task["data_dir"]
                with (task_dir / "keys.txt").open(encoding="utf-8") as f:
                    key_sets.append(set(f.read().splitlines()))
                with (task_dir / "stop_times.txt").open(newline="", encoding="utf-8") as f:
                    self.assertEqual(sum(1 for _ in csv.DictReader(f)), task["rows"])
            self.assertEqual(sum(map(len, key_sets)), 10)
            self.assertEqual(len(set.union(*key_sets)), 10)

    def test_missing_threshold_keeps_one_task_per_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delta = root / "delta.csv"
            trip_ids = ["a", "b", "c", "d"]
            with delta.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=("trip_id", "stop_sequence"))
                writer.writeheader()
                for seq, trip_id in enumerate(trip_ids):
                    writer.writerow({"trip_id": trip_id, "stop_sequence": seq})

            counts, duplicates, tasks = partition_delta(delta, 2, root / "parts")

            self.assertEqual(sum(counts), 4)
            self.assertEqual(duplicates, 0)
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task["subpartition_id"] is None for task in tasks))

    def test_conflict_injection_can_be_combined_with_subpartitioning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delta = root / "delta.csv"
            buckets = {0: [], 1: []}
            candidate = 0
            while len(buckets[0]) < 9 or len(buckets[1]) < 1:
                trip_id = str(candidate)
                buckets[partition_for_trip_id(trip_id, 2)].append(trip_id)
                candidate += 1
            trip_ids = buckets[0][:9] + buckets[1][:1]
            with delta.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=("trip_id", "stop_sequence"))
                writer.writeheader()
                for seq, trip_id in enumerate(trip_ids):
                    writer.writerow({"trip_id": trip_id, "stop_sequence": seq})

            counts, duplicates, tasks = partition_delta(
                delta, 2, root / "parts", conflict_rate=0.25, rng_seed=42,
                subpartition_threshold=1.1, subpartition_factor=2)

            self.assertEqual(duplicates, 2)
            self.assertEqual(sum(counts), len(trip_ids) + duplicates)
            self.assertTrue(any(task["subpartition_id"] is not None for task in tasks))
            keys_by_partition = {0: set(), 1: set()}
            for task in tasks:
                with (task["data_dir"] / "keys.txt").open(encoding="utf-8") as f:
                    keys = f.read().splitlines()
                self.assertEqual(len(keys), len(set(keys)))
                keys_by_partition[task["partition_id"]].update(keys)
            self.assertTrue(keys_by_partition[0] & keys_by_partition[1])


if __name__ == "__main__":
    unittest.main()
