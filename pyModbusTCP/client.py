# -*- coding: utf-8 -*-

# Python module: Client ModBus / TCP class 1
#       Version: 0.01
#       Website: https://github.com/sourceperl/pyModbusTCP
#          Date: 2014-08-04
#       License: MIT (http://http://opensource.org/licenses/mit-license.php)
#   Description: Client ModBus / TCP
#                Support functions 3 and 16 (class 0)
#                1,2,4,5,6 (Class 1)
#       Charset: us-ascii, unix end of line

# TODO
#   - update the code to deal with IPv6
#   - add Sphinx docstring
#   - add exceptions
#   - check Python3 support

from pyModbusTCP import const
import re
import socket
import select
import struct
import random

class ModbusClient:
    """Client Modbus TCP"""

    def __init__(self):
        """Constructor"""
        # public
        self.HOST          = "localhost"       #
        self.PORT          = const.MODBUS_PORT #
        self.UNIT_ID       = 1                 #
        self.MODE          = const.MODBUS_TCP  # by default modbus/tcp
        # private
        self.__sock        = None              # socket handle
        self.__timeout     = 30                # socket timeout
        self.__hd_tr_id    = 0                 # store transaction ID
        self.__debug       = False             # debug trace on/off
        self.__version     = const.VERSION     # version number
        self.__last_error  = const.MB_NO_ERR   # last error code
        self.__last_except = 0                 # last expect code

    def version(self):
        """
        Get current version number
        """
        return self.__version

    def last_error(self):
        """
        Get last error code
        """
        return self.__last_error

    def last_except(self):
        """
        Get last except code
        """
        return self.__last_except

    def host(self, hostname=None):
        """
        Get or set host field (IPv4 or hostname like 'plc.domain.net')
        """
        # return last hostname if no arg
        if hostname is None:
            return self.HOST
        # if host is IPv4 address or valid URL
        if (re.match("^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", hostname) or
           (re.match("^[a-z][a-z0-9\.\-]+$", hostname))):
            self.HOST = hostname
            return self.HOST
        else:
            return None

    def port(self, port=None):
        """Get or set TCP port field"""
        if port is None:
            return self.PORT
        if (0 < int(port) < 65536):
            self.PORT = int(port)
            return self.PORT
        else:
            return None

    def debug(self, debug=None):
        """
        Get or set debug mode
        """
        if debug is None:
            return self.__debug
        self.__debug = bool(debug)
        return self.__debug

    def unit_id(self, unit_id=None):
        """Get or set unit ID field"""
        if unit_id is None:
            return self.UNIT_ID
        if (0 <= int(unit_id) < 256):
            self.UNIT_ID = int(unit_id)
            return self.UNIT_ID
        else:
            return None

    def mode(self, mode=None):
        """Get or set modbus mode (TCP or RTU)"""
        if mode is None:
            return self.MODE
        if (mode == const.MODBUS_TCP or mode == const.MODBUS_RTU):
            self.MODE = mode
            return self.MODE
        else:
            return None

    def open(self):
        """Connect to modbus server"""
        self.__debug_msg("call open()")
        # restart TCP if already open
        if self.is_open():
            self.close()
        # init socket and connect 
        # list available sockets on the target host/port
        # set AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6, 
        #              AF_UNSPEC -> IPv6 (priority on some system) or 4
        # now just accept IPv4: fix this
        for res in socket.getaddrinfo(self.HOST, self.PORT, 
                                      socket.AF_INET, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            try:
                self.__sock = socket.socket(af, socktype, proto)
            except socket.error:
                self.__sock = None
                self.__last_error = const.MB_CONNECT_ERR
                self.__debug_msg("init socket error")
                continue
            try:
                self.__sock.connect(sa)
            except socket.error:
                self.__sock.close()
                self.__sock = None
                self.__last_error = const.MB_CONNECT_ERR
                self.__debug_msg("socket error")
                continue
            break
        return self.__sock is not None

    def is_open(self):
        return self.__sock is not None

    def close(self):
        if self.__sock:
            self.__sock.close()
            self.__sock = None
            return True
        else:
            return None

    def read_coils(self, bit_addr, bit_nb):
        """
         Modbus function READ_COILS (0x01).
           read_coils(bit_addr, bit_number)
           return a ref to result array
                  or undef if error
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg("read_coils() : bit_addr out of range")
            return None
        if not (1 <= int(bit_nb) <= 125):
            self.__debug_msg("read_coils() : bit_nb out of range")
            return None
        if (int(bit_addr) + int(bit_nb)) > 65536:
            self.__debug_msg("read_coils() : read after ad 65535")
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_COILS, 
                                     struct.pack(">HH", bit_addr, bit_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        rx_byte_count = struct.unpack("B", f_body[0])
        # frame with regs value
        f_bits = f_body[1:]
        bits = []
        for f_byte in f_bits:
            for pos in range(8):
                bits.append(bool(ord(f_byte)>>pos&0x01))
        return bits[:int(bit_nb)]

    def read_discrete_inputs(self, bit_addr, bit_nb):
        """
         Modbus function READ_DISCRETE_INPUTS (0x02).
           read_coils(bit_addr, bit_number)
           return a ref to result array
                  or undef if error
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg("read_discrete_inputs() : bit_addr out of range")
            return None
        if not (1 <= int(bit_nb) <= 125):
            self.__debug_msg("read_discrete_inputs() : bit_nb out of range")
            return None
        if (int(bit_addr) + int(bit_nb)) > 65536:
            self.__debug_msg("read_discrete_inputs() : read after ad 65535")
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_DISCRETE_INPUTS, 
                                     struct.pack(">HH", bit_addr, bit_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        rx_byte_count = struct.unpack("B", f_body[0])
        # frame with regs value
        f_bits = f_body[1:]
        bits = []
        for f_byte in f_bits:
            for pos in range(8):
                bits.append(bool(ord(f_byte)>>pos&0x01))
        return bits[:int(bit_nb)]

    def read_holding_registers(self, reg_addr, reg_nb):
        """
        Modbus function READ_HOLDING_REGISTERS (0x03).
          read_holding_registers(reg_addr, reg_number)
          return a ref to result array
                 or undef if error
        """
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg("read_holding_registers() : reg_addr out of range")
            return None
        if not (1 <= int(reg_nb) <= 125):
            self.__debug_msg("read_holding_registers() : reg_nb out of range")
            return None
        if (int(reg_addr) + int(reg_nb)) > 65536:
            self.__debug_msg("read_holding_registers() : read after ad 65535")
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_HOLDING_REGISTERS,
                                     struct.pack(">HH", reg_addr, reg_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        rx_reg_count = struct.unpack("B", f_body[0])
        # frame with regs value
        f_regs = f_body[1:]
        # split f_regs in 2 bytes blocs
        registers = [f_regs[i:i+2] for i in range(0, len(f_regs), 2)]
        registers = [struct.unpack(">H", i)[0] for i in registers]
        return registers

    def read_input_registers(self, reg_addr, reg_nb):
        """
        Modbus function READ_INPUT_REGISTERS (0x04).
          read_holding_registers(reg_addr, reg_number)
          return a ref to result array
                 or None if error
        """
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg("read_input_registers() : reg_addr out of range")
            return None
        if not (1 <= int(reg_nb) <= 125):
            self.__debug_msg("read_input_registers() : reg_nb out of range")
            return None
        if (int(reg_addr) + int(reg_nb)) > 65536:
            self.__debug_msg("read_input_registers() : read after ad 65535")
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_INPUT_REGISTERS,
                                     struct.pack(">HH", reg_addr, reg_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        rx_reg_count = struct.unpack("B", f_body[0])
        # frame with regs value
        f_regs = f_body[1:]
        # split f_regs in 2 bytes blocs
        registers = [f_regs[i:i+2] for i in range(0, len(f_regs), 2)]
        registers = [struct.unpack(">H", i)[0] for i in registers]
        return registers

    def write_single_coil(self, bit_addr, bit_value):
        """
        Modbus function WRITE_SINGLE_COIL (0x05).
          write_single_coil(bit_addr, bit_value)
          return 1 if write success
                   or None if error
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg("write_single_coil() : bit_addr out of range")
            return None
        # build frame
        bit_value = 0xFF if bit_value else 0x00
        tx_buffer = self._mbus_frame(const.WRITE_SINGLE_COIL,
                                       struct.pack(">HB", bit_addr, bit_value))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        (rx_bit_addr, rx_bit_value) = struct.unpack(">HB", f_body[:2])
        # check bit write
        is_ok = (rx_bit_addr == bit_addr) and (rx_bit_value == bit_value)
        return True if is_ok else None

    def write_single_register(self, reg_addr, reg_value):
        """
        Modbus function WRITE_SINGLE_REGISTER (0x06).
          write_single_register(reg_addr, reg_value)
          return True if write success
                      or None if error
        """
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg("write_single_register() : reg_addr out of range")
            return None
        if not (0 <= int(reg_value) <= 65535):
            self.__debug_msg("write_single_register() : reg_value out of range")
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.WRITE_SINGLE_REGISTER, 
                                     struct.pack(">HH", reg_addr, reg_value))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        rx_reg_addr, rx_reg_value = struct.unpack(">HH", f_body)
        # check register write
        is_ok = (rx_reg_addr == reg_addr) and (rx_reg_value == reg_value)
        return True if is_ok else None

    def write_multiple_registers(self, reg_addr, regs_value):
        """
        Modbus function WRITE_MULTIPLE_REGISTERS (0x10).
          write_multiple_registers(reg_addr, regs_value)
          return True if write success
                      or None if error
        """
        # number of registers to write
        regs_nb = len(regs_value)
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg("write_multiple_registers() : " +
                             "reg_addr out of range")
            return None
        if not (1 <= int(regs_nb) <= 125):
            self.__debug_msg("write_multiple_registers() : " +
                             "reg_nb out of range")
            return None
        if (int(reg_addr) + int(regs_nb)) > 65536:
            self.__debug_msg("write_multiple_registers() : " +
                             "write after ad 65535")
            return None
        # build frame
        # format reg value string
        regs_val_str = ""
        for reg in regs_value:
            # check current register value
            if not (0 <= int(reg) <= 65535):
                self.__debug_msg("write_multiple_registers() : " +
                                 "regs_value out of range")
                return None
            # pack register for build frame
            regs_val_str += struct.pack(">H", reg)
        bytes_nb = len(regs_val_str)
        # format modbus frame body
        body = struct.pack(">HHB", reg_addr, regs_nb, bytes_nb) + regs_val_str
        tx_buffer = self._mbus_frame(const.WRITE_MULTIPLE_REGISTERS, body)
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # register extract
        (rx_reg_addr, rx_reg_nb) = struct.unpack(">HH", f_body[:4])
        # check regs write
        is_ok = (rx_reg_addr == reg_addr)
        return True if is_ok else None

    def _can_read(self):
        """
        Wait data available for socket read
        """
        if self.__sock is None:
            return None
        if select.select([self.__sock], [], [], self.__timeout)[0]:
            return True
        else:
            self.__last_error = const.MB_TIMEOUT_ERR
            self.__debug_msg("timeout error")
            self.close()
            return None

    def _send(self, data):
        """ 
        Send data over current socket.
         _send(data_to_send)
         return the number of bytes send
                or None if error
        """
        # check link, open if need
        if self.__sock is None:
            return None
        # send data
        data_l = len(data)
        send_l = self.__sock.send(data)
        # send error
        if send_l != data_l:
            self.__last_error = const.MB_SEND_ERR
            self.__debug_msg("_send error")
            self.close()
            return None
        else:
            return send_l

    def _recv(self, max_size):
        """ Recv data over current socket.
           _recv(max_size)
           return the receive buffer
                  or None if error
        """
        # wait for read
        if not self._can_read():
            self.close()
            return None
        # recv
        r_buffer = self.__sock.recv(max_size)
        if not r_buffer:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg("_recv error")
            self.close()
            return None
        return r_buffer

    def _send_mbus(self, frame):
        """
        Send modbus frame.
          _send_mbus(frame)
          return nb_byte send
        """
        # send request
        bytes_send = self._send(frame)
        if bytes_send:
            if self.__debug:
                self._pretty_dump('Tx', frame)
            return bytes_send
        else:
            return None

    def _recv_mbus(self):
        """
        Recv modbus frame.
          _recv_mbus()
          return body (after func. code)
        """
        ## receive
        # modbus TCP receive
        if self.MODE == const.MODBUS_TCP:
            # 7 bytes header
            rx_buffer = self._recv(7)
            # check recv
            if not (rx_buffer and len(rx_buffer) == 7):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg("_recv MBAP error")
                self.close()
                return None
            rx_frame = rx_buffer
            # decode header
            (rx_hd_tr_id, rx_hd_pr_id,
             rx_hd_length, rx_hd_unit_id) = struct.unpack(">HHHB", rx_frame)
            # check header
            if not ((rx_hd_tr_id == self.__hd_tr_id) and
                   (rx_hd_pr_id == 0) and
                   (rx_hd_length < 256) and
                   (rx_hd_unit_id == self.UNIT_ID)):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg("MBAP format error")
                self.close()
                return None
            # end of frame
            rx_buffer = self._recv(rx_hd_length-1)
            if not (rx_buffer and (len(rx_buffer) == rx_hd_length-1)):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg("_recv frame body error")
                self.close()
                return None
            rx_frame += rx_buffer
            # dump frame
            if self.__debug:
                self._pretty_dump('Rx', rx_frame)
            # body decode
            rx_bd_fc = struct.unpack("B", rx_buffer[0])[0]
            f_body = rx_buffer[1:]
        # modbus RTU receive
        elif self.MODE == const.MODBUS_RTU:
            rx_buffer = self._recv(const.FRAME_RTU_MAXSIZE)
            if not rx_buffer:
                return None
            rx_frame = rx_buffer
            # dump frame
            if self.__debug:
                self._pretty_dump('Rx', rx_frame)
            # body decode
            (rx_unit_id, rx_bd_fc) = struct.unpack("BB", rx_frame[:2])
            f_body = rx_frame[2:]
            # check
            if not (rx_unit_id == self.UNIT_ID):
              self.close()
              return None
        # check except
        if rx_bd_fc > 0x80:
            # except code
            exp_code = struct.unpack("B", f_body[0])[0]
            self.__last_error  = const.MB_EXCEPT_ERR
            self.__last_except = exp_code
            self.__debug_msg("except (code"+str(exp_code)+")")
            return None
        else:
            # return
            return f_body


    def _mbus_frame(self, fc, body):
        """
         Build modbus frame.
           _mbus_frame(function code, body)
           return modbus frame
        """
        # build frame body
        f_body = struct.pack("B", fc) + body
        # modbus/TCP
        if self.MODE == const.MODBUS_TCP:
            # build frame ModBus Application Protocol header (mbap)
            self.__hd_tr_id  = random.randint(0,65535)
            tx_hd_pr_id      = 0
            tx_hd_length     = len(f_body) + 1
            f_mbap = struct.pack(">HHHB", self.__hd_tr_id, tx_hd_pr_id,
                                  tx_hd_length, self.UNIT_ID)
            return f_mbap + f_body
        # modbus RTU
        elif self.MODE == const.MODBUS_RTU:
            # format [slave addr(unit_id)]frame_body[CRC16]
            slave_ad = struct.pack("B", self.UNIT_ID)
            return self._add_crc(slave_ad + f_body)

    def _pretty_dump(self, label, data):
        """
        Print modbus/TCP frame ("[header]body") or RTU ("body[CRC]")
        """
        # split data string items to a list of hex value
        dump = ["%02X" % ord(c) for c in list(data)]
        # format for TCP or RTU
        if self.MODE == const.MODBUS_TCP:
            if len(dump) > 6:
                # "[MBAP] ..."
                dump[0] = "[" + dump[0]
                dump[6] = dump[6] + "]"
        elif self.MODE == const.MODBUS_RTU:
            if len(dump) > 4:
                # "... [CRC]"
                dump[-2] = "[" + dump[-2]
                dump[-1] = dump[-1] + "]"
        # print result
        print(label)
        s = ""
        for i in dump:
          s += i + " "
        print(s)

    def _crc(self, frame):
        """
        Compute modbus CRC16 (for RTU mode).
          _crc(modbus_frame)
          return the CRC
        """
        crc = 0xFFFF
        for index, item in enumerate(frame):
            next_byte = ord(item)
            crc ^= next_byte
            for i in range(8):
                lsb = crc & 1
                crc >>= 1
                if lsb:
                    crc ^= 0xA001
        return crc

    def _add_crc(self, frame):
        """
        Add CRC to modbus frame (for RTU mode).
          _add_crc(modbus_frame)
          return modbus_frame_with_crc
        """
        crc = struct.pack("<H", self._crc(frame))
        return frame+crc

    def _crc_is_ok(self, frame):
        """
        Check the CRC of modbus RTU frame.
          _crc_is_ok(modbus_frame_with_crc)
          return True if CRC is ok
        """
        return (self._crc(frame) == 0)

    def __debug_msg(self, msg):
        """
        Print debug message if debug mode is on
        """
        if self.__debug:
            print(msg)

