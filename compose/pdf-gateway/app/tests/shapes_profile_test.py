#!/usr/bin/env python3
"""What a shapes rebuild's pages may draw. Run in the analysis-worker image, with /opt/pdf-gateway on PYTHONPATH."""
import final_validate

# The shapes profile: a page may draw from the list alone.
for content in (b"BI /W 1 ID x EI", b"(hello) Tj", b"/Evil Do", b"/F0 1 Tf (x) Tj", b"1 0 0 rg 0 0 1 1 re sh", b"1 ri", b"/P <</MCID 0>> BDC", b"1 0 0 RG\x00", b"<zz> Tj"):
    assert final_validate.check_shape_content(content) is not None, content
for content in (b"q 1 0 0 -1 0 810 cm 0.1 .2 -3 rg 0 0 m 1 1 l 1 2 3 4 5 6 c h f* Q", b"/G0 gs /I0 Do", b"BT 3 Tr /F0 12.5 Tf 100 Tz 1 0 0 1 2 3 Tm <0041> Tj ET", b"[1 2] 0 d S"):
    assert final_validate.check_shape_content(content) is None, content
for data, ok in ((b"\xff\xd8\xff\xc0\x00\x02\xff\xda\x00\x02\x12\xff\x00\xff\xd9", True), (b"\xff\xd8\xff\xfe\x00\x02\xff\xc0\x00\x02\xff\xda\x00\x02\xff\xd9", False),
                 (b"\xff\xd8\xff\xc0\x00\x02\xff\xda\x00\x02\xff\xfe\xff\xd9", False), (b"%PDF", False)):
    assert (final_validate.check_jpeg(data) is None) == ok, data
print("shapes profile: content and JPEG lists hold")
