"""Fixed, versioned PDF template built from owned, recorded examination facts."""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
import io
import os

TEMPLATE_VERSION = 'spine-exam-report-1.0'


def build_case_pdf(case, questionnaire=None, gait_report=None, *, profile=None, legacy_entries=None, review=None, contact_sheet=None, history=None):
    # Source launches can use the project-local dependency staged by setup_local.
    try:
        import reportlab
    except ImportError:
        import sys
        local_library = Path(__file__).resolve().parents[1] / '.runtime' / 'pdf'
        if local_library.is_dir(): sys.path.insert(0, str(local_library))
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
    from reportlab.graphics.shapes import Drawing, Rect, String, Line, Circle

    font = 'SpineReportCN'
    if font not in pdfmetrics.getRegisteredFontNames():
        candidates = [os.getenv('MINDSPINE_FONT_PATH', ''), 'C:/Windows/Fonts/simhei.ttf']
        existing = next((p for p in candidates if p and Path(p).is_file()), None)
        if existing:
            try: pdfmetrics.registerFont(TTFont(font, existing))
            except Exception: existing = None
        if not existing:
            font = 'STSong-Light'
            if font not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(UnicodeCIDFont(font))
    ink, muted, accent = colors.HexColor('#14293D'), colors.HexColor('#627487'), colors.HexColor('#177D8D')
    body = ParagraphStyle('Body', fontName=font, fontSize=9.5, leading=16, textColor=ink, wordWrap='CJK')
    small = ParagraphStyle('Small', parent=body, fontSize=8, leading=12, textColor=muted)
    heading = ParagraphStyle('Heading', parent=body, fontSize=13, leading=19, spaceBefore=14, spaceAfter=8)
    title = ParagraphStyle('Title', parent=body, fontSize=25, leading=33, spaceAfter=8)
    def p(value, style=body):
        return Paragraph(escape(str(value if value is not None else '未记录')).replace('\n', '<br/>'), style)
    section_number = 0
    def section(label):
        nonlocal section_number
        section_number += 1
        story.extend([p(f'{section_number:02d}  {label}', heading)])
    def table(rows, widths=(105, 388)):
        item = Table([[p(cell) for cell in row] for row in rows], colWidths=widths, hAlign='LEFT')
        item.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(0,-1),colors.HexColor('#EFF4F7')),
            ('LINEBELOW',(0,0),(-1,-1),0.4,colors.HexColor('#DFE6EC')),('LEFTPADDING',(0,0),(-1,-1),10),
            ('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
        story.append(item)
    profile, review = profile or {}, review or {}
    gait_report, legacy_entries = gait_report or {}, legacy_entries or []
    output = io.BytesIO()
    report_id = str(case.get('id',''))[:12].upper()
    recorded = datetime.fromtimestamp(case.get('created_at') or datetime.now().timestamp()).strftime('%Y-%m-%d %H:%M')
    document = SimpleDocTemplate(output, pagesize=(595.28,841.89), rightMargin=51, leftMargin=51, topMargin=66, bottomMargin=57,
        title='脊见智诊综合检查报告', author='脊见智诊', subject=TEMPLATE_VERSION)
    def footer(c, doc):
        c.saveState(); c.setFillColor(ink); c.rect(0,812,595.28,30,fill=1,stroke=0)
        c.setFillColor(colors.white); c.setFont(font,9); c.drawString(51,823,'脊见智诊  /  SPINEINSIGHT')
        c.setStrokeColor(colors.HexColor('#D7E0E7')); c.line(51,43,544,43)
        c.setFillColor(muted); c.setFont(font,8); c.drawString(51,28,f'检查编号 {report_id}  |  {TEMPLATE_VERSION}')
        c.drawRightString(544,28,f'{doc.page}'); c.restoreState()
    story = [p('综合检查报告',title),p('影像 · 步态 · 问卷 · 复核',small),Spacer(1,17)]
    rows = [('检查名称',case.get('title','本次脊柱检查')),('检查时间',recorded)]
    for label,key in [('姓名','display_name'),('机构','institution'),('记录人员','reviewer')]:
        if profile.get(key): rows.append((label,profile[key]))
    rows.append(('报告状态','已复核归档' if review.get('archived') else '待复核'))
    table(rows)
    section('检查摘要')
    analysis = gait_report.get('analysis') or {}
    if analysis.get('summary'): story.append(p(analysis['summary']))
    else: story.append(p('本报告汇集本次检查已记录的资料；未完成项目保留为空，不填入推测数值。'))
    if questionnaire:
        triage = questionnaire.get('triage') or {}
        story.append(Spacer(1,5)); story.append(p(triage.get('message','已记录问卷信息。')))
    if gait_report:
        section('步态分析')
        quality = gait_report.get('quality') or {}
        prediction = gait_report.get('prediction') or {}
        scores = prediction.get('probabilities') or analysis.get('model_scores') or {}
        if scores:
            chart = Drawing(493,96)
            for i,(label,value) in enumerate(scores.items()):
                y=70-i*29; value=max(0,min(float(value),1))
                chart.add(String(0,y+3,str(label),fontName=font,fontSize=9,fillColor=ink))
                chart.add(Rect(88,y,330,14,fillColor=colors.HexColor('#E9EFF3'),strokeColor=None))
                chart.add(Rect(88,y,330*value,14,fillColor=accent,strokeColor=None))
                chart.add(String(432,y+3,f'{value:.1%}',fontName=font,fontSize=9,fillColor=ink))
            story.extend([chart,p('三类模型分数；与其他模态的置信度分别展示。',small),Spacer(1,9)])
        source = gait_report.get('source') or {}
        table([('运行版本',gait_report.get('report_view_version','2.0.1')),('质量状态','通过' if quality.get('passed') else '需复核/重拍'),
               ('有效轮廓',f"{quality.get('valid_frames','--')} / {quality.get('sampled_frames','--')} 帧"),
               ('轮廓覆盖率',f"{float(quality.get('usable_coverage',0)):.1%}"),('人物轨迹',(gait_report.get('tracking') or {}).get('selected_track_id','见视频预览'))])
        for issue in quality.get('issues',[]):
            if issue.get('severity') in {'error','warning'}: story.append(p('- '+str(issue.get('message','')),small))
        if contact_sheet and Path(contact_sheet).is_file():
            from PIL import Image as PILImage
            with PILImage.open(contact_sheet) as source_image: width,height=source_image.size
            display_h=min(170,493*height/max(width,1)); display_w=display_h*width/max(height,1)
            story.extend([Spacer(1,10),Image(str(contact_sheet),width=display_w,height=display_h,hAlign='LEFT'),p('人物与轮廓抽帧复核',small)])
    if questionnaire:
        section('症状与功能记录')
        answers=questionnaire.get('answers') or {}; rows=[]
        fields=[('疼痛评分','pain_score'),('活动受限','activity_limit'),('症状位置','symptom_location'),('持续时间','duration'),
                ('功能影响','function_impacts'),('既往情况','history'),('当前处理','treatment'),('本次目标','goals')]
        for label,key in fields:
            value=answers.get(key)
            if value is not None and value!='' and value!=[]:
                rows.append((label,'、'.join(map(str,value)) if isinstance(value,list) else value))
        if rows: table(rows)
        story.append(p('问卷用于记录本次自述情况，版本：'+str(questionnaire.get('protocol_version','')),small))
    if legacy_entries:
        section('已关联影像与测量')
        rows=[]
        for entry in legacy_entries[:20]:
            payload=entry.get('payload') or {}; angles=payload.get('angles') or {}
            if angles:
                values='；'.join(f'{k}: {v}' for k,v in angles.items() if isinstance(v,(int,float,str)))
                rows.append((str(entry.get('kind','影像')).upper(),values))
        if rows: table(rows)
        else: story.append(p('已关联的历史资料没有可直接列出的量化测量。'))
    section('分析建议与复核')
    recommendations=analysis.get('recommendations') or []
    for value in recommendations:
        if isinstance(value,dict): value=value.get('text') or value.get('message') or value.get('title') or ''
        if value: story.append(p('- '+str(value)))
    story.extend([Spacer(1,8),p('复核备注',small),p(review.get('notes') or '尚未填写。')])
    if review.get('archived_at'):
        story.append(p('归档时间：'+datetime.fromtimestamp(review['archived_at']).strftime('%Y-%m-%d %H:%M'),small))
    section('数据来源')
    provenance=[]
    if gait_report:
        provenance += [('步态报告',gait_report.get('job_id','本次任务')),('报告视图',gait_report.get('report_view_version','2.0.1')),
                       ('质控协议',(gait_report.get('quality') or {}).get('gate_version','未记录'))]
    if questionnaire: provenance.append(('问卷协议',questionnaire.get('protocol_version','未记录')))
    provenance.append(('结果采用','各模态保留原始来源与质量信息；不同定义的置信度不直接排序。'))
    source_heading = story.pop()
    story.append(KeepTogether([source_heading, *[p(f'{label}：{value}', small) for label, value in provenance]]))
    document.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
