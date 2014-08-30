import unittest
import math
from pyModbusTCP import utils

class TestUtils(unittest.TestCase):

    def test_decode_ieee(self):
        # test IEEE NaN
        self.assertTrue(math.isnan(utils.decode_ieee(0x7fffffff)))
        # test +/- infinity
        self.assertTrue(math.isinf(utils.decode_ieee(0xff800000)))
        self.assertTrue(math.isinf(utils.decode_ieee(0x7f800000)))
        # test some values
        self.assertAlmostEqual(utils.decode_ieee(0x3e99999a), 0.3)
        self.assertAlmostEqual(utils.decode_ieee(0xbe99999a), -0.3)

    def test_encode_ieee(self):
        # test IEEE NaN
        self.assertEqual(utils.encode_ieee(float('nan')), 2143289344)
        # test +/- infinity
        #self.assertTrue(math.isinf(utils.decode_ieee(0xff800000)))
        #self.assertTrue(math.isinf(utils.decode_ieee(0x7f800000)))
        # test some values
        self.assertAlmostEqual(utils.encode_ieee(0.3), 0x3e99999a)
        self.assertAlmostEqual(utils.encode_ieee(-0.3), 0xbe99999a)

    def test_word_list_to_long(self):
        # empty list, return empty list
        self.assertEqual(utils.word_list_to_long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(utils.word_list_to_long([0x1,0x2,0x3]), [0x10002])
        # test convert with big and little endian
        word_list = utils.word_list_to_long([0xdead, 0xbeef])
        self.assertEqual(word_list, [0xdeadbeef])
        word_list = utils.word_list_to_long([0xdead, 0xbeef, 0xdead, 0xbeef])
        self.assertEqual(word_list, [0xdeadbeef, 0xdeadbeef])
        word_list = utils.word_list_to_long([0xdead, 0xbeef], big_endian=False)
        self.assertEqual(word_list, [0xbeefdead])
        word_list = utils.word_list_to_long([0xdead, 0xbeef, 0xdead, 0xbeef],
                                            big_endian=False)
        self.assertEqual(word_list, [0xbeefdead, 0xbeefdead])

    def test_get_2comp(self):
        # 2's complement of 16bits 0x0001 value is 1
        self.assertEqual(utils.get_2comp(0x0001, 16), 1)
        # 2's complement of 16bits 0x8000 value is -32768
        self.assertEqual(utils.get_2comp(0x8000, 16), -0x8000)
        # 2's complement of 16bits 0xFFFF value is -1
        self.assertEqual(utils.get_2comp(0xFFFF, 16), -0x0001)

    def test_get_list_2comp(self):
        # with 1 item
        self.assertEqual(utils.get_list_2comp([0x8000], 16), [-32768])
        # with 3 items
        self.assertEqual(utils.get_list_2comp([0x8000, 0xFFFF, 0x0042], 16), 
                         [-0x8000, -0x0001, 0x42])

