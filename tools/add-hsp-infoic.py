#!/usr/bin/env python3
"""Add the TL866man HSP-08-0 entry to a minipro infoic.xml copy."""

from pathlib import Path
import sys


ENTRY = """    <custom name=\"TL866man\">\n      <ic\n          name=\"HSP-08-0 PRG · LH2310 / DIP-28\"\n          type=\"1\"\n          protocol_id=\"0x80000001\"\n          variant=\"0x0d\"\n          read_buffer_size=\"0x100\"\n          write_buffer_size=\"0x00\"\n          code_memory_size=\"0x20000\"\n          data_memory_size=\"0x00\"\n          data_memory2_size=\"0x00\"\n          page_size=\"0x0000\"\n          pages_per_block=\"0x0000\"\n          chip_id=\"0x00000000\"\n          voltages=\"0x0900\"\n          pulse_delay=\"0x0000\"\n          flags=\"0x00000000\"\n          chip_info=\"0x0000\"\n          pin_map=\"0x0017\"\n          blank_value=\"0xff\"\n          package_details=\"0x1c000000\"\n          config=\"NULL\"\n      />\n    </custom>\n"""
MARKER = """  <database\n      type=\"INFOIC\"\n    >\n"""


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} INPUT_INFOIC OUTPUT_INFOIC", file=sys.stderr)
        return 2
    source, destination = map(Path, sys.argv[1:])
    text = source.read_text(encoding="utf-8")
    if 'name="HSP-08-0 PRG · LH2310 / DIP-28"' not in text:
        if text.count(MARKER) != 1:
            raise SystemExit("INFOIC database marker was not found exactly once")
        text = text.replace(MARKER, MARKER + ENTRY, 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
