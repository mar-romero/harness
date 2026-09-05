import unittest
from calc import cpa

class CpaTests(unittest.TestCase):
    def test_normal(self): self.assertEqual(cpa(100,4),25)

if __name__=='__main__': unittest.main()
