# -*- coding: utf-8 -*-

import unittest
import math
from pyModbusTCP.utils import \
    get_bits_from_int, int2bits, decode_ieee, encode_ieee, \
    word_list_to_long, words2longs, long_list_to_word, longs2words, \
    get_2comp, twos_c, get_list_2comp, twos_c_l


class TestUtils(unittest.TestCase):
    def test_get_bits_from_int(self):
        # test get_bits_from_int() and short alias int2bits()
        # default bits list size is 16
        self.assertEqual(len(get_bits_from_int(0)), 16)
        # for 8 size (positional arg)
        self.assertEqual(len(get_bits_from_int(0, 8)), 8)
        # for 32 size (named arg)
        self.assertEqual(len(get_bits_from_int(0, val_size=32)), 32)
        # test binary decode
        self.assertEqual(int2bits(0x0000), [False]*16)
        self.assertEqual(int2bits(0xffff), [True]*16)
        self.assertEqual(int2bits(0xf007), [True]*3 + [False]*9 + [True]*4)
        self.assertEqual(int2bits(6, 4), [False, True, True, False])

    def test_decode_ieee(self):
        # test IEEE NaN
        self.assertTrue(math.isnan(decode_ieee(0x7fffffff)))
        # test +/- infinity
        self.assertTrue(math.isinf(decode_ieee(0xff800000)))
        self.assertTrue(math.isinf(decode_ieee(0x7f800000)))
        # test some values
        self.assertAlmostEqual(decode_ieee(0x3e99999a), 0.3)
        self.assertAlmostEqual(decode_ieee(0xbe99999a), -0.3)

    def test_encode_ieee(self):
        # test IEEE NaN
        self.assertEqual(encode_ieee(float('nan')), 2143289344)
        # test some values
        self.assertAlmostEqual(encode_ieee(0.3), 0x3e99999a)
        self.assertAlmostEqual(encode_ieee(-0.3), 0xbe99999a)

    def test_word_list_to_long(self):
        # test word_list_to_long() and short alias words2longs()
        # empty list, return empty list
        self.assertEqual(word_list_to_long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(word_list_to_long([0x1, 0x2, 0x3]), [0x10002])
        # test convert with big and little endian
        dead_l = [0xdead, 0xbeef]
        big = dict(big_endian=True)
        nobig = dict(big_endian=False)
        self.assertEqual(words2longs(dead_l, **big), [0xdeadbeef])
        self.assertEqual(words2longs(dead_l*2, **big), [0xdeadbeef]*2)
        self.assertEqual(words2longs(dead_l, **nobig), [0xbeefdead])
        self.assertEqual(words2longs(dead_l*2, **nobig), [0xbeefdead]*2)

    def test_long_list_to_word(self):
        # test long_list_to_word() and short alias longs2words()
        # empty list, return empty list
        self.assertEqual(long_list_to_word([]), [])
        # test convert with big and little endian
        dead_l = [0xdeadbeef]
        big = dict(big_endian=True)
        nobig = dict(big_endian=False)
        self.assertEqual(longs2words(dead_l, **big), [0xdead, 0xbeef])
        self.assertEqual(longs2words(dead_l*2, **big), [0xdead, 0xbeef]*2)
        self.assertEqual(longs2words(dead_l, **nobig), [0xbeef, 0xdead])
        self.assertEqual(longs2words(dead_l*2, **nobig), [0xbeef, 0xdead]*2)

    def test_get_2comp(self):
        # test get_2comp() and short alias twos_c()
        # check if ValueError exception is raised
        self.assertRaises(ValueError, get_2comp, 0x10000)
        self.assertRaises(ValueError, get_2comp, -0x8001)
        self.assertRaises(ValueError, twos_c, 0x100000000, val_size=32)
        self.assertRaises(ValueError, twos_c, -0x80000001, val_size=32)
        # 2's complement of 16bits values (default)
        self.assertEqual(get_2comp(0x0001), 0x0001)
        self.assertEqual(get_2comp(0x8000), -0x8000)
        self.assertEqual(get_2comp(-0x8000), 0x8000)
        self.assertEqual(get_2comp(0xffff), -0x0001)
        self.assertEqual(get_2comp(-0x0001), 0xffff)
        self.assertEqual(get_2comp(-0x00fa), 0xff06)
        self.assertEqual(get_2comp(0xff06), -0x00fa)
        # 2's complement of 32bits values
        self.assertEqual(twos_c(0xfffffff, val_size=32), 0xfffffff)
        self.assertEqual(twos_c(-1, val_size=32), 0xffffffff)
        self.assertEqual(twos_c(0xffffffff, val_size=32), -1)
        self.assertEqual(twos_c(125, val_size=32), 0x0000007d)
        self.assertEqual(twos_c(0x0000007d, val_size=32), 125)
        self.assertEqual(twos_c(-250, val_size=32), 0xffffff06)
        self.assertEqual(twos_c(0xffffff06, val_size=32), -250)
        self.assertEqual(twos_c(0xfffea2a5, val_size=32), -89435)
        self.assertEqual(twos_c(-89435, val_size=32), 0xfffea2a5)

    def test_get_list_2comp(self):
        # test get_list_2comp() and short alias twos_c_l()
        self.assertEqual(get_list_2comp([0x8000], 16), [-32768])
        in_l = [0x8000, 0xffff, 0x0042]
        out_l = [-0x8000, -0x0001, 0x42]
        self.assertEqual(twos_c_l(in_l, val_size=16), out_l)
        in_l = [0x8000, 0xffffffff, 0xfffea2a5]
        out_l = [0x8000, -0x0001, -89435]
        self.assertEqual(twos_c_l(in_l, val_size=32), out_l)


if __name__ == '__main__':
    unittest.main()
