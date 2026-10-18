import unittest
from scripts.benchmark import ItemCounter, percentile


class BenchmarkTests(unittest.TestCase):
    def test_counts_items_not_classes_with_similar_names(self):
        parser = ItemCounter()
        parser.feed('<main><a class="filter-item x"></a><div class="filter-items"></div>'
                    '<article class="explorer-item"></article></main>')
        self.assertEqual(parser.items, 2)
        self.assertEqual(parser.elements, 4)

    def test_p95_uses_nearest_rank(self):
        self.assertEqual(percentile(list(range(1, 21))), 19)


if __name__ == '__main__':
    unittest.main()
