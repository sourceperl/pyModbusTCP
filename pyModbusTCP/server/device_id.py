""" pyModbusTCP server: DeviceIdentification, objects returned by the read device identification function. """

from __future__ import annotations

from threading import Lock
from typing import Dict, List, Optional, Tuple

from ..constants import MAX_PDU_SIZE


class DeviceIdentification:
    """ Container class for device identification objects (MEI type 0x0E) return by function 0x2B. """

    # max size of an object value: it must fit in a response PDU (7 bytes header + 2 bytes object id and length)
    MAX_OBJECT_SIZE = MAX_PDU_SIZE - 9

    def __init__(self, vendor_name: bytes = b'', product_code: bytes = b'', major_minor_revision: bytes = b'',
                 vendor_url: bytes = b'', product_name: bytes = b'', model_name: bytes = b'',
                 user_application_name: bytes = b'', objects_id: Optional[Dict[int, bytes]] = None) -> None:
        """
        Constructor

        :param vendor_name: VendorName mandatory object
        :type vendor_name: bytes
        :param product_code: ProductCode mandatory object
        :type product_code: bytes
        :param major_minor_revision: MajorMinorRevision mandatory object
        :type major_minor_revision: bytes
        :param vendor_url: VendorUrl regular object
        :type vendor_url: bytes
        :param product_name: ProductName regular object
        :type product_name: bytes
        :param model_name: ModelName regular object
        :type model_name: bytes
        :param user_application_name: UserApplicationName regular object
        :type user_application_name: bytes
        :param objects_id: Objects values by id as dict example: {42:b'value'} (optional)
        :type objects_id: dict
        """
        # private
        self._objs_d: Dict[int, bytes] = {}
        self._objs_lock = Lock()
        # default values
        self.vendor_name = vendor_name
        self.product_code = product_code
        self.major_minor_revision = major_minor_revision
        self.vendor_url = vendor_url
        self.product_name = product_name
        self.model_name = model_name
        self.user_application_name = user_application_name
        # process objects_id dict (populate no name objects)
        if isinstance(objects_id, dict):
            for key, value in objects_id.items():
                self[key] = value

    def __getitem__(self, key: int) -> bytes:
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        with self._objs_lock:
            return self._objs_d[key]

    def __setitem__(self, key: int, value: bytes) -> None:
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        if 0xff >= key >= 0x00:
            if not isinstance(value, bytes):
                raise TypeError('this object is of type bytes only')
            if len(value) > self.MAX_OBJECT_SIZE:
                raise ValueError('object value is too long (max %d bytes)' % self.MAX_OBJECT_SIZE)
            with self._objs_lock:
                self._objs_d[key] = value
        else:
            raise ValueError('key not in valid range (0 to 255)')

    def __contains__(self, key: object) -> bool:
        # without it, "in" would iterate with __getitem__(0, 1, 2...) until a KeyError escapes
        with self._objs_lock:
            return key in self._objs_d

    def __repr__(self) -> str:
        named_params = ''
        # add named parameters
        for prop_name in ('vendor_name', 'product_code', 'major_minor_revision', 'vendor_url',
                          'product_name', 'model_name', 'user_application_name'):
            prop_value = getattr(self, prop_name)
            if prop_value:
                if named_params:
                    named_params += ', '
                named_params += '%s=%r' % (prop_name, getattr(self, prop_name))
        # add parameters without shortcut name
        objs_id_d_str = ''
        for _id in range(0x07, 0x100):
            try:
                obj_id_item = '%r: %r' % (_id, self[_id])
                if objs_id_d_str:
                    objs_id_d_str += ', '
                objs_id_d_str += obj_id_item
            except KeyError:
                pass
        # format str: classname(params_name=value, ..., objects_id={42: 'value'})
        class_args = named_params
        if objs_id_d_str:
            if class_args:
                class_args += ', '
            class_args += 'objects_id={%s}' % objs_id_d_str
        return '%s(%s)' % (self.__class__.__name__, class_args)

    @property
    def vendor_name(self) -> bytes:
        return self[0]

    @vendor_name.setter
    def vendor_name(self, value: bytes) -> None:
        self[0] = value

    @property
    def product_code(self) -> bytes:
        return self[1]

    @product_code.setter
    def product_code(self, value: bytes) -> None:
        self[1] = value

    @property
    def major_minor_revision(self) -> bytes:
        return self[2]

    @major_minor_revision.setter
    def major_minor_revision(self, value: bytes) -> None:
        self[2] = value

    @property
    def vendor_url(self) -> bytes:
        return self[3]

    @vendor_url.setter
    def vendor_url(self, value: bytes) -> None:
        self[3] = value

    @property
    def product_name(self) -> bytes:
        return self[4]

    @product_name.setter
    def product_name(self, value: bytes) -> None:
        self[4] = value

    @property
    def model_name(self) -> bytes:
        return self[5]

    @model_name.setter
    def model_name(self, value: bytes) -> None:
        self[5] = value

    @property
    def user_application_name(self) -> bytes:
        return self[6]

    @user_application_name.setter
    def user_application_name(self, value: bytes) -> None:
        self[6] = value

    def items(self, start: int = 0x00, end: int = 0xff) -> List[Tuple[int, bytes]]:
        items_l = []
        for obj_id in range(start, end + 1):
            try:
                items_l.append((obj_id, self[obj_id]))
            except KeyError:
                pass
        return items_l
