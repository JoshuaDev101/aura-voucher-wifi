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

    def create_voucher(self, plan_name, minutes):
        if not self.site or not self.rate:
            raise OmadaError("Site ID / Rate Limit ID not configured.")

        self.login()

        name = (
            f"AURA-{plan_name.replace(' ', '-')}-"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )

        payload = {
            "amount": 1,
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
            timeout=15,
        )
        d = self._json(r, "Create voucher group")
        gid = (d.get("result") or {}).get("id")

        if not gid:
            raise OmadaError("No group ID returned by Omada.")

        voucher = self.get_voucher_from_group(gid)

        if not voucher or not voucher.get("code"):
            raise OmadaError("No voucher code returned by Omada.")

        return {
            "code": voucher["code"],
            "group_id": gid,
            "voucher_id": voucher.get("id"),
            "status": voucher.get("status"),
            "start_time": voucher.get("startTime"),
            "end_time": voucher.get("endTime"),
        }

    def get_voucher_from_group(self, group_id, code=None, voucher_id=None):
        if not self.site:
            raise OmadaError("Site ID not configured.")

        if not self.token:
            self.login()

        r = self.s.get(
            f"{self.base}/{self.oid}/api/v2/hotspot/"
            f"sites/{self.site}/voucherGroups/{group_id}",
            params={
                "token": self.token,
                "currentPage": 1,
                "currentPageSize": 20,
            },
            headers=self._headers(),
            verify=self.verify,
            timeout=10,
        )
        d = self._json(r, "Voucher group detail")
        rows = (d.get("result") or {}).get("data", []) or []

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
