"""Bounded, in-memory PDF export; no public files or user-controlled paths."""
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

_fonts = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('Statement', str(_fonts / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('StatementBold', str(_fonts / 'VeraBd.ttf')))


def statement_pdf(statement):
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles['Normal'].fontName = 'Statement'
    styles['Title'].fontName = 'StatementBold'
    historical = statement.get('historical', False)
    currency = 'CZK' if historical else 'EGP'
    blocks = [Paragraph('BankWise - Historical research statement' if historical else 'BankWise - DEMO statement', styles['Title']),
              Paragraph('PKDD99 / CTU historical CZK records. Not CIB accounts. Most recent 100 transactions. Not bank-issued.' if historical else 'Simulated EGP funds. Most recent 100 transactions. Not a bank-issued statement.', styles['Normal']),
              Spacer(1, 18)]
    for account in statement['accounts']:
        if historical and str(statement['selected_account_id']) != account['id']:
            continue
        blocks.append(Paragraph(f"Account: {account.get('account_number', account['id'])}<br/>Balance: {currency} {statement.get('latest_balance', '0') if historical else account['balance']} ({account['status']})", styles['Normal']))
        blocks.append(Spacer(1, 8))
    rows = [['Date', 'Direction', currency] + ([] if historical else ['Reference'])]
    rows += [[row['created_at'][:19].replace('T', ' '), row['direction'], row['amount']] + ([] if historical else [row.get('reference', '')])
             for row in statement['transactions']]
    table = Table(rows, colWidths=[240, 100, 120] if historical else [130, 55, 70, 205], repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16384c')),
                              ('FONTNAME', (0, 0), (-1, -1), 'Statement'),
                              ('FONTSIZE', (0, 0), (-1, -1), 9),
                              ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                              ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                              ('TOPPADDING', (0, 0), (-1, -1), 8),
                              ('LINEBELOW', (0, 0), (-1, -1), .3, colors.lightgrey)]))
    blocks.append(table)
    SimpleDocTemplate(output, title='BankWise DEMO statement', author='BankWise',
                      leftMargin=48, rightMargin=48).build(blocks)
    return output.getvalue()
