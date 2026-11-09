import unittest

from solution import merge_ranges


class TestMergeRanges(unittest.TestCase):

    def test_empty_list(self):
        self.assertEqual(merge_ranges([]), [])

    def test_single_point_interval(self):
        self.assertEqual(merge_ranges([[5, 5]]), [[5, 5]])

    def test_unsorted_intervals(self):
        self.assertEqual(merge_ranges([[5, 8], [1, 3], [2, 6]]), [[1, 8]])

    def test_duplicates(self):
        self.assertEqual(merge_ranges([[1, 3], [1, 3], [3, 5]]), [[1, 5]])

    def test_containment(self):
        self.assertEqual(merge_ranges([[1, 10], [2, 5], [3, 4]]), [[1, 10]])

    def test_overlap_merging(self):
        self.assertEqual(merge_ranges([[1, 5], [3, 7]]), [[1, 7]])

    def test_adjacent_merging(self):
        # end=3, start=4 -> 3+1=4, adjacent, merge to [1,6]
        self.assertEqual(merge_ranges([[1, 3], [4, 6]]), [[1, 6]])

    def test_adjacent_with_negative(self):
        # end=-1, start=0 -> adjacent, merge
        self.assertEqual(merge_ranges([[-3, -1], [0, 2]]), [[-3, 2]])

    def test_output_sorted_and_merged(self):
        self.assertEqual(merge_ranges([[5, 10], [1, 3]]), [[1, 3], [5, 10]])

    def test_boolean_rejection(self):
        # Booleans are invalid despite being subclass of int in Python
        with self.assertRaises(ValueError):
            merge_ranges([[True, 5]])
        with self.assertRaises(ValueError):
            merge_ranges([[1, True]])
        with self.assertRaises(ValueError):
            merge_ranges([[True, False]])

    def test_float_rejection(self):
        with self.assertRaises(ValueError):
            merge_ranges([[1.5, 5]])
        with self.assertRaises(ValueError):
            merge_ranges([[1, 5.5]])

    def test_start_gt_end(self):
        with self.assertRaises(ValueError):
            merge_ranges([[5, 3]])

    def test_wrong_inner_type(self):
        with self.assertRaises(ValueError):
            merge_ranges([1, 2])

    def test_wrong_inner_length(self):
        with self.assertRaises(ValueError):
            merge_ranges([[1, 2, 3]])

    def test_input_not_mutated(self):
        input_ranges = [[1, 3], [5, 8]]
        original = [list(pair) for pair in input_ranges]
        merge_ranges(input_ranges)
        self.assertEqual(input_ranges, original)

    def test_input_not_mutated_on_error(self):
        input_ranges = [[5, 3]]  # invalid: start > end
        original = [list(pair) for pair in input_ranges]
        with self.assertRaises(ValueError):
            merge_ranges(input_ranges)
        self.assertEqual(input_ranges, original)

    def test_adjacent_and_overlap_mixed(self):
        # [1,4], [5,8] adjacent -> [1,8]; [2,3] contained in [1,4]
        self.assertEqual(merge_ranges([[1, 4], [5, 8], [2, 3]]), [[1, 8]])

    def test_single_element_result(self):
        self.assertEqual(merge_ranges([[42, 42]]), [[42, 42]])


if __name__ == '__main__':
    unittest.main()
