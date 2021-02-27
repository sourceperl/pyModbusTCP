# -*- coding: utf-8 -*-

import unittest
import math
from pyModbusTCP import utils


class TestUtils(unittest.TestCase):
    def test_get_bits_from_int(self):
        # default bits list size is 16
        self.assertEqual(len(utils.get_bits_from_int(0)), 16)
        # for 8 size (positional arg)
        self.assertEqual(len(utils.get_bits_from_int(0, 8)), 8)
        # for 32 size (named arg)
        self.assertEqual(len(utils.get_bits_from_int(0, val_size=32)), 32)
        # test binary decode
        self.assertEqual(utils.get_bits_from_int(6, 4),
                         [False, True, True, False])

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
        # self.assertTrue(math.isinf(utils.decode_ieee(0xff800000)))
        # self.assertTrue(math.isinf(utils.decode_ieee(0x7f800000)))
        # test some values
        self.assertAlmostEqual(utils.encode_ieee(0.3), 0x3e99999a)
        self.assertAlmostEqual(utils.encode_ieee(-0.3), 0xbe99999a)

    def test_word_list_to_long(self):
        # empty list, return empty list
        self.assertEqual(utils.word_list_to_long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(utils.word_list_to_long([0x1, 0x2, 0x3]), [0x10002])
        # test convert with big and little endian
        long_list = utils.word_list_to_long([0xdead, 0xbeef])
        self.assertEqual(long_list, [0xdeadbeef])
        long_list = utils.word_list_to_long([0xdead, 0xbeef, 0xdead, 0xbeef])
        self.assertEqual(long_list, [0xdeadbeef, 0xdeadbeef])
        long_list = utils.word_list_to_long([0xdead, 0xbeef], big_endian=False)
        self.assertEqual(long_list, [0xbeefdead])
        long_list = utils.word_list_to_long(
            [0xdead, 0xbeef, 0xdead, 0xbeef], big_endian=False)
        self.assertEqual(long_list, [0xbeefdead, 0xbeefdead])

    def test_long_list_to_word(self):
        # empty list, return empty list
        self.assertEqual(utils.long_list_to_word([]), [])
        # test convert with big and little endian
        word_list = utils.long_list_to_word([0xdeadbeef])
        self.assertEqual(word_list, [0xdead, 0xbeef])
        word_list = utils.long_list_to_word([0xdeadbeef, 0xdeadbeef])
        self.assertEqual(word_list, [0xdead, 0xbeef, 0xdead, 0xbeef])
        word_list = utils.long_list_to_word([0xdeadbeef], big_endian=False)
        self.assertEqual(word_list, [0xbeef, 0xdead])
        word_list = utils.long_list_to_word(
            [0xdeadbeef, 0xdeadbeef], big_endian=False)
        self.assertEqual(word_list, [0xbeef, 0xdead, 0xbeef, 0xdead])

    def test_get_2comp(self):
        # check if ValueError exception is raised
        self.assertRaises(ValueError, utils.get_2comp, 0x10000)
        self.assertRaises(ValueError, utils.get_2comp, -0x8001)
        self.assertRaises(ValueError, utils.get_2comp,
                          0x100000000, val_size=32)
        self.assertRaises(ValueError, utils.get_2comp, -
                          0x80000001, val_size=32)
        # 2's complement of 16bits values (default)
        self.assertEqual(utils.get_2comp(0x0001), 0x0001)
        self.assertEqual(utils.get_2comp(0x8000), -0x8000)
        self.assertEqual(utils.get_2comp(-0x8000), 0x8000)
        self.assertEqual(utils.get_2comp(0xffff), -0x0001)
        self.assertEqual(utils.get_2comp(-0x0001), 0xffff)
        self.assertEqual(utils.get_2comp(-0x00fa), 0xff06)
        self.assertEqual(utils.get_2comp(0xff06), -0x00fa)
        # 2's complement of 32bits values
        self.assertEqual(utils.get_2comp(0xfffffff, val_size=32), 0xfffffff)
        self.assertEqual(utils.get_2comp(-1, val_size=32), 0xffffffff)
        self.assertEqual(utils.get_2comp(0xffffffff, val_size=32), -1)
        self.assertEqual(utils.get_2comp(125, val_size=32), 0x0000007d)
        self.assertEqual(utils.get_2comp(0x0000007d, val_size=32), 125)
        self.assertEqual(utils.get_2comp(-250, val_size=32), 0xffffff06)
        self.assertEqual(utils.get_2comp(0xffffff06, val_size=32), -250)
        self.assertEqual(utils.get_2comp(0xfffea2a5, val_size=32), -89435)
        self.assertEqual(utils.get_2comp(-89435, val_size=32), 0xfffea2a5)

    def test_get_list_2comp(self):
        self.assertEqual(utils.get_list_2comp([0x8000], 16), [-32768])
        self.assertEqual(utils.get_list_2comp(
            [0x8000, 0xffff, 0x0042], val_size=16), [-0x8000, -0x0001, 0x42])
        self.assertEqual(utils.get_list_2comp(
            [0x8000, 0xffffffff, 0xfffea2a5], val_size=32), [0x8000, -0x0001, -89435])


if __name__ == '__main__':
    unittest.main()
