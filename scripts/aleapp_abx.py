import base64
import enum
import struct
import typing
import xml.etree.ElementTree as etree


def abxread(in_path, multi_root):
    class AbxDecodeError(Exception):
        pass

    class XmlType(enum.IntEnum):
        START_DOCUMENT = 0
        END_DOCUMENT = 1
        START_TAG = 2
        END_TAG = 3
        TEXT = 4
        ATTRIBUTE = 15

    class DataType(enum.IntEnum):
        TYPE_NULL = 1 << 4
        TYPE_STRING = 2 << 4
        TYPE_STRING_INTERNED = 3 << 4
        TYPE_BYTES_HEX = 4 << 4
        TYPE_BYTES_BASE64 = 5 << 4
        TYPE_INT = 6 << 4
        TYPE_INT_HEX = 7 << 4
        TYPE_LONG = 8 << 4
        TYPE_LONG_HEX = 9 << 4
        TYPE_FLOAT = 10 << 4
        TYPE_DOUBLE = 11 << 4
        TYPE_BOOLEAN_TRUE = 12 << 4
        TYPE_BOOLEAN_FALSE = 13 << 4

    class AbxReader:
        MAGIC = b"ABX\x00"

        def __init__(self, stream: typing.BinaryIO):
            self._interned_strings = []
            self._stream = stream

        def _read_raw(self, length):
            buff = self._stream.read(length)
            if len(buff) < length:
                raise ValueError(f"couldn't read enough data at offset: {self._stream.tell() - len(buff)}")
            return buff

        def _read_short(self):
            return struct.unpack(">h", self._read_raw(2))[0]

        def _read_int(self):
            return struct.unpack(">i", self._read_raw(4))[0]

        def _read_long(self):
            return struct.unpack(">q", self._read_raw(8))[0]

        def _read_float(self):
            return struct.unpack(">f", self._read_raw(4))[0]

        def _read_double(self):
            return struct.unpack(">d", self._read_raw(8))[0]

        def _read_string_raw(self):
            length = self._read_short()
            if length < 0:
                raise ValueError(f"Negative string length at offset {self._stream.tell() - 2}")
            return self._read_raw(length).decode("utf-8")

        def _read_interned_string(self):
            reference = self._read_short()
            if reference == -1:
                value = self._read_string_raw()
                self._interned_strings.append(value)
            else:
                value = self._interned_strings[reference]
            return value

        def read(self, *, is_multi_root=False):
            magic = self._read_raw(len(AbxReader.MAGIC))
            if magic != AbxReader.MAGIC:
                raise ValueError(f"Invalid magic. Expected {AbxReader.MAGIC.hex()}; got: {magic.hex()}")

            document_opened = True
            root_closed = False
            root = None
            element_stack = []
            if is_multi_root:
                root = etree.Element("root")
                element_stack.append(root)

            while True:
                token_raw = self._stream.read(1)
                if not token_raw:
                    break
                token = token_raw[0]
                data_start_offset = self._stream.tell()
                xml_type = token & 0x0F

                if xml_type == XmlType.START_DOCUMENT:
                    if token & 0xF0 != DataType.TYPE_NULL:
                        raise AbxDecodeError(f"START_DOCUMENT with an invalid data type at offset {data_start_offset - 1}")
                    document_opened = True
                elif xml_type == XmlType.END_DOCUMENT:
                    if token & 0xF0 != DataType.TYPE_NULL:
                        raise AbxDecodeError(f"END_DOCUMENT with an invalid data type at offset {data_start_offset - 1}")
                    break
                elif xml_type == XmlType.START_TAG:
                    if token & 0xF0 != DataType.TYPE_STRING_INTERNED:
                        raise AbxDecodeError(f"START_TAG with an invalid data type at offset {data_start_offset - 1}")
                    tag_name = self._read_interned_string()
                    if len(element_stack) == 0:
                        element = etree.Element(tag_name)
                        element_stack.append(element)
                        root = element
                    else:
                        element = etree.SubElement(element_stack[-1], tag_name)
                        element_stack.append(element)
                elif xml_type == XmlType.END_TAG:
                    tag_name = self._read_interned_string()
                    if element_stack and element_stack[-1].tag == tag_name:
                        last = element_stack.pop()
                        if len(element_stack) == 0:
                            root_closed = True
                            root = last
                elif xml_type == XmlType.TEXT:
                    value = self._read_string_raw()
                    if element_stack and element_stack[-1].text is None:
                        element_stack[-1].text = value
                elif xml_type == XmlType.ATTRIBUTE:
                    attribute_name = self._read_interned_string()
                    data_type = token & 0xF0
                    if data_type == DataType.TYPE_NULL:
                        value = None
                    elif data_type == DataType.TYPE_BOOLEAN_TRUE:
                        value = "true"
                    elif data_type == DataType.TYPE_BOOLEAN_FALSE:
                        value = "false"
                    elif data_type == DataType.TYPE_INT:
                        value = self._read_int()
                    elif data_type == DataType.TYPE_INT_HEX:
                        value = f"{self._read_int():x}"
                    elif data_type == DataType.TYPE_LONG:
                        value = self._read_long()
                    elif data_type == DataType.TYPE_LONG_HEX:
                        value = f"{self._read_long():x}"
                    elif data_type == DataType.TYPE_FLOAT:
                        value = self._read_float()
                    elif data_type == DataType.TYPE_DOUBLE:
                        value = self._read_double()
                    elif data_type == DataType.TYPE_STRING:
                        value = self._read_string_raw()
                    elif data_type == DataType.TYPE_STRING_INTERNED:
                        value = self._read_interned_string()
                    elif data_type == DataType.TYPE_BYTES_HEX:
                        value = self._read_raw(self._read_short()).hex()
                    elif data_type == DataType.TYPE_BYTES_BASE64:
                        value = base64.encodebytes(self._read_raw(self._read_short())).decode().strip()
                    else:
                        raise AbxDecodeError(f"Unexpected attribute datatype at offset: {data_start_offset}")
                    if element_stack:
                        element_stack[-1].attrib[attribute_name] = str(value)

            if root is None:
                raise AbxDecodeError("Document was never assigned a root element")
            return etree.ElementTree(root)

    with open(in_path, "rb") as handle:
        reader = AbxReader(handle)
        return reader.read(is_multi_root=multi_root)


def checkabx(in_path):
    with open(in_path, "rb") as handle:
        return handle.read(4) == b"ABX\x00"
