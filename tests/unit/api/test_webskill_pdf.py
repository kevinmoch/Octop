"""PDF extraction API: text, budget, origin and malformed input."""

from __future__ import annotations

import base64
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin

ORIGIN_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def make_pdf(text: str = "Hello PDF") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream._data = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()  # noqa: SLF001
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def test_pdf_extract_returns_text_without_a_path(tmp_path) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        data = base64.b64encode(make_pdf()).decode()
        response = await client.post(
            "/api/browser-agent/pdf/extract",
            headers={**auth, **ORIGIN_HEADERS},
            json={"data": data},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pages"] == 1
        assert body["truncated"] is False
        assert "Hello PDF" in body["text"]
        assert "path" not in body
        assert str(tmp_path) not in body["text"]


async def test_pdf_extract_rejects_bad_base64_and_cross_origin(tmp_path) -> None:
    async with octop_client(tmp_path) as (client, _server):
        await bootstrap_admin(client, tmp_path)
        auth = await auth_header(client)
        bad = await client.post(
            "/api/browser-agent/pdf/extract",
            headers={**auth, **ORIGIN_HEADERS},
            json={"data": "not-base64"},
        )
        assert bad.status_code == 400
        cross = await client.post(
            "/api/browser-agent/pdf/extract",
            headers={
                **auth,
                "Origin": "https://attacker.invalid",
                "Sec-Fetch-Site": "cross-site",
            },
            json={"data": base64.b64encode(make_pdf()).decode()},
        )
        assert cross.status_code == 403
        assert PdfReader(BytesIO(make_pdf())).pages[0].extract_text() == "Hello PDF"
