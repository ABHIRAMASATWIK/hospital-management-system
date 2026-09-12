"""The tenancy matrix — the critical isolation tests.

Business admin of Alpha must never read or mutate Beta's data; super_admin
addresses tenants explicitly with ?business_id=.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TOMORROW_10AM = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=1)).replace(
    hour=10, minute=0, second=0, microsecond=0
)


def _create_appointment(client, headers, name="Asha", business_id=None):
    params = {"business_id": business_id} if business_id else {}
    return client.post(
        "/api/v1/appointments",
        headers=headers,
        params=params,
        json={
            "name": name,
            "department": "General",
            "doctor": "Dr. Mehta",
            "appointment_start": _TOMORROW_10AM.isoformat(),
        },
    )


def test_business_admin_sees_only_own_appointments(client, alpha_headers, beta_headers):
    assert _create_appointment(client, alpha_headers, "AlphaPatient").status_code == 201
    assert _create_appointment(client, beta_headers, "BetaPatient").status_code == 201

    alpha_list = client.get("/api/v1/appointments", headers=alpha_headers).json()
    names = [a["name"] for a in alpha_list["items"]]
    assert names == ["AlphaPatient"]


def test_cross_tenant_query_param_is_403(client, alpha_headers):
    resp = client.get(
        "/api/v1/appointments", headers=alpha_headers, params={"business_id": 2}
    )
    assert resp.status_code == 403


def test_cannot_mutate_other_tenants_row(client, alpha_headers, beta_headers):
    created = _create_appointment(client, beta_headers, "BetaPatient").json()
    resp = client.patch(
        f"/api/v1/appointments/{created['id']}",
        headers=alpha_headers,
        json={"name": "Hacked"},
    )
    assert resp.status_code == 404  # invisible across tenants, not just forbidden


def test_super_admin_requires_business_id(client, admin_headers):
    resp = client.get("/api/v1/appointments", headers=admin_headers)
    assert resp.status_code == 400


def test_super_admin_with_business_id_sees_that_tenant(
    client, admin_headers, alpha_headers
):
    _create_appointment(client, alpha_headers, "AlphaPatient")
    resp = client.get(
        "/api/v1/appointments", headers=admin_headers, params={"business_id": 1}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_business_admin_cannot_reach_admin_routes(client, alpha_headers):
    assert (
        client.get("/api/v1/admin/businesses", headers=alpha_headers).status_code == 403
    )


def test_overview_is_tenant_scoped(client, alpha_headers, beta_headers):
    _create_appointment(client, alpha_headers, "AlphaPatient")
    alpha_overview = client.get("/api/v1/overview", headers=alpha_headers).json()
    beta_overview = client.get("/api/v1/overview", headers=beta_headers).json()
    assert len(alpha_overview["upcoming_appointments"]) == 1
    assert beta_overview["upcoming_appointments"] == []
