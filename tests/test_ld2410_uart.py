import unittest

from ubiquitous_parakeet.ld2410_uart import (
    LD2410Report,
    REPORT_FOOTER,
    REPORT_HEADER,
    parse_report_frame,
)


def basic_frame(state=3):
    # 0x0d payload bytes: mode, 0xaa marker, state, moving/stationary/detection data,
    # then the 0x55,0x00 report terminator/check bytes.
    payload = bytes((
        0x02, 0xAA, state,
        0x4F, 0x00, 0x64,
        0x4C, 0x00, 0x64,
        0x32, 0x00,
        0x55, 0x00,
    ))
    return REPORT_HEADER + len(payload).to_bytes(2, "little") + payload + REPORT_FOOTER


class LD2410UARTTests(unittest.TestCase):
    def test_decodes_basic_report(self):
        report = parse_report_frame(basic_frame())
        self.assertEqual(report, LD2410Report(3, 79, 100, 76, 100, 50, 2))
        self.assertTrue(report.present)

    def test_no_target_is_absent(self):
        self.assertFalse(parse_report_frame(basic_frame(state=0)).present)

    def test_rejects_invalid_footer_and_state(self):
        frame = bytearray(basic_frame())
        frame[-1] = 0
        with self.assertRaisesRegex(ValueError, "header or footer"):
            parse_report_frame(bytes(frame))
        with self.assertRaisesRegex(ValueError, "target state"):
            parse_report_frame(basic_frame(state=4))


if __name__ == "__main__":
    unittest.main()
