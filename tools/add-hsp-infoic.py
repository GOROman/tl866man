#!/usr/bin/env python3
"""Add TL866man custom read-only entries to a minipro infoic.xml copy."""

from pathlib import Path
import sys


ENTRY = """    <custom name="TL866man">
      <ic
          name="HSP-08-0 PRG · LH2310 / DIP-28"
          type="1"
          protocol_id="0x80000001"
          variant="0x0d"
          read_buffer_size="0x100"
          write_buffer_size="0x00"
          code_memory_size="0x20000"
          data_memory_size="0x00"
          data_memory2_size="0x00"
          page_size="0x0000"
          pages_per_block="0x0000"
          chip_id="0x00000000"
          voltages="0x0900"
          pulse_delay="0x0000"
          flags="0x00000000"
          chip_info="0x0000"
          pin_map="0x0017"
          blank_value="0xff"
          package_details="0x1c000000"
          config="NULL"
      />
      <ic
          name="SC-88Pro PRG LH538U0P-ROT180 DIP40"
          type="1"
          protocol_id="0x80000001"
          variant="0x0e"
          read_buffer_size="0x400"
          write_buffer_size="0x00"
          code_memory_size="0x80000"
          data_memory_size="0x00"
          data_memory2_size="0x00"
          page_size="0x0000"
          pages_per_block="0x0000"
          chip_id="0x00000000"
          voltages="0x0900"
          pulse_delay="0x0000"
          flags="0x01002000"
          chip_info="0x0000"
          pin_map="0x0017"
          blank_value="0xffff"
          package_details="0x28000000"
          config="NULL"
      />
    </custom>
"""
MARKER = """  <database
      type="INFOIC"
    >
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} INPUT_INFOIC OUTPUT_INFOIC", file=sys.stderr)
        return 2
    source, destination = map(Path, sys.argv[1:])
    text = source.read_text(encoding="utf-8")
    if ('name="HSP-08-0 PRG · LH2310 / DIP-28"' not in text or
            'name="SC-88Pro PRG LH538U0P-ROT180 DIP40"' not in text):
        if text.count(MARKER) != 1:
            raise SystemExit("INFOIC database marker was not found exactly once")
        text = text.replace(MARKER, MARKER + ENTRY, 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
