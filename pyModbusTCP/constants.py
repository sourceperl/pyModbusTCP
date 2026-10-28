""" pyModbusTCP package constants definition """

# Package version
VERSION = '0.4.0a4.dev0'
# Modbus/TCP
MODBUS_PORT = 502
# Modbus function code
READ_COILS = 0x01
READ_DISCRETE_INPUTS = 0x02
READ_HOLDING_REGISTERS = 0x03
READ_INPUT_REGISTERS = 0x04
WRITE_SINGLE_COIL = 0x05
WRITE_SINGLE_REGISTER = 0x06
WRITE_MULTIPLE_COILS = 0x0F
WRITE_MULTIPLE_REGISTERS = 0x10
WRITE_FILE_RECORD = 0x15
MASK_WRITE_REGISTER = 0x16
WRITE_READ_MULTIPLE_REGISTERS = 0x17
ENCAPSULATED_INTERFACE_TRANSPORT = 0x2B
# the functions implemented by the client and the server (not 0x15 and 0x16: only known for read_only)
SUPPORTED_FUNCTION_CODES = (READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS,
                            WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS,
                            WRITE_READ_MULTIPLE_REGISTERS, ENCAPSULATED_INTERFACE_TRANSPORT)
# the standard functions that write data (refused by ModbusServer(read_only=True), whatever their handler)
WRITE_FUNCTION_CODES = (WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS,
                        WRITE_FILE_RECORD, MASK_WRITE_REGISTER, WRITE_READ_MULTIPLE_REGISTERS)
# the functions that only read data (with the MEI type 0x0E for 0x2B): the only ones a read-only server relays to
# its fallback handler (an unknown function may write: diagnostics 0x08, a private function...)
READ_FUNCTION_CODES = (READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS,
                       ENCAPSULATED_INTERFACE_TRANSPORT)
# MEI type
MEI_TYPE_READ_DEVICE_ID = 0x0E
# Modbus except code
EXP_NONE = 0x00
EXP_ILLEGAL_FUNCTION = 0x01
EXP_DATA_ADDRESS = 0x02
EXP_DATA_VALUE = 0x03
EXP_SLAVE_DEVICE_FAILURE = 0x04
EXP_ACKNOWLEDGE = 0x05
EXP_SLAVE_DEVICE_BUSY = 0x06
EXP_NEGATIVE_ACKNOWLEDGE = 0x07
EXP_MEMORY_PARITY_ERROR = 0x08
EXP_GATEWAY_PATH_UNAVAILABLE = 0x0A
EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND = 0x0B
# Exception as short human-readable
EXP_TXT = {
    EXP_NONE: 'no exception',
    EXP_ILLEGAL_FUNCTION: 'illegal function',
    EXP_DATA_ADDRESS: 'illegal data address',
    EXP_DATA_VALUE: 'illegal data value',
    EXP_SLAVE_DEVICE_FAILURE: 'slave device failure',
    EXP_ACKNOWLEDGE: 'acknowledge',
    EXP_SLAVE_DEVICE_BUSY: 'slave device busy',
    EXP_NEGATIVE_ACKNOWLEDGE: 'negative acknowledge',
    EXP_MEMORY_PARITY_ERROR: 'memory parity error',
    EXP_GATEWAY_PATH_UNAVAILABLE: 'gateway path unavailable',
    EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND: 'gateway target device failed to respond'
}
# Exception as details human-readable
EXP_DETAILS = {
    EXP_NONE: 'The last request produced no exceptions.',
    EXP_ILLEGAL_FUNCTION: 'Function code received in the query is not recognized or allowed by slave.',
    EXP_DATA_ADDRESS: 'Data address of some or all the required entities are not allowed or do not exist in slave.',
    EXP_DATA_VALUE: 'Value is not accepted by slave.',
    EXP_SLAVE_DEVICE_FAILURE: 'Unrecoverable error occurred while slave was attempting to perform requested action.',
    EXP_ACKNOWLEDGE: 'Slave has accepted request and is processing it, but a long duration of time is required. '
                     'This response is returned to prevent a timeout error from occurring in the master. '
                     'Master can next issue a Poll Program Complete message to determine whether processing '
                     'is completed.',
    EXP_SLAVE_DEVICE_BUSY: 'Slave is engaged in processing a long-duration command. Master should retry later.',
    EXP_NEGATIVE_ACKNOWLEDGE: 'Slave cannot perform the programming functions. '
                              'Master should request diagnostic or error information from slave.',
    EXP_MEMORY_PARITY_ERROR: 'Slave detected a parity error in memory. '
                             'Master can retry the request, but service may be required on the slave device.',
    EXP_GATEWAY_PATH_UNAVAILABLE: 'Specialized for Modbus gateways, this indicates a misconfiguration on gateway.',
    EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND: 'Specialized for Modbus gateways, sent when slave fails to respond.'
}
# Module error codes (ModbusClient.last_error); MB_RESOLVE_ERR and MB_CRC_ERR are never set by ModbusClient (a name
# resolution error is MB_CONNECT_ERR, Modbus/TCP has no CRC): kept for the code written for 0.3.x
MB_NO_ERR = 0
MB_RESOLVE_ERR = 1
MB_CONNECT_ERR = 2
MB_SEND_ERR = 3
MB_RECV_ERR = 4
MB_TIMEOUT_ERR = 5
MB_FRAME_ERR = 6
MB_EXCEPT_ERR = 7
MB_CRC_ERR = 8
MB_SOCK_CLOSE_ERR = 9
# Module error as short human-readable
MB_ERR_TXT = {
    MB_NO_ERR: 'no error',
    MB_RESOLVE_ERR: 'name resolve error',
    MB_CONNECT_ERR: 'connect error',
    MB_SEND_ERR: 'socket send error',
    MB_RECV_ERR: 'socket recv error',
    MB_TIMEOUT_ERR: 'recv timeout occur',
    MB_FRAME_ERR: 'frame format error',
    MB_EXCEPT_ERR: 'modbus exception',
    MB_CRC_ERR: 'bad CRC on receive frame',
    MB_SOCK_CLOSE_ERR: 'socket is closed'
}
# Misc
MAX_PDU_SIZE = 253
MIN_PDU_SIZE = 1
MBAP_SIZE = 7
MBAP_MAX_LENGTH = MAX_PDU_SIZE + 1
MBAP_MIN_LENGTH = MIN_PDU_SIZE + 1
