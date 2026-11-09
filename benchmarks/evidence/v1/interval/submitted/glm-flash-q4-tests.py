import unittest

from solution import merge_ranges


class TestMergeRanges(unittest.TestCase):
    def test_basic_merge(self):
        rows = [[1, 3], [2, 6], [8, 10], [15, 18]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[1, 6], [8, 10], [15, 18]])

    def test_adjacent_merge(self):
        rows = [[1, 4], [4, 5], [10, 12]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[1, 5], [10, 12]])

    def test_unsorted(self):
        rows = [[8, 10], [1, 3], [2, 6]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[1, 6], [8, 10]])

    def test_duplicates_and_containment(self):
        rows = [[1, 5], [1, 5], [2, 3], [3, 4], [4, 5]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[1, 5]])

    def test_one_point_intervals(self):
        rows = [[5, 5], [1, 1], [1, 3], [4, 4]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[1, 3], [4, 5]])

    def test_empty_input(self):
        rows = []
        result = merge_ranges(rows)
        self.assertEqual(result, [])

    def test_negative_numbers(self):
        rows = [[-5, -1], [-10, -6], [-3, 0]]
        result = merge_ranges(rows)
        self.assertEqual(result, [[-10, 0]])

    def test_invalid_float(self):
        rows = [[1.5, 3]]
        with self.assertRaises(ValueError):
            merge_ranges(rows)

    def test_invalid_boolean(self):
        rows = [[1, True]]
        with self.assertRaises(ValueError):
            merge_ranges(rows)

    def test_start_greater_than_end(self):
        rows = [[5, 1]]
        with self.assertRaises(ValueError):
            merge_ranges(rows)

    def test_malformed_pair_length(self):
        rows = [[1, 2, 3]]
        with self.assertRaises(ValueError):
            merge_ranges(rows)

    def test_non_list_outer_type(self):
        rows = {"start": 1, "end": 2}
        with self.assertRaises(ValueError):
            merge_ranges(rows)

    def test_empty_inner_list(self):
        rows = [[]]
        with self.assertRaises(ValueError):
            merge_ranges(rows)


if __name__ == '__main__':
    unittest.main()