"""Regenerate committed, visibly synthetic PDF fixtures (ReportLab dev tool).

The running sandbox needs only the generated files, not ReportLab. No official
source is opened. Run this module with a Python runtime containing ReportLab.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from dev.fixture_data import ASSETS, ROWS_PER_PAGE, observations, printed_row


def main() -> None:
    output = Path(__file__).parent / "fixtures"
    output.mkdir(exist_ok=True)
    rows = observations()
    for asset in ASSETS:
        selected = [row for row in rows if row["asset"] == asset]
        path = output / f"sika_synthetic_{asset}.pdf"
        canvas = Canvas(str(path), pagesize=A4, pageCompression=1, invariant=1)
        canvas.setTitle("SIKA SYNTHETIC FIXTURES - NOT OFFICIAL DATA")
        canvas.setAuthor("Sika local development fixtures")
        page_count = (len(selected) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE
        for page in range(page_count):
            canvas.setFont("Helvetica-Bold", 14)
            canvas.drawString(36, 800, "SIKA SYNTHETIC FIXTURES")
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawString(36, 780, "INVENTED VALUES - NOT OFFICIAL TOGO OR BENIN STATISTICS")
            canvas.setFont("Helvetica", 9)
            canvas.drawString(36, 760, f"Local test publication: {asset}. No provider or official dataset used.")
            canvas.drawString(36, 746, "Exact row values below are the authority for this synthetic fixture only.")
            canvas.setFont("Courier-Bold", 8)
            canvas.drawString(36, 724, "Series key | Period | Value | Unit | Revision")
            canvas.setFont("Courier", 8)
            for position, row in enumerate(selected[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]):
                canvas.drawString(36, 706 - position * 15, printed_row(row))
            canvas.setFont("Helvetica", 9)
            canvas.drawString(36, 48, f"Synthetic source - page {page + 1} of {page_count} - no commercial or official evidence")
            canvas.showPage()
        canvas.save()
        print(path)


if __name__ == "__main__":
    main()
