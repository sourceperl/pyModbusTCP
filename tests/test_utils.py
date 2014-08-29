import unittest
import math
from pyModbusTCP import utils

class TestUtils(unittest.TestCase):

    def test_decodeIEEE(self):
        # test IEEE NaN
        self.assertTrue(math.isnan(utils.decodeIEEE(0x7fffffff)))
        # test +/- infinity
        self.assertTrue(math.isinf(utils.decodeIEEE(0xff800000)))
        self.assertTrue(math.isinf(utils.decodeIEEE(0x7f800000)))
        # test some values
        self.assertAlmostEqual(utils.decodeIEEE(0x3e99999a), 0.3)
        self.assertAlmostEqual(utils.decodeIEEE(0xbe99999a), -0.3)

    def test_encodeIEEE(self):
        # test IEEE NaN
        self.assertEqual(utils.encodeIEEE(float('nan')), 2143289344)
        # test +/- infinity
        #self.assertTrue(math.isinf(utils.decodeIEEE(0xff800000)))
        #self.assertTrue(math.isinf(utils.decodeIEEE(0x7f800000)))
        # test some values
        self.assertAlmostEqual(utils.encodeIEEE(0.3), 0x3e99999a)
        self.assertAlmostEqual(utils.encodeIEEE(-0.3), 0xbe99999a)

    def test_wordList2long(self):
        # empty list, return empty list
        self.assertEqual(utils.wordList2long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(utils.wordList2long([0x1,0x2,0x3]), [0x10002])
        # test convert with big and little endian
        self.assertEqual(utils.wordList2long([0xdead, 0xbeef]), [0xdeadbeef])
        self.assertEqual(utils.wordList2long([0xdead, 0xbeef, 0xdead, 0xbeef]),
                         [0xdeadbeef, 0xdeadbeef])
        self.assertEqual(utils.wordList2long([0xdead, 0xbeef], 
                                             big_endian=False), [0xbeefdead])
        self.assertEqual(utils.wordList2long([0xdead, 0xbeef, 0xdead, 0xbeef],
                                          big_endian=False),
                         [0xbeefdead, 0xbeefdead])

    def test_get2comp(self):
        # 2's complement of 16bits 0x0001 value is 1
        self.assertEqual(utils.get2comp(0x0001, 16), 1)
        # 2's complement of 16bits 0x8000 value is -32768
        self.assertEqual(utils.get2comp(0x8000, 16), -32768)

    def test_getList2comp(self):
        # with 1 item
        self.assertEqual(utils.getList2comp([0x8000], 16), [-32768])
        # with 2 items
        self.assertEqual(utils.getList2comp([0x8000, 0x0042], 16), [-32768, 0x42])

