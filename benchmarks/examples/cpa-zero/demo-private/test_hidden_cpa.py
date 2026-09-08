import unittest
from calc import cpa

class HiddenCpaTests(unittest.TestCase):
    def test_zero(self): self.assertIsNone(cpa(100,0))

if __name__=='__main__': unittest.main()
