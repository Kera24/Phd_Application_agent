"""Dedup by professor email / fuzzy name + title."""
from db.models import Opportunity, Professor
from modules import discovery


def test_duplicate_professor_by_email(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane Doe", email="jane@uni.edu", university="Uni X"))
        s.flush()
        assert discovery.is_duplicate_professor(s, "jane@uni.edu", "Jane Doe", "Uni X")


def test_duplicate_professor_by_fuzzy_name(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane A. Doe", university="Technical University of Munich"))
        s.flush()
        found = discovery.is_duplicate_professor(s, None, "Jane A Doe", "TU Munich")
        assert found is not None


def test_non_duplicate_professor(db):
    with db.session_scope() as s:
        s.add(Professor(name="Jane Doe", email="jane@uni.edu"))
        s.flush()
        assert discovery.is_duplicate_professor(s, None, "John Smith", "Other Uni") is None


def test_duplicate_opportunity_by_email(db):
    with db.session_scope() as s:
        s.add(Opportunity(source_type="web", professor_email="p@uni.edu", university="Uni"))
        s.flush()
        assert discovery.is_duplicate_opportunity(s, "Uni", "p@uni.edu", "Some PhD")


def test_duplicate_opportunity_by_fuzzy_title(db):
    with db.session_scope() as s:
        s.add(Opportunity(source_type="web", university="Uni",
                          position_title="PhD in Medical Imaging with Deep Learning"))
        s.flush()
        assert discovery.is_duplicate_opportunity(
            s, "Uni", None, "PhD in Deep Learning for Medical Imaging")
