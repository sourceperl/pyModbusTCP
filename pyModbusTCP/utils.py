# -*- coding: utf-8 -*-

# Python module: Some functions for modbus data mangling

##################################
# modbus to 32bits signed/unsigned
##################################

def wlist2long(val_list, big_endian=True):
    """Word list (16 bits int) to long list (32 bits int)

        By default wlist2long() use big endian order. For use little endian, set
        big_endian param to False.

        :param val_list: list of 16 bits int value
        :type val_list: list
        :param big_endian: True for big endian/False for little (optional)
        :type big_endian: bool
        :returns: 2's complement result
        :rtype: list
    """
    # allocate list for long int
    long_list = [None] * int(len(val_list)/2)
    # fill registers list with register items
    for i, item in enumerate(long_list):
        if big_endian:
            long_list[i] = (val_list[i*2]<<16) + val_list[(i*2)+1]
        else:
            long_list[i] = (val_list[(i*2)+1]<<16) + val_list[i*2]
    # return long list
    return long_list

###############################################
# 2's complement of int value (scalar and list)
###############################################

def int2comp(val_int, val_size=16):
    """Compute the 2's complement of val_int

        :param val_int: int value to apply 2's complement
        :type val_int: int
        :param val_size: bit size of int value (word = 16, long = 32) (optional)
        :type val_size: int
        :returns: 2's complement result
        :rtype: int
    """
    # test MSBit (1 for negative)
    if (val_int&(1<<(val_size-1))):
        # do complement
        val_int = val_int - (1<<val_size)
    return val_int

def list2comp(val_list, val_size=16):
    """Compute the 2's complement of val_list

        :param val_list: list of int value to apply 2's complement
        :type val_list: list
        :param val_size: bit size of int value (word = 16, long = 32) (optional)
        :type val_size: int
        :returns: 2's complement result
        :rtype: list
    """
    return [int2comp(val, val_size) for val in val_list]
