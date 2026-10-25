#!/usr/bin/env python3
"""Create a real visible cryptographic test signature using disposable keys.

Writes signed.pdf and signed-then-modified.pdf: the latter appends an
incremental update that changes page content after signing, which a reviewer
must see as a post-signature modification rather than a byte-level tamper.
"""

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers

signer = signers.SimpleSigner.load(
    "/fixtures/signer.key",
    "/fixtures/signer.crt",
    ca_chain_files=("/fixtures/root.crt",),
)
if signer is None:
    raise SystemExit("could not load disposable signer")
with open("/fixtures/unsigned.pdf", "rb") as source:
    writer = IncrementalPdfFileWriter(source)
    metadata = signers.PdfSignatureMetadata(field_name="ApprovalSignature", md_algorithm="sha256")
    field = fields.SigFieldSpec(sig_field_name="ApprovalSignature", on_page=0, box=(30, 20, 260, 85))
    with open("/fixtures/signed.pdf", "wb") as output:
        signers.sign_pdf(writer, metadata, signer=signer, new_field_spec=field, output=output)

with open("/fixtures/signed.pdf", "rb") as source:
    writer = IncrementalPdfFileWriter(source)
    stream = generic.StreamObject(stream_data=b"q 1 0 0 RG 4 w 300 20 m 390 110 l S Q")
    writer.add_stream_to_page(0, writer.add_object(stream))
    with open("/fixtures/signed-then-modified.pdf", "wb") as output:
        writer.write(output)
