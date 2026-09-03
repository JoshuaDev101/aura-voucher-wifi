import os
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OmadaError(RuntimeError):
    pass


class OmadaClient:
    def __init__(self):
        self.base = os.environ.get(
            "AURA_OMADA_URL", "https://127.0.0.1:8043"
        ).rstrip("/")
        self.user = os.environ.get("AURA_OMADA_USER", "")
        self.password = os.environ.get("AURA_OMADA_PASSWORD", "")
        self.site = os.environ.get("AURA_OMADA_SITE_ID", "")
        self.rate = os.environ.get("AURA_OMADA_RATE_LIMIT_ID", "")
        self.verify = (
            os.environ.get("AURA_OMADA_VERIFY_TLS", "false").lower() == "true"
        )

        self.s = requests.Session()
        self.oid = None
        self.token = None

    def _json(self, response, context):
        try:
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise OmadaError(f"{context} failed: {exc}") from exc

        if data.get("errorCode") not in (0, None):
            raise OmadaError(
                data.get("msg")
                or f"{context}: Omada error {data.get('errorCode')}"
            )
        return data

    def info(self):
        try:
            r = self.s.get(
                self.base + "/api/info",
                verify=self.verify,
                timeout=5,
            )
        except Exception as exc:
            raise OmadaError(f"Controller unavailable: {exc}") from exc

        d = self._json(r, "Controller info")
        x = d.get("result") or d
        self.oid = x.get("omadacId") or d.get("omadacId")

        return {
            "version": x.get("controllerVer") or "Unknown",
            "api_version": x.get("apiVer") or "Unknown",
            "omadac_id": self.oid or "Unknown",
        }

    def probe(self):
        try:
            return {"online": True, "error": None, **self.info()}
        except Exception as exc:
            return {
                "online": False,
                "error": str(exc),
                "version": "Unknown",
                "api_version": "Unknown",
                "omadac_id": "Unknown",
            }

    def login(self):
        if not self.user or not self.password:
            raise OmadaError("Omada credentials not configured.")

        if not self.oid:
            self.info()

        r = self.s.post(
            f"{self.base}/{self.oid}/api/v2/login",
            json={"username": self.user, "password": self.password},
            verify=self.verify,
            timeout=10,
        )
        d = self._json(r, "Omada login")
        self.token = (d.get("result") or {}).get("token")

        if not self.token:
            raise OmadaError("No token returned by Omada.")

    def _headers(self):
        if not self.token:
            raise OmadaError("Omada session is not authenticated.")
        return {"Csrf-Token": self.token}


    def get_live_stats(self):
        if not self.site:
            raise OmadaError("Site ID not configured.")

        if not self.token:
            self.login()

        r = self.s.get(
            f"{self.base}/{self.oid}/api/v2/sites/{self.site}/grid/devices",
            params={
                "token": self.token,
                "currentPage": 1,
                "currentPageSize": 100,
            },
            headers=self._headers(),
            verify=self.verify,
            timeout=10,
        )
        d = self._json(r, "Live device stats")
        result = d.get("result") or {}
        rows = result.get("data", []) if isinstance(result, dict) else []

        aps = [
            device
            for device in rows
            if str(device.get("type", "")).lower() == "ap"
        ]

        online_aps = [
            device
            for device in aps
            if device.get("statusCategory") == 1
            or device.get("status") == 14
        ]

        connected_clients = 0
        for device in online_aps:
            try:
                connected_clients += int(device.get("clientNum") or 0)
            except (TypeError, ValueError):
                pass

        return {
            "ap_total": len(aps),
            "ap_online": len(online_aps),
            "connected_clients": connected_clients,
            "aps": [
                {
                    "name": d.get("name"),
                    "model": d.get("model"),
                    "ip": d.get("ip"),
                    "clients": d.get("clientNum"),
                    "status": d.get("status"),
                    "status_category": d.get("statusCategory"),
                }
                for d in aps
            ],
        }


    def get_hotspot_clients(self):
        """Return hotspot/voucher sessions with one authenticated session."""
        if not self.site:
            raise OmadaError("Site ID not configured.")

        if not self.token:
            self.login()

        all_rows = []
        page = 1
        page_size = 100

        while page <= 5:
            r = self.s.get(
                f"{self.base}/{self.oid}/api/v2/hotspot/"
                f"sites/{self.site}/clients",
                params={
                    "token": self.token,
                    "currentPage": page,
                    "currentPageSize": page_size,
                },
                headers=self._headers(),
                verify=self.verify,
                timeout=10,
            )
            d = self._json(r, "Hotspot client status")
            result = d.get("result") or {}

            if isinstance(result, dict):
                rows = result.get("data") or result.get("rows") or []
                total_rows = int(result.get("totalRows") or len(rows))
            elif isinstance(result, list):
                rows = result
                total_rows = len(rows)
            else:
                rows = []
                total_rows = 0

            all_rows.extend(rows)

            if page * page_size >= total_rows or not rows:
                break
            page += 1

        return all_rows

    def get_hotspot_client_by_ip(self, client_ip):
        """Return the newest hotspot/voucher session matching a client IP."""
        matches = [
            row for row in self.get_hotspot_clients()
            if str(row.get("ip") or "").strip() == str(client_ip).strip()
        ]

        if not matches:
            return None

        # Prefer a currently valid session, then the newest end/start.
        matches.sort(
            key=lambda row: (
                1 if row.get("valid") else 0,
                int(row.get("end") or 0),
                int(row.get("start") or 0),
            ),
            reverse=True,
        )
        return matches[0]

    def create_vouchers(self, plan_name, minutes, amount=1):
        """Create 1-50 vouchers in one Omada voucher group/API request."""
        if not self.site or not self.rate:
            raise OmadaError("Site ID / Rate Limit ID not configured.")

        try:
            amount = int(amount)
        except (TypeError, ValueError):
            amount = 0
        if amount < 1 or amount > 50:
            raise OmadaError("Voucher amount must be between 1 and 50.")

        self.login()

        name = (
            f"AURA-{plan_name.replace(' ', '-')}-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )

        payload = {
            "amount": amount,
            "applyToAllPortals": True,
            "codeForm": [0, 1],
            "codeLength": 6,
            "description": f"Aura Web Admin - {plan_name}",
            "duration": int(minutes),
            "durationType": 1,
            "maxUsers": 1,
            "name": name,
            "rateLimitId": self.rate,
            "trafficLimit": None,
            "trafficLimitEnable": False,
            "type": 0,
            "upTimeLimitEnable": False,
            "voucherValidityEnable": False,
        }

        r = self.s.post(
            f"{self.base}/{self.oid}/api/v2/hotspot/"
            f"sites/{self.site}/voucherGroups",
            params={"token": self.token},
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            verify=self.verify,
            timeout=20,
        )
        d = self._json(r, "Create voucher group")
        gid = (d.get("result") or {}).get("id")

        if not gid:
            raise OmadaError("No group ID returned by Omada.")

        rows = self.get_vouchers_from_group(gid, expected=amount)
        if not rows:
            raise OmadaError("No voucher codes returned by Omada.")

        return [
            {
                "code": voucher["code"],
                "group_id": gid,
                "voucher_id": voucher.get("id"),
                "status": voucher.get("status"),
                "start_time": voucher.get("startTime"),
                "end_time": voucher.get("endTime"),
            }
            for voucher in rows
            if voucher.get("code")
        ]

    def create_voucher(self, plan_name, minutes):
        rows = self.create_vouchers(plan_name, minutes, 1)
        if not rows:
            raise OmadaError("No voucher code returned by Omada.")
        return rows[0]

    def get_vouchers_from_group(self, group_id, expected=None):
        if not self.site:
            raise OmadaError("Site ID not configured.")

        if not self.token:
            self.login()

        rows = []
        page = 1
        page_size = 100
        while page <= 3:
            r = self.s.get(
                f"{self.base}/{self.oid}/api/v2/hotspot/"
                f"sites/{self.site}/voucherGroups/{group_id}",
                params={
                    "token": self.token,
                    "currentPage": page,
                    "currentPageSize": page_size,
                },
                headers=self._headers(),
                verify=self.verify,
                timeout=10,
            )
            d = self._json(r, "Voucher group detail")
            result = d.get("result") or {}
            page_rows = result.get("data", []) or []
            rows.extend(page_rows)

            if expected and len(rows) >= int(expected):
                break
            total = int(result.get("totalRows") or len(rows))
            if not page_rows or page * page_size >= total:
                break
            page += 1

        return rows


    def get_voucher_from_group(self, group_id, code=None, voucher_id=None):
        rows = self.get_vouchers_from_group(group_id)

        if voucher_id:
            match = next(
                (v for v in rows if str(v.get("id")) == str(voucher_id)),
                None,
            )
            if match:
                return match

        if code:
            match = next((v for v in rows if v.get("code") == code), None)
            if match:
                return match

        return rows[0] if rows else None

