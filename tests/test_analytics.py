"""Phase 3 — analytics aggregation."""
import datetime as dt

from db.models import Email, Opportunity, Professor
from modules import analytics


def _opp(s, country, fields):
    o = Opportunity(source_type="web", country=country, research_fields=fields)
    s.add(o); s.flush()
    return o


def test_metrics_on_seeded_db(db):
    with db.session_scope() as s:
        o1 = _opp(s, "Germany", ["Medical Imaging"])
        o2 = _opp(s, "Sweden", ["Computer Vision"])
        sent_at = dt.datetime(2026, 6, 1, 9, tzinfo=dt.timezone.utc)
        # Two first-contact sent; one replied 24h later.
        s.add(Email(opportunity_id=o1.id, status="sent", sent_at=sent_at,
                    reply_received=True,
                    reply_received_at=sent_at + dt.timedelta(hours=24)))
        s.add(Email(opportunity_id=o2.id, status="sent", sent_at=sent_at))
        # One follow-up awaiting review.
        s.add(Email(opportunity_id=o1.id, status="awaiting_review", is_followup=True))
        s.flush()

        m = analytics.compute_metrics(s)
        assert m["sends"]["first_contact_sent"] == 2
        assert m["sends"]["replies"] == 1
        assert m["sends"]["reply_rate_pct"] == 50.0
        assert m["sends"]["avg_response_hours"] == 24.0
        assert m["totals"]["followups"] == 1
        assert m["followup_funnel"]["awaiting_review"] == 1
        assert m["by_country"]["Germany"] == 1
        assert m["by_field"]["Computer Vision"] == 1


def test_metrics_empty_db(db):
    with db.session_scope() as s:
        m = analytics.compute_metrics(s)
        assert m["sends"]["first_contact_sent"] == 0
        assert m["sends"]["reply_rate_pct"] == 0.0
        assert m["sends"]["avg_response_hours"] is None
