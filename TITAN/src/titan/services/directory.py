"""Departments and doctors directory.

The receptionist needs to know which departments the hospital offers and which
doctors staff each one. That data is *configuration*, not code, so it lives in a
JSON file (``data/doctors.json`` by default) and is loaded through this small
seam. Swap the JSON loader for a database- or CMS-backed implementation without
touching any caller: tools and the booking service depend only on
:func:`get_directory` and the models defined here.

Lookups are case-insensitive and tolerant of extra whitespace so they work well
with speech-to-text output (e.g. "cardiology" matches "Cardiology").
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from titan.config import Settings, get_settings
from titan.utils import get_logger

logger = get_logger("titan.directory")


class Doctor(BaseModel):
    """A single doctor within a department."""

    name: str
    specialization: str | None = None


class Department(BaseModel):
    """A hospital department and the doctors who staff it."""

    name: str
    doctors: list[Doctor] = Field(default_factory=list)


def _normalize(value: str) -> str:
    """Lower-case and collapse whitespace for tolerant matching."""
    return " ".join(value.split()).casefold()


class Directory:
    """In-memory departments/doctors directory loaded from JSON.

    This is the swappable seam for a real datastore. It performs tolerant,
    case-insensitive matching so STT transcripts map cleanly onto the canonical
    department and doctor names used everywhere else in the system.
    """

    def __init__(self, departments: list[Department]) -> None:
        self._departments = departments
        # Pre-index by normalized name for O(1) tolerant lookups.
        self._by_dept: dict[str, Department] = {
            _normalize(d.name): d for d in departments
        }

    @classmethod
    def from_file(cls, path: str | Path) -> Directory:
        """Load a directory from a JSON file.

        The file must contain a top-level ``departments`` array. A missing or
        malformed file raises, because the receptionist cannot function without
        knowing its own departments — this is a deploy-time configuration error.
        """
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Doctors directory not found at {path}. Create it or set "
                f"DOCTORS_FILE to a valid path."
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Doctors directory {path} is not valid JSON: {exc}"
            ) from exc

        departments = [Department.model_validate(d) for d in raw.get("departments", [])]
        if not departments:
            raise ValueError(f"Doctors directory {path} lists no departments.")
        logger.info("Loaded %d departments from %s", len(departments), path)
        return cls(departments)

    @classmethod
    def from_db(cls, business_id: int) -> Directory | None:
        """Build a directory from the dashboard database for one business.

        Returns ``None`` when the business has no departments (caller falls
        back to the JSON file so the agent stays bootable pre-seed). Inactive
        doctors are excluded — the agent must not offer them to callers.
        """
        from titan.db import connection as db_connection

        conn = db_connection.get_db()
        dept_rows = conn.execute(
            "SELECT id, name FROM departments WHERE business_id = ? ORDER BY id",
            (business_id,),
        ).fetchall()
        if not dept_rows:
            return None
        doctor_rows = conn.execute(
            "SELECT department_id, name, specialization FROM doctors"
            " WHERE business_id = ? AND is_active = 1 ORDER BY id",
            (business_id,),
        ).fetchall()
        by_dept: dict[int, list[Doctor]] = {}
        for row in doctor_rows:
            by_dept.setdefault(row["department_id"], []).append(
                Doctor(name=row["name"], specialization=row["specialization"])
            )
        departments = [
            Department(name=row["name"], doctors=by_dept.get(row["id"], []))
            for row in dept_rows
        ]
        logger.info(
            "Loaded %d departments for business %d from DB",
            len(departments),
            business_id,
        )
        return cls(departments)

    def list_departments(self) -> list[Department]:
        """Return all departments in configured order."""
        return list(self._departments)

    def department_names(self) -> list[str]:
        """Return the canonical department names."""
        return [d.name for d in self._departments]

    def get_department(self, name: str) -> Department | None:
        """Return the department matching ``name`` (case-insensitive), or None."""
        return self._by_dept.get(_normalize(name))

    def list_doctors(self, department: str) -> list[Doctor]:
        """Return the doctors in ``department``; empty if the department is unknown."""
        dept = self.get_department(department)
        return list(dept.doctors) if dept else []

    def find_doctor(self, department: str, doctor: str) -> Doctor | None:
        """Return the matching doctor within ``department`` (case-insensitive).

        Matches on an exact normalized name first, then falls back to a partial
        match so "Rajesh" resolves to "Dr. Rajesh".
        """
        dept = self.get_department(department)
        if dept is None:
            return None
        target = _normalize(doctor)
        for doc in dept.doctors:
            if _normalize(doc.name) == target:
                return doc
        # Fall back to partial match (STT may drop the "Dr." prefix).
        for doc in dept.doctors:
            norm = _normalize(doc.name)
            if target in norm or norm in target:
                return doc
        return None


_directory: Directory | None = None


def get_directory(settings: Settings | None = None) -> Directory:
    """Return the process-wide :class:`Directory` singleton."""
    global _directory
    if _directory is None:
        settings = settings or get_settings()
        _directory = Directory.from_file(settings.doctors_path)
    return _directory
