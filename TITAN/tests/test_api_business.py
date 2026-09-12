"""Business-scoped API tests: overview, appointments CRUD, staff, calls,
analytics, settings, holidays, AI settings."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Kolkata")


def _tomorrow(hour: int) -> str:
    dt = (datetime.now(_TZ) + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return dt.isoformat()


def _appointment_payload(name="Asha", hour=10):
    return {
        "name": name,
        "phone": "9999900000",
        "department": "General",
        "doctor": "Dr. Mehta",
        "appointment_start": _tomorrow(hour),
    }


# -- appointments ------------------------------------------------------------


def test_appointment_create_read_update(client, alpha_headers):
    created = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    )
    assert created.status_code == 201
    appt_id = created.json()["id"]

    got = client.get(f"/api/v1/appointments/{appt_id}", headers=alpha_headers)
    assert got.status_code == 200
    assert got.json()["name"] == "Asha"

    patched = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"phone": "8888800000"},
    )
    assert patched.status_code == 200
    assert patched.json()["phone"] == "8888800000"


def test_appointment_cancel(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    resp = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"status": "cancelled"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancelled_at"]


def test_appointment_reschedule(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    resp = client.post(
        f"/api/v1/appointments/{appt_id}/reschedule",
        headers=alpha_headers,
        json={"new_start": _tomorrow(15)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rescheduled"
    assert body["appointment_start"] == _tomorrow(15)


def test_appointment_reschedule_outside_hours_is_422(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    resp = client.post(
        f"/api/v1/appointments/{appt_id}/reschedule",
        headers=alpha_headers,
        json={"new_start": _tomorrow(22)},  # business hours are 9-18
    )
    assert resp.status_code == 422


def test_appointment_search_and_pagination(client, alpha_headers):
    for i, name in enumerate(["Asha", "Vikram", "Meera"]):
        client.post(
            "/api/v1/appointments",
            headers=alpha_headers,
            json=_appointment_payload(name, hour=10 + i),
        )
    found = client.get(
        "/api/v1/appointments", headers=alpha_headers, params={"q": "vik"}
    ).json()
    assert found["total"] == 1
    page = client.get(
        "/api/v1/appointments",
        headers=alpha_headers,
        params={"page": 1, "page_size": 2},
    ).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_appointment_payment_defaults_and_update(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    got = client.get(f"/api/v1/appointments/{appt_id}", headers=alpha_headers).json()
    assert got["payment_status"] == "pending"
    assert got["payment_method"] is None

    patched = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={
            "payment_status": "paid",
            "payment_method": "UPI",
            "payment_notes": "Paid ₹500 via UPI",
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["payment_status"] == "paid"
    assert body["payment_method"] == "UPI"
    assert body["payment_notes"] == "Paid ₹500 via UPI"


def test_appointment_payment_status_validated(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    resp = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"payment_status": "maybe"},
    )
    assert resp.status_code == 422


def test_appointment_complete_stamps_and_clears_timestamp(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    done = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"status": "completed"},
    ).json()
    assert done["status"] == "completed"
    assert done["completed_at"]

    # Re-completing must not move the original completion timestamp.
    stamp = done["completed_at"]
    again = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"status": "completed"},
    ).json()
    assert again["completed_at"] == stamp

    # Reverting the status clears the completion timestamp.
    reverted = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"status": "confirmed"},
    ).json()
    assert reverted["completed_at"] is None


def test_appointment_list_filters_department_and_payment(client, alpha_headers):
    a = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    cardio = _appointment_payload("Meera", hour=11)
    cardio["department"] = "Cardiology"
    client.post("/api/v1/appointments", headers=alpha_headers, json=cardio)
    client.patch(
        f"/api/v1/appointments/{a}",
        headers=alpha_headers,
        json={"payment_status": "paid"},
    )

    by_dept = client.get(
        "/api/v1/appointments",
        headers=alpha_headers,
        params={"department": "Cardiology"},
    ).json()
    assert by_dept["total"] == 1
    assert by_dept["items"][0]["name"] == "Meera"

    paid = client.get(
        "/api/v1/appointments",
        headers=alpha_headers,
        params={"payment_status": "paid"},
    ).json()
    assert paid["total"] == 1
    assert paid["items"][0]["id"] == a


def test_appointment_edit_start_via_patch(client, alpha_headers):
    appt_id = client.post(
        "/api/v1/appointments", headers=alpha_headers, json=_appointment_payload()
    ).json()["id"]
    moved = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"appointment_start": _tomorrow(16)},
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["appointment_start"] == _tomorrow(16)
    assert body["status"] == "confirmed"  # unlike /reschedule, status is untouched

    outside = client.patch(
        f"/api/v1/appointments/{appt_id}",
        headers=alpha_headers,
        json={"appointment_start": _tomorrow(23)},
    )
    assert outside.status_code == 422


# -- staff (departments + doctors) --------------------------------------------


def test_department_and_doctor_crud(client, alpha_headers):
    dept = client.post(
        "/api/v1/departments", headers=alpha_headers, json={"name": "Cardiology"}
    )
    assert dept.status_code == 201
    dept_id = dept.json()["id"]

    doc = client.post(
        "/api/v1/doctors",
        headers=alpha_headers,
        json={
            "department_id": dept_id,
            "name": "Dr. Rajesh",
            "specialization": "Cardio",
        },
    )
    assert doc.status_code == 201
    doc_id = doc.json()["id"]

    patched = client.patch(
        f"/api/v1/doctors/{doc_id}",
        headers=alpha_headers,
        json={"availability": '{"mon": [[9, 13]]}'},
    )
    assert patched.status_code == 200

    deleted = client.delete(f"/api/v1/doctors/{doc_id}", headers=alpha_headers)
    assert deleted.status_code == 204
    doctors = client.get("/api/v1/doctors", headers=alpha_headers).json()
    assert all(d["id"] != doc_id for d in doctors["items"])  # soft-deleted = hidden


def test_department_names_unique_per_tenant(client, alpha_headers, beta_headers):
    assert (
        client.post(
            "/api/v1/departments", headers=alpha_headers, json={"name": "ENT"}
        ).status_code
        == 201
    )
    dup = client.post(
        "/api/v1/departments", headers=alpha_headers, json={"name": "ENT"}
    )
    assert dup.status_code == 409
    # Same name is fine for a different tenant.
    assert (
        client.post(
            "/api/v1/departments", headers=beta_headers, json={"name": "ENT"}
        ).status_code
        == 201
    )


# -- AI settings ---------------------------------------------------------------


def test_ai_settings_roundtrip(client, alpha_headers):
    got = client.get("/api/v1/ai-settings", headers=alpha_headers)
    assert got.status_code == 200
    updated = client.put(
        "/api/v1/ai-settings",
        headers=alpha_headers,
        json={
            "assistant_name": "Nova",
            "greeting": "Welcome to Alpha Hospital!",
            "voice": "anushka",
            "language": "en-IN",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["greeting"] == "Welcome to Alpha Hospital!"


# -- business settings + holidays ----------------------------------------------


def test_business_settings_update(client, alpha_headers):
    resp = client.put(
        "/api/v1/settings",
        headers=alpha_headers,
        json={"name": "Alpha Multi-speciality", "business_hours_end": 20},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alpha Multi-speciality"
    assert resp.json()["business_hours_end"] == 20


def test_holiday_add_list_delete(client, alpha_headers):
    added = client.post(
        "/api/v1/holidays",
        headers=alpha_headers,
        json={"date": "2026-08-15", "label": "Independence Day"},
    )
    assert added.status_code == 201
    listed = client.get("/api/v1/holidays", headers=alpha_headers).json()
    assert len(listed) == 1
    assert (
        client.delete(
            f"/api/v1/holidays/{added.json()['id']}", headers=alpha_headers
        ).status_code
        == 204
    )


# -- calls + analytics -----------------------------------------------------------


def _insert_call(conn, call_id, business_id, status="completed", hour=10, dur=120):
    conn.execute(
        "INSERT INTO calls (call_id, business_id, status, duration_seconds,"
        " summary, intent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            call_id,
            business_id,
            status,
            dur,
            "Caller booked appointment",
            "book",
            f"2026-07-14T{hour:02d}:00:00+05:30",
        ),
    )
    conn.commit()


def test_call_history_list_and_detail(client, alpha_headers, api_env):
    _insert_call(api_env, "c-1", 1)
    _insert_call(api_env, "c-2", 1, status="missed", dur=0)
    _insert_call(api_env, "c-other", 2)

    calls = client.get("/api/v1/calls", headers=alpha_headers).json()
    assert calls["total"] == 2

    missed = client.get(
        "/api/v1/calls", headers=alpha_headers, params={"status": "missed"}
    ).json()
    assert missed["total"] == 1

    detail = client.get(
        f"/api/v1/calls/{calls['items'][0]['id']}", headers=alpha_headers
    )
    assert detail.status_code == 200


def test_analytics_shape(client, alpha_headers, api_env):
    _insert_call(api_env, "c-1", 1, hour=10)
    _insert_call(api_env, "c-2", 1, status="missed", hour=14)
    resp = client.get(
        "/api/v1/analytics",
        headers=alpha_headers,
        params={"from": "2026-07-01", "to": "2026-07-31"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {
        "calls_per_day",
        "appointments_per_day",
        "missed_per_day",
        "conversion_rate",
        "peak_hours",
    }
    assert len(body["peak_hours"]) == 24
    assert body["peak_hours"][10] == 1
