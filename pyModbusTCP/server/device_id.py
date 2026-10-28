""" pyModbusTCP server: DeviceIdentification, objects returned by the read device identification function. """

from __future__ import annotations

import warnings
from threading import Lock

from ..constants import MAX_PDU_SIZE


class DeviceIdentification:
    """ Container class for device identification objects (MEI type 0x0E) return by function 0x2B. """

    # max size of an object value: it must fit in a response PDU (7 bytes header + 2 bytes object id and length)
    MAX_OBJECT_SIZE = MAX_PDU_SIZE - 9

    def __init__(self, vendor_name: bytes = b'', product_code: bytes = b'', major_minor_revision: bytes = b'',
                 vendor_url: bytes = b'', product_name: bytes = b'', model_name: bytes = b'',
                 user_application_name: bytes = b'', objects_by_id: dict[int, bytes] | None = None,
                 objects_id: dict[int, bytes] | None = None) -> None:
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
        :param objects_by_id: Objects values by id as dict example: {42:b'value'} (optional), the name of the client\
            side (DeviceIdentificationResponse.objects_by_id)
        :type objects_by_id: dict
        :param objects_id: deprecated name of objects_by_id (DeprecationWarning)
        :type objects_id: dict
        """
        # private
        self._objs_d: dict[int, bytes] = {}
        self._objs_lock = Lock()
        # default values
        self.vendor_name = vendor_name
        self.product_code = product_code
        self.major_minor_revision = major_minor_revision
        self.vendor_url = vendor_url
        self.product_name = product_name
        self.model_name = model_name
        self.user_application_name = user_application_name
        if objects_id is not None:
            if objects_by_id is not None:
                raise TypeError('objects_id is the deprecated name of objects_by_id: give only objects_by_id')
            warnings.warn('DeviceIdentification(objects_id=) is deprecated, use objects_by_id=',
                          DeprecationWarning, stacklevel=2)
            objects_by_id = objects_id
        # process objects_by_id dict (populate no name objects)
        if isinstance(objects_by_id, dict):
            for key, value in objects_by_id.items():
                self[key] = value

    def __getitem__(self, key: int) -> bytes:
        # an object id is an int, never a bool (True would be the object 1)
        if isinstance(key, bool) or not isinstance(key, int):
            raise TypeError('key must be an int')
        with self._objs_lock:
            return self._objs_d[key]

    def __setitem__(self, key: int, value: bytes) -> None:
        if isinstance(key, bool) or not isinstance(key, int):
            raise TypeError('key must be an int')
        if 0xff >= key >= 0x00:
            if not isinstance(value, bytes):
                raise TypeError('this object is of type bytes only')
            if len(value) > self.MAX_OBJECT_SIZE:
                raise ValueError(f'object value is too long (max {self.MAX_OBJECT_SIZE} bytes)')
            with self._objs_lock:
                self._objs_d[key] = value
        else:
            raise ValueError('key not in valid range (0 to 255)')

    def __contains__(self, key: object) -> bool:
        # without it, "in" would iterate with __getitem__(0, 1, 2...) until a KeyError escapes
        if isinstance(key, bool):
            return False
        with self._objs_lock:
            return key in self._objs_d

    @property
    def conformity_level(self) -> int:
        """The conformity level of the read device identification response: the categories of the published objects.

        0x81 basic (objects 0x00 to 0x02), 0x82 regular (an object of 0x03 to 0x7F), 0x83 extended (an object of 0x80
        to 0xFF); the flag 0x80: the server supports the individual access (read code 4) as well as the stream access.
        The objects 0x03 to 0x06 always exist (empty by default): the level is at least 0x82.
        """
        with self._objs_lock:
            last_id = max(self._objs_d, default=0)
        if last_id >= 0x80:
            return 0x83
        return 0x82 if last_id >= 0x03 else 0x81

    def __repr__(self) -> str:
        named_params = ''
        # add named parameters
        for prop_name in ('vendor_name', 'product_code', 'major_minor_revision', 'vendor_url',
                          'product_name', 'model_name', 'user_application_name'):
            prop_value = getattr(self, prop_name)
            if prop_value:
                if named_params:
                    named_params += ', '
                named_params += f'{prop_name}={getattr(self, prop_name)!r}'
        # add parameters without shortcut name
        objs_id_d_str = ''
        for _id in range(0x07, 0x100):
            try:
                obj_id_item = f'{_id!r}: {self[_id]!r}'
                if objs_id_d_str:
                    objs_id_d_str += ', '
                objs_id_d_str += obj_id_item
            except KeyError:
                pass
        # format str: classname(params_name=value, ..., objects_by_id={42: 'value'})
        class_args = named_params
        if objs_id_d_str:
            if class_args:
                class_args += ', '
            class_args += f'objects_by_id={{{objs_id_d_str}}}'
        return f'{self.__class__.__name__}({class_args})'

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

    def items(self, start: int = 0x00, end: int = 0xff) -> list[tuple[int, bytes]]:
        items_l = []
        for obj_id in range(start, end + 1):
            try:
                items_l.append((obj_id, self[obj_id]))
            except KeyError:
                pass
        return items_l
