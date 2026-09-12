"""Offline unit tests for the departments/doctors directory.

These exercise tolerant, case-insensitive matching (so STT transcripts map onto
canonical names) and JSON loading, with no network or model access.
"""

import json

import pytest

from titan.services.directory import Department, Directory, Doctor


def _directory() -> Directory:
    return Directory(
        [
            Department(
                name="Cardiology",
                doctors=[Doctor(name="Dr. Rajesh"), Doctor(name="Dr. Anitha")],
            ),
            Department(name="Orthopedics", doctors=[Doctor(name="Dr. Kiran")]),
        ]
    )


def test_department_names_in_order() -> None:
    assert _directory().department_names() == ["Cardiology", "Orthopedics"]


def test_get_department_is_case_and_space_insensitive() -> None:
    directory = _directory()
    assert directory.get_department("  cardiology ").name == "Cardiology"
    assert directory.get_department("CARDIOLOGY").name == "Cardiology"


def test_get_department_unknown_returns_none() -> None:
    assert _directory().get_department("Oncology") is None


def test_list_doctors() -> None:
    names = [d.name for d in _directory().list_doctors("cardiology")]
    assert names == ["Dr. Rajesh", "Dr. Anitha"]


def test_list_doctors_unknown_department_is_empty() -> None:
    assert _directory().list_doctors("Oncology") == []


def test_find_doctor_exact_and_partial() -> None:
    directory = _directory()
    assert directory.find_doctor("Cardiology", "dr. anitha").name == "Dr. Anitha"
    # STT often drops the "Dr." prefix — partial match should still resolve it.
    assert directory.find_doctor("Cardiology", "Rajesh").name == "Dr. Rajesh"


def test_find_doctor_unknown_returns_none() -> None:
    assert _directory().find_doctor("Cardiology", "Dr. Nobody") is None


def test_from_file_roundtrip(tmp_path) -> None:
    path = tmp_path / "doctors.json"
    path.write_text(
        json.dumps(
            {"departments": [{"name": "ENT", "doctors": [{"name": "Dr. Ravi"}]}]}
        ),
        encoding="utf-8",
    )
    directory = Directory.from_file(path)
    assert directory.department_names() == ["ENT"]
    assert directory.find_doctor("ENT", "Ravi").name == "Dr. Ravi"


def test_from_file_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        Directory.from_file(tmp_path / "nope.json")


def test_from_file_empty_departments_raises(tmp_path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"departments": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        Directory.from_file(path)


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """A temp dashboard DB with departments/doctors for two businesses."""
    from titan.db import connection as db_connection

    monkeypatch.setattr(db_connection, "_db_path_override", tmp_path / "t.db")
    db_connection._reset()
    conn = db_connection.get_db()
    conn.execute(
        "INSERT INTO businesses (id, name, slug, created_at, updated_at)"
        " VALUES (1, 'ABC Hospital', 'abc', '', ''), (2, 'XYZ Clinic', 'xyz', '', '')"
    )
    conn.executemany(
        "INSERT INTO departments (id, business_id, name) VALUES (?, ?, ?)",
        [(1, 1, "Cardiology"), (2, 1, "ENT"), (3, 2, "Dermatology")],
    )
    conn.executemany(
        "INSERT INTO doctors (business_id, department_id, name, is_active)"
        " VALUES (?, ?, ?, ?)",
        [
            (1, 1, "Dr. Rajesh", 1),
            (1, 1, "Dr. Anitha", 1),
            (1, 2, "Dr. Ravi", 1),
            (1, 2, "Dr. Retired", 0),  # inactive: must be excluded
            (2, 3, "Dr. Meera", 1),
        ],
    )
    conn.commit()
    yield conn
    db_connection._reset()


def test_from_db_builds_tenant_scoped_directory(seeded_db) -> None:
    directory = Directory.from_db(1)
    assert directory.department_names() == ["Cardiology", "ENT"]
    assert directory.find_doctor("cardiology", "rajesh").name == "Dr. Rajesh"
    # Inactive doctors are hidden from the agent.
    assert directory.find_doctor("ENT", "Dr. Retired") is None
    # Other tenants' data never leaks in.
    assert directory.get_department("Dermatology") is None


def test_from_db_empty_business_returns_none(seeded_db) -> None:
    assert Directory.from_db(99) is None
