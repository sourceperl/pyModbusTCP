""" Test of pyModbusTCP.utils """

import unittest
import math
from pyModbusTCP.utils import \
    get_bits_from_int, int2bits, decode_ieee, encode_ieee, \
    word_list_to_long, words2longs, long_list_to_word, longs2words, \
    get_2comp, twos_c, get_list_2comp, twos_c_l


class TestUtils(unittest.TestCase):
    """ pyModbusTCP.utils function test class. """

    def test_get_bits_from_int(self):
        """Test function get_bits_from_int and it's short alias int2bits."""
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

    def test_ieee(self):
        """Test IEEE functions: decode_ieee and encode_ieee."""
        # test IEEE NaN
        self.assertTrue(math.isnan(decode_ieee(0x7fc00000)))
        self.assertEqual(encode_ieee(float('nan')), 0x7fc00000)
        # test +/- infinity
        self.assertTrue(math.isinf(decode_ieee(0xff800000)))
        self.assertTrue(math.isinf(decode_ieee(0x7f800000)))
        # test big and small values
        avogad = 6.022140857e+23
        avo_32 = 0x66ff0c2f
        avo_64 = 0x44dfe185d2f54b67
        planck = 6.62606957e-34
        pla_32 = 0x085c305e
        pla_64 = 0x390b860bb596a559
        # IEEE single or double precision format -> float
        self.assertAlmostEqual(decode_ieee(avo_32), avogad, delta=avogad*1e-7)
        self.assertAlmostEqual(decode_ieee(avo_64, double=True), avogad)
        self.assertAlmostEqual(decode_ieee(pla_32), planck)
        self.assertAlmostEqual(decode_ieee(pla_64, double=True), planck)
        # float -> IEEE single or double precision format
        self.assertAlmostEqual(encode_ieee(avogad), avo_32)
        self.assertAlmostEqual(encode_ieee(avogad, double=True), avo_64)
        self.assertAlmostEqual(encode_ieee(planck), pla_32)
        self.assertAlmostEqual(encode_ieee(planck, double=True), pla_64)

    def test_word_list_to_long(self):
        """Test function word_list_to_long and it 's short alias words2longs."""
        # empty list, return empty list
        self.assertEqual(word_list_to_long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(word_list_to_long([0x1, 0x2, 0x3]), [0x10002])
        # test convert with big and little endian
        l1 = [0xdead, 0xbeef]
        l2 = [0xfeed, 0xface, 0xcafe, 0xbeef]
        big = dict(big_endian=True)
        nobig = dict(big_endian=False)
        big64 = dict(big_endian=True, long_long=True)
        nobig64 = dict(big_endian=False, long_long=True)
        self.assertEqual(words2longs(l1, **big), [0xdeadbeef])
        self.assertEqual(words2longs(l2, **big), [0xfeedface, 0xcafebeef])
        self.assertEqual(words2longs(l1, **nobig), [0xbeefdead])
        self.assertEqual(words2longs(l2, **nobig), [0xfacefeed, 0xbeefcafe])
        self.assertEqual(words2longs(l1*2, **big64), [0xdeadbeefdeadbeef])
        self.assertEqual(words2longs(l2*2, **big64), [0xfeedfacecafebeef]*2)
        self.assertEqual(words2longs(l1*2, **nobig64), [0xbeefdeadbeefdead])
        self.assertEqual(words2longs(l2*2, **nobig64), [0xbeefcafefacefeed]*2)

    def test_long_list_to_word(self):
        """Test function long_list_to_word and short alias longs2words."""
        # empty list, return empty list
        self.assertEqual(long_list_to_word([]), [])
        # test convert with big and little endian
        l1 = [0xdeadbeef]
        l1_big = [0xdead, 0xbeef]
        l1_nobig = [0xbeef, 0xdead]
        l1_big64 = [0x0000, 0x0000, 0xdead, 0xbeef]
        l1_nobig64 = [0xbeef, 0xdead, 0x0000, 0x0000]
        l2 = [0xfeedface, 0xcafebeef]
        l2_big = [0xfeed, 0xface, 0xcafe, 0xbeef]
        l2_nobig = [0xface, 0xfeed, 0xbeef, 0xcafe]
        l3 = [0xfeedfacecafebeef]
        l3_big64 = [0xfeed, 0xface, 0xcafe, 0xbeef]
        l3_nobig64 = [0xbeef, 0xcafe, 0xface, 0xfeed]
        big = dict(big_endian=True)
        nobig = dict(big_endian=False)
        big64 = dict(big_endian=True, long_long=True)
        nobig64 = dict(big_endian=False, long_long=True)
        self.assertEqual(longs2words(l1, **big), l1_big)
        self.assertEqual(longs2words(l2, **big), l2_big)
        self.assertEqual(longs2words(l1, **nobig), l1_nobig)
        self.assertEqual(longs2words(l2, **nobig), l2_nobig)
        self.assertEqual(longs2words(l1*2, **big64), l1_big64*2)
        self.assertEqual(longs2words(l3*2, **big64), l3_big64*2)
        self.assertEqual(longs2words(l1*4, **nobig64), l1_nobig64*4)
        self.assertEqual(longs2words(l3*4, **nobig64), l3_nobig64*4)

    def test_get_2comp(self):
        """Test function get_2comp and it's short alias twos_c."""
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
        """Test get_list_2comp and it's short alias twos_c_l."""
        self.assertEqual(get_list_2comp([0x8000], 16), [-32768])
        in_l = [0x8000, 0xffff, 0x0042]
        out_l = [-0x8000, -0x0001, 0x42]
        self.assertEqual(twos_c_l(in_l, val_size=16), out_l)
        in_l = [0x8000, 0xffffffff, 0xfffea2a5]
        out_l = [0x8000, -0x0001, -89435]
        self.assertEqual(twos_c_l(in_l, val_size=32), out_l)


if __name__ == '__main__':
    unittest.main()
