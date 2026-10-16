import json
from html import escape
from io import BytesIO
from typing import Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _paragraph(value: object, style) -> Paragraph:
    return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)


def build_work_order_audit_pack(work_order, events: Sequence[object]) -> bytes:
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Fieldnote audit pack {work_order.id}",
        author="Fieldnote",
    )
    styles = getSampleStyleSheet()
    styles["BodyText"].alignment = TA_LEFT
    story = [
        Paragraph("Fieldnote Work Order Audit Pack", styles["Title"]),
        Spacer(1, 10),
        _paragraph(f"Work order: {work_order.id}", styles["BodyText"]),
        _paragraph(f"Status: {work_order.status}", styles["BodyText"]),
        _paragraph(f"Summary: {work_order.summary}", styles["BodyText"]),
        _paragraph(f"Diagnosis: {work_order.diagnosis or 'Not recorded'}", styles["BodyText"]),
        _paragraph(f"Recommendation: {work_order.recommendation or 'Not recorded'}", styles["BodyText"]),
        _paragraph(f"CMMS reference: {work_order.cmms_reference or 'Not dispatched'}", styles["BodyText"]),
        Spacer(1, 12),
        Paragraph("Evidence references", styles["Heading2"]),
    ]
    workflow_state = work_order.workflow_state if isinstance(work_order.workflow_state, dict) else {}
    evidence = workflow_state.get("evidence_sources", [])
    if evidence:
        for source in evidence:
            story.append(_paragraph(json.dumps(source, sort_keys=True, ensure_ascii=True), styles["BodyText"]))
    else:
        story.append(_paragraph("No document evidence references were recorded.", styles["BodyText"]))
    story.extend([Spacer(1, 12), Paragraph("Hash-linked workflow events", styles["Heading2"])])
    rows = [["Timestamp / event", "Actor / transition", "Event hash"]]
    for event in events:
        when_and_type = f"{event.created_at.isoformat()}\n{event.event_type}"
        actor_and_transition = (
            f"{event.actor_id or event.actor_type}\n"
            f"{event.from_status or 'start'} -> {event.to_status or 'recorded'}\n"
            f"{json.dumps(event.details or {}, sort_keys=True, ensure_ascii=True)}"
        )
        event_hash = f"{event.event_hash}\nPrevious: {event.previous_hash or 'genesis'}"
        rows.append([
            _paragraph(when_and_type, styles["BodyText"]),
            _paragraph(actor_and_transition, styles["BodyText"]),
            _paragraph(event_hash, styles["BodyText"]),
        ])
    table = Table(rows, colWidths=[1.55 * inch, 2.85 * inch, 2.0 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b2924")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#aab5ae")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    document.build(story)
    return output.getvalue()