"""
OpenLCB Configuration Description Information (CDI) Protocol and XML Generator.
Compliant with NMRA Standard S-9.7.4.1.
Enables programmatic CDI generation, automatic offset calculation, and configuration initialization.
"""

import struct
from .node_id import NodeID
from .event_id import EventID
from .memory import SPACE_CDI, SPACE_CONFIG, BytesMemorySpace, ConfigMemorySpace


def _escape_xml(val) -> str:
    """Escape XML special characters."""
    if val is None:
        return ""
    text = str(val)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class CdiItem:
    """Base class for an element inside a CDI Segment or Group."""
    def __init__(self, key: str, name: str = "", description: str = "", offset: int = None, size: int = 0):
        self.key = key
        self.name = name
        self.description = description
        self.offset = offset
        self.size = size

    def to_xml(self, indent: int = 4) -> str:
        raise NotImplementedError

    def get_default_bytes(self) -> bytes:
        return b"\x00" * self.size


class CdiEventID(CdiItem):
    """An 8-byte OpenLCB Event ID field."""
    def __init__(self, key: str, name: str = "", description: str = "", offset: int = None, default=None):
        super().__init__(key, name, description, offset, size=8)
        if default is not None:
            self.default = EventID(default)
        else:
            self.default = EventID(0)

    def to_xml(self, indent: int = 4) -> str:
        pad = " " * indent
        pad2 = " " * (indent + 2)
        lines = [f"{pad}<eventid>"]
        if self.name:
            lines.append(f"{pad2}<name>{_escape_xml(self.name)}</name>")
        if self.description:
            lines.append(f"{pad2}<description>{_escape_xml(self.description)}</description>")
        lines.append(f"{pad}</eventid>")
        return "\n".join(lines)

    def get_default_bytes(self) -> bytes:
        return self.default.to_bytes()


class CdiInt(CdiItem):
    """An integer configuration field (1, 2, 4, or 8 bytes)."""
    def __init__(
        self,
        key: str,
        name: str = "",
        description: str = "",
        size: int = 1,
        offset: int = None,
        min_val: int = None,
        max_val: int = None,
        default: int = 0,
        map_values=None,
    ):
        if size not in (1, 2, 4, 8):
            raise ValueError(f"Invalid integer size: {size}. Must be 1, 2, 4, or 8.")
        super().__init__(key, name, description, offset, size=size)
        self.min_val = min_val
        self.max_val = max_val
        self.default = default
        self.map_values = map_values or {}  # {value: "Label"}

    def to_xml(self, indent: int = 4) -> str:
        pad = " " * indent
        pad2 = " " * (indent + 2)
        pad3 = " " * (indent + 4)
        pad4 = " " * (indent + 6)
        lines = [f'{pad}<int size="{self.size}">']
        if self.name:
            lines.append(f"{pad2}<name>{_escape_xml(self.name)}</name>")
        if self.description:
            lines.append(f"{pad2}<description>{_escape_xml(self.description)}</description>")
        if self.min_val is not None:
            lines.append(f"{pad2}<min>{self.min_val}</min>")
        if self.max_val is not None:
            lines.append(f"{pad2}<max>{self.max_val}</max>")
        if self.default is not None:
            lines.append(f"{pad2}<default>{self.default}</default>")
        if self.map_values:
            lines.append(f"{pad2}<map>")
            for val, label in self.map_values.items():
                lines.append(f"{pad3}<relation>")
                lines.append(f"{pad4}<property>{val}</property>")
                lines.append(f"{pad4}<value>{_escape_xml(label)}</value>")
                lines.append(f"{pad3}</relation>")
            lines.append(f"{pad2}</map>")
        lines.append(f"{pad}</int>")
        return "\n".join(lines)

    def get_default_bytes(self) -> bytes:
        val = int(self.default)
        if self.size == 1:
            return struct.pack(">B", val & 0xFF)
        elif self.size == 2:
            return struct.pack(">H", val & 0xFFFF)
        elif self.size == 4:
            return struct.pack(">I", val & 0xFFFFFFFF)
        elif self.size == 8:
            return struct.pack(">Q", val & 0xFFFFFFFFFFFFFFFF)
        return b"\x00" * self.size


class CdiString(CdiItem):
    """A fixed-size text string field."""
    def __init__(self, key: str, name: str = "", description: str = "", size: int = 20, offset: int = None, default: str = ""):
        super().__init__(key, name, description, offset, size=size)
        self.default = default

    def to_xml(self, indent: int = 4) -> str:
        pad = " " * indent
        pad2 = " " * (indent + 2)
        lines = [f'{pad}<string size="{self.size}">']
        if self.name:
            lines.append(f"{pad2}<name>{_escape_xml(self.name)}</name>")
        if self.description:
            lines.append(f"{pad2}<description>{_escape_xml(self.description)}</description>")
        lines.append(f"{pad}</string>")
        return "\n".join(lines)

    def get_default_bytes(self) -> bytes:
        raw = self.default.encode("utf-8")[:self.size]
        return raw + b"\x00" * (self.size - len(raw))


class CdiGroup(CdiItem):
    """A group container that can hold nested CDI items and replicate."""
    def __init__(
        self,
        key: str,
        name: str = "",
        description: str = "",
        replication: int = 1,
        repname: str = "",
        offset: int = None,
    ):
        super().__init__(key, name, description, offset, size=0)
        self.replication = max(1, replication)
        self.repname = repname
        self.items = []

    def add_item(self, item: CdiItem) -> CdiItem:
        self.items.append(item)
        return item

    def add_group(self, key: str, name: str = "", description: str = "", replication: int = 1, repname: str = "") -> "CdiGroup":
        grp = CdiGroup(key, name=name, description=description, replication=replication, repname=repname)
        self.items.append(grp)
        return grp

    def add_eventid(self, key: str, name: str = "", description: str = "", default=None) -> CdiEventID:
        item = CdiEventID(key, name=name, description=description, default=default)
        self.items.append(item)
        return item

    def add_int(self, key: str, name: str = "", description: str = "", size: int = 1, min_val=None, max_val=None, default=0, map_values=None) -> CdiInt:
        item = CdiInt(key, name=name, description=description, size=size, min_val=min_val, max_val=max_val, default=default, map_values=map_values)
        self.items.append(item)
        return item

    def add_string(self, key: str, name: str = "", description: str = "", size: int = 20, default: str = "") -> CdiString:
        item = CdiString(key, name=name, description=description, size=size, default=default)
        self.items.append(item)
        return item

    def calculate_offsets(self, start_offset: int) -> int:
        """Recursively calculate offsets and total size for items in this group."""
        self.offset = start_offset
        current_offset = start_offset

        single_replica_size = 0
        for item in self.items:
            if isinstance(item, CdiGroup):
                sub_size = item.calculate_offsets(current_offset)
                current_offset += sub_size
                single_replica_size += sub_size
            else:
                item.offset = current_offset
                current_offset += item.size
                single_replica_size += item.size

        self.size = single_replica_size * self.replication
        return self.size

    def to_xml(self, indent: int = 4) -> str:
        pad = " " * indent
        pad2 = " " * (indent + 2)

        attr_parts = []
        if self.replication > 1:
            attr_parts.append(f'replication="{self.replication}"')
        attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""

        lines = [f"{pad}<group{attr_str}>"]
        if self.name:
            lines.append(f"{pad2}<name>{_escape_xml(self.name)}</name>")
        if self.description:
            lines.append(f"{pad2}<description>{_escape_xml(self.description)}</description>")
        if self.repname and self.replication > 1:
            lines.append(f"{pad2}<repname>{_escape_xml(self.repname)}</repname>")

        for item in self.items:
            lines.append(item.to_xml(indent + 2))

        lines.append(f"{pad}</group>")
        return "\n".join(lines)

    def get_default_bytes(self) -> bytes:
        single_replica = bytearray()
        for item in self.items:
            single_replica.extend(item.get_default_bytes())
        return bytes(single_replica * self.replication)


class CdiSegment:
    """An addressable memory segment in CDI (typically Space 253 / 0xFD)."""
    def __init__(self, space: int = SPACE_CONFIG, origin: int = 0, name: str = "", description: str = ""):
        self.space = space
        self.origin = origin
        self.name = name
        self.description = description
        self.items = []
        self._size = 0

    def add_item(self, item: CdiItem) -> CdiItem:
        self.items.append(item)
        return item

    def add_group(self, key: str, name: str = "", description: str = "", replication: int = 1, repname: str = "") -> CdiGroup:
        grp = CdiGroup(key, name=name, description=description, replication=replication, repname=repname)
        self.items.append(grp)
        return grp

    def add_eventid(self, key: str, name: str = "", description: str = "", default=None) -> CdiEventID:
        item = CdiEventID(key, name=name, description=description, default=default)
        self.items.append(item)
        return item

    def add_int(self, key: str, name: str = "", description: str = "", size: int = 1, min_val=None, max_val=None, default=0, map_values=None) -> CdiInt:
        item = CdiInt(key, name=name, description=description, size=size, min_val=min_val, max_val=max_val, default=default, map_values=map_values)
        self.items.append(item)
        return item

    def add_string(self, key: str, name: str = "", description: str = "", size: int = 20, default: str = "") -> CdiString:
        item = CdiString(key, name=name, description=description, size=size, default=default)
        self.items.append(item)
        return item

    def calculate_offsets(self) -> int:
        """Calculate and assign sequential offsets to all items in segment."""
        current_offset = self.origin
        for item in self.items:
            if isinstance(item, CdiGroup):
                item_size = item.calculate_offsets(current_offset)
                current_offset += item_size
            else:
                item.offset = current_offset
                current_offset += item.size
        self._size = current_offset - self.origin
        return self._size

    def get_size(self) -> int:
        return self._size

    def to_xml(self, indent: int = 2) -> str:
        pad = " " * indent
        pad2 = " " * (indent + 2)

        origin_str = f' origin="{self.origin}"' if self.origin != 0 else ""
        lines = [f'{pad}<segment space="{self.space}"{origin_str}>']
        if self.name:
            lines.append(f"{pad2}<name>{_escape_xml(self.name)}</name>")
        if self.description:
            lines.append(f"{pad2}<description>{_escape_xml(self.description)}</description>")

        for item in self.items:
            lines.append(item.to_xml(indent + 2))

        lines.append(f"{pad}</segment>")
        return "\n".join(lines)

    def get_default_bytes(self) -> bytes:
        data = bytearray()
        for item in self.items:
            data.extend(item.get_default_bytes())
        return bytes(data)


class CdiDocument:
    """
    OpenLCB Configuration Description Information (CDI) Document.
    Builds standard XML compliant with S-9.7.4.1 and maps directly to Memory Spaces.
    """
    def __init__(
        self,
        manufacturer: str = "",
        model: str = "",
        hardware_version: str = "",
        software_version: str = "",
    ):
        self.manufacturer = manufacturer
        self.model = model
        self.hardware_version = hardware_version
        self.software_version = software_version
        self.segments = []
        self._built = False
        self._items_by_key = {}

    def add_segment(self, space: int = SPACE_CONFIG, origin: int = 0, name: str = "", description: str = "") -> CdiSegment:
        seg = CdiSegment(space=space, origin=origin, name=name, description=description)
        self.segments.append(seg)
        self._built = False
        return seg

    def get_segment(self, space: int = SPACE_CONFIG) -> CdiSegment:
        for seg in self.segments:
            if seg.space == space:
                return seg
        return None

    def build(self):
        """Calculate offsets for all segments and index items by key."""
        self._items_by_key.clear()
        for seg in self.segments:
            seg.calculate_offsets()
            self._index_items(seg.items)
        self._built = True

    def _index_items(self, items):
        for item in items:
            if item.key:
                self._items_by_key[item.key] = item
            if isinstance(item, CdiGroup):
                self._index_items(item.items)

    def get_item(self, key: str) -> CdiItem:
        if not self._built:
            self.build()
        return self._items_by_key.get(key)

    def get_offset(self, key: str) -> int:
        item = self.get_item(key)
        return item.offset if item else None

    def to_xml(self) -> str:
        """Generate well-formed OpenLCB CDI XML string."""
        if not self._built:
            self.build()

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<cdi xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:noNamespaceSchemaLocation="http://openlcb.org/schema/cdi/1/1/cdi.xsd">',
        ]

        # Node Identification
        if self.manufacturer or self.model or self.hardware_version or self.software_version:
            lines.append("  <identification>")
            if self.manufacturer:
                lines.append(f"    <manufacturer>{_escape_xml(self.manufacturer)}</manufacturer>")
            if self.model:
                lines.append(f"    <model>{_escape_xml(self.model)}</model>")
            if self.hardware_version:
                lines.append(f"    <hardwareVersion>{_escape_xml(self.hardware_version)}</hardwareVersion>")
            if self.software_version:
                lines.append(f"    <softwareVersion>{_escape_xml(self.software_version)}</softwareVersion>")
            lines.append("  </identification>")

        # ACDI indicator
        lines.append("  <acdi/>")

        # Segments
        for seg in self.segments:
            lines.append(seg.to_xml(indent=2))

        lines.append("</cdi>")
        return "\n".join(lines)

    def to_bytes(self) -> bytes:
        """
        Generate UTF-8 encoded XML bytes with required trailing null-terminator (0x00)
        as expected by JMRI and OpenLCB CDI specifications.
        """
        xml_str = self.to_xml()
        return xml_str.encode("utf-8") + b"\x00"

    def create_cdi_space(self) -> BytesMemorySpace:
        """Create a ready-to-use read-only BytesMemorySpace for Space 0xFF (CDI)."""
        return BytesMemorySpace(SPACE_CDI, self.to_bytes(), read_only=True)

    def create_default_config(self, space: int = SPACE_CONFIG) -> bytes:
        """Generate the default binary memory configuration image for a segment."""
        if not self._built:
            self.build()
        seg = self.get_segment(space)
        if seg is None:
            return b""
        return seg.get_default_bytes()

    def create_config_space(
        self,
        space: int = SPACE_CONFIG,
        filename: str = None,
        on_write=None,
        min_size: int = 512,
    ) -> ConfigMemorySpace:
        """
        Create a ConfigMemorySpace initialized with default configuration values,
        and automatically loaded from non-volatile storage (filename) if present.
        """
        default_bytes = self.create_default_config(space)
        size = max(len(default_bytes), min_size)
        return ConfigMemorySpace(
            size=size,
            on_write=on_write,
            filename=filename,
            default_data=default_bytes,
        )
