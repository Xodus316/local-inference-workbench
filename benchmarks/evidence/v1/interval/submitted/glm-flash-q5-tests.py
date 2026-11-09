import unittest
from solution import merge_ranges

class TestMergeRanges(unittest.TestCase):

    def test_empty_input(self):
        self.assertEqual(merge_ranges([]), [])

    def test_single_range(self):
        self.assertEqual(merge_ranges([[1, 10]]), [[1, 10]])
        self.assertEqual(merge_ranges([[5, 5]]), [[5, 5]])

    def test_no_overlap_no_merge(self):
        # Non-adjacent and non-overlapping ranges
        self.assertEqual(merge_ranges([[1, 2], [3, 4]]), [[1, 2], [3, 4]])
        # Unsorted input that does not merge
        self.assertEqual(merge_ranges([[10, 15], [1, 5]]), [[1, 5], [10, 15]])

    def test_basic_overlap(self):
        self.assertEqual(merge_ranges([[1, 5], [3, 7]]), [[1, 7]])
        self.assertEqual(merge_ranges([[1, 5], [4, 6], [6, 7]]), [[1, 7]])
        # Containment
        self.assertEqual(merge_ranges([[1, 10], [2, 8], [3, 4]]), [[1, 10]])

    def test_adjacent_ranges(self):
        # Definition: merge if a_end == b_start - 1
        # [1, 5] and [6, 10] -> 5 == 6 - 1 (True) -> Merge
        self.assertEqual(merge_ranges([[1, 5], [6, 10]]), [[1, 10]])
        # [1, 4] and [5, 6] -> 4 == 5 - 1 (True) -> Merge
        self.assertEqual(merge_ranges([[1, 4], [5, 6]]), [[1, 6]])
        # Distant ranges do not merge despite being adjacent to non-overlapping sections
        self.assertEqual(merge_ranges([[1, 2], [3, 4], [5, 6]]), [[1, 6]])

    def test_negative_numbers_and_large_integers(self):
        self.assertEqual(merge_ranges([[-5, -1], [0, 10]]), [[-5, 10]])
        self.assertEqual(merge_ranges([[2**63 - 1, 2**63]]), [[2**63 - 1, 2**63]])

    def test_deduplication(self):
        # Duplicates should result in no change or implicit merging
        self.assertEqual(merge_ranges([[1, 5], [1, 5], [1, 5]]), [[1, 5]])
        self.assertEqual(merge_ranges([[1, 5], [1, 5]]), [[1, 5]])

    def test_immutability(self):
        original = [[1, 3], [2, 4]]
        original_copy = [[1, 3], [2, 4]]
        merge_ranges(original)
        self.assertEqual(original, original_copy)

    def test_invalid_outer_type(self):
        with self.assertRaises(ValueError):
            merge_ranges(None)
        with self.assertRaises(ValueError):
            merge_ranges("not a list")
        with self.assertRaises(ValueError):
            merge_ranges(123)

    def test_invalid_inner_type(self):
        with self.assertRaises(ValueError):
            merge_ranges([[1, 2, 3]])  # Inner list length != 2
        with self.assertRaises(ValueError):
            merge_ranges([1, 2])  # Outer element is not a list
        with self.assertRaises(ValueError):
            merge_ranges([["1", 2]])  # Inner element is not int
        with self.assertRaises(ValueError):
            merge_ranges([[True, 5]])  # Boolean is invalid
        with self.assertRaises(ValueError):
            merge_ranges([[1.5, 5]])  # Float is invalid

    def test_invalid_start_greater_than_end(self):
        with self.assertRaises(ValueError):
            merge_ranges([[5, 1]])
        with self.assertRaises(ValueError):
            merge_ranges([[1, 5], [10, 0]])

    def test_mixed_invalid_inputs(self):
        with self.assertRaises(ValueError):
            merge_ranges([[1, 2], "bad", [3, 4]])
        with self.assertRaises(ValueError):
            merge_ranges([[1, 5], [6, 4], None])
