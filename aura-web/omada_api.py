import os
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OmadaError(RuntimeError):
    pass


class OmadaClient:
    def __init__(self):
        self.base_url = os.environ.get(
            "AURA_OMADA_URL", "https://192.168.1.124:8043"
        ).rstrip("/")
        self.username = os.environ.get("AURA_OMADA_USER", "")
        self.password = os.environ.get("AURA_OMADA_PASSWORD", "")
        self.verify_tls = os.environ.get("AURA_OMADA_VERIFY_TLS", "false").lower() == "true"

        self.session = requests.Session()
        self.omadac_id = None
        self.token = None

    def get_info(self):
        """Unauthenticated controller info endpoint.

        We will verify this against the live Omada 5.15 controller before
        depending on it for voucher generation.
        """
        try:
            r = self.session.get(
                f"{self.base_url}/api/info",
                timeout=5,
                verify=self.verify_tls,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise OmadaError(f"Controller unavailable: {exc}") from exc

        result = data.get("result") or data
        self.omadac_id = (
            result.get("omadacId")
            or result.get("omadac_id")
            or data.get("omadacId")
        )

        return {
            "raw": data,
            "version": result.get("controllerVer") or data.get("controllerVer"),
            "api_version": result.get("apiVer") or data.get("apiVer"),
            "omadac_id": self.omadac_id,
        }

    def probe(self):
        try:
            info = self.get_info()
            return {
                "online": True,
                "version": info.get("version") or "Unknown",
                "api_version": info.get("api_version") or "Unknown",
                "omadac_id": info.get("omadac_id") or "Unknown",
                "error": None,
            }
        except Exception as exc:
            return {
                "online": False,
                "version": "Unknown",
                "api_version": "Unknown",
                "omadac_id": "Unknown",
                "error": str(exc),
            }

    def login(self):
        """Prepared for the next milestone.

        Do not call this for real voucher creation until the live 5.15 login
        endpoint/response has been verified.
        """
        if not self.username or not self.password:
            raise OmadaError(
                "Set AURA_OMADA_USER and AURA_OMADA_PASSWORD locally first."
            )

        if not self.omadac_id:
            self.get_info()

        if not self.omadac_id:
            raise OmadaError("Could not discover Omada controller ID.")

        url = f"{self.base_url}/{self.omadac_id}/api/v2/login"
        try:
            r = self.session.post(
                url,
                json={"username": self.username, "password": self.password},
                timeout=8,
                verify=self.verify_tls,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise OmadaError(f"Omada login request failed: {exc}") from exc

        if data.get("errorCode") not in (0, None):
            raise OmadaError(data.get("msg") or f"Omada error {data.get('errorCode')}")

        result = data.get("result") or {}
        self.token = result.get("token")
        if not self.token:
            raise OmadaError("Login response did not contain a token.")

        return True
