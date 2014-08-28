import unittest
from pyModbusTCP import utils

class TestUtils(unittest.TestCase):

    def test_wlist2long(self):
        # empty list, return empty list
        self.assertEqual(utils.wlist2long([]), [])
        # if len of list is odd ignore last value
        self.assertEqual(utils.wlist2long([0x1,0x2,0x3]), [0x10002])
        # test convert with big and little endian
        self.assertEqual(utils.wlist2long([0xdead, 0xbeef]), [0xdeadbeef])
        self.assertEqual(utils.wlist2long([0xdead, 0xbeef, 0xdead, 0xbeef]),
                         [0xdeadbeef, 0xdeadbeef])
        self.assertEqual(utils.wlist2long([0xdead, 0xbeef], big_endian=False),
                         [0xbeefdead])
        self.assertEqual(utils.wlist2long([0xdead, 0xbeef, 0xdead, 0xbeef],
                                          big_endian=False),
                         [0xbeefdead, 0xbeefdead])

    def test_int2comp(self):
        # 2's complement of 16bits 0x0001 value is 1
        self.assertEqual(utils.int2comp(0x0001, 16), 1)
        # 2's complement of 16bits 0x8000 value is -32768
        self.assertEqual(utils.int2comp(0x8000, 16), -32768)

    def test_list2comp(self):
        # with 1 item
        self.assertEqual(utils.list2comp([0x8000], 16), [-32768])
        # with 2 items
        self.assertEqual(utils.list2comp([0x8000, 0x0042], 16), [-32768, 0x42])

