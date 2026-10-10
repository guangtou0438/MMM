"""Allowlisted calendar export. Bytes exist only during the HTTP response."""
import csv
import io
from datetime import timedelta
from xml.sax.saxutils import escape
from sqlalchemy import select
from ..models import CalendarSession
from ..core.date_tasks import today, is_deleted, activity_type


def bounds(scope, now):
    start = today(now)
    if scope == 'week':
        start -= timedelta(days=start.weekday())
        return start, start+timedelta(days=7)
    if scope != 'month':
        raise ValueError('请选择 week 或 month')
    start = start.replace(day=1)
    return start, (start.replace(day=28)+timedelta(days=4)).replace(day=1)


def rows(factory, scope, now):
    start, end = bounds(scope, now)
    with factory() as db:
        tasks = db.scalars(select(CalendarSession).where(CalendarSession.planned_date >= start, CalendarSession.planned_date < end,
                          CalendarSession.archived_at.is_(None), CalendarSession.status.in_(['open', 'completed'])).order_by(CalendarSession.planned_date, CalendarSession.id))
        return [{'title': t.title, 'activity_type': activity_type(t), 'planned_date': str(t.planned_date),
                 'deadline': str(t.deadline_date) if t.deadline_date else '',
                 'checklist': '\n'.join(('✓ ' if a.get('done') else '□ ')+a['text'] for a in t.agenda_json),
                 'status': t.status} for t in tasks if not is_deleted(t)]


def cell(value):
    # Prevent spreadsheet formula execution for user-authored titles/checklists.
    return "'"+value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def ics_text(value):
    return value.replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(line):
    result, chunk = [], ''
    for char in line:
        if len((chunk+char).encode()) > 73:
            result.append(chunk); chunk = ' '
        chunk += char
    return '\r\n'.join(result+[chunk])


def render(items, format, now):
    if format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=['title', 'activity_type', 'planned_date', 'deadline', 'checklist', 'status'])
        writer.writeheader(); writer.writerows({k: cell(v) for k, v in row.items()} for row in items)
        return output.getvalue().encode('utf-8-sig'), 'text/csv'
    if format == 'ics':
        import hashlib
        lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//MMM//Calendar Memory Agent//ZH', 'CALSCALE:GREGORIAN']
        for index, item in enumerate(items):
            identifier = hashlib.sha256((str(index)+str(item)).encode()).hexdigest()[:24]
            description = f"Status: {item['status']}\nType: {item['activity_type']}\nDeadline: {item['deadline']}\n{item['checklist']}"
            lines += ['BEGIN:VEVENT', f'UID:{identifier}@mmm.local', 'DTSTAMP:'+now.strftime('%Y%m%dT%H%M%SZ'),
                      'DTSTART;VALUE=DATE:'+item['planned_date'].replace('-', ''), 'SUMMARY:'+ics_text(item['title']),
                      'DESCRIPTION:'+ics_text(description), 'END:VEVENT']
        lines.append('END:VCALENDAR')
        return ('\r\n'.join(fold(line) for line in lines)+'\r\n').encode(), 'text/calendar'
    if format != 'pdf':
        raise ValueError('不支持的格式')
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from pathlib import Path
    import os
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font_path = Path(os.getenv('CALENDAR_PDF_FONT', 'C:/Windows/Fonts/simsun.ttc'))
    if font_path.is_file():
        pdfmetrics.registerFont(TTFont('CalendarCJK', str(font_path)))
        font = 'CalendarCJK'
    else:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        font = 'STSong-Light'
    output = io.BytesIO()
    document = SimpleDocTemplate(output, leftMargin=50, rightMargin=50, topMargin=50, bottomMargin=50)
    style = ParagraphStyle('Calendar', fontName=font, fontSize=11, leading=19, wordWrap='CJK', textColor=colors.HexColor('#334155'))
    blocks = [Paragraph('MMM 日历导出', ParagraphStyle('Heading', parent=style, fontSize=22, leading=28)),
              Paragraph('生成日期：'+now.strftime('%Y-%m-%d'), style), Spacer(1, 24)]
    for item in items:
        for label, value in zip(('标题', '类型', '计划日期', '截止日期', '清单', '状态'), item.values()):
            if label == '类型': value = {'task': '任务', 'arrangement': '安排'}.get(value, value)
            if label == '状态': value = {'open': '进行中', 'completed': '已完成'}.get(value, value)
            if not value: value = '未设置'
            blocks.append(Paragraph(escape(label+': '+value).replace('\n', '<br/>'), style))
        blocks.append(Spacer(1, 16))
    def footer(canvas, doc):
        canvas.setFont(font, 9); canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawRightString(doc.pagesize[0]-50, 28, '第 '+str(doc.page)+' 页')
    document.build(blocks, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue(), 'application/pdf'
