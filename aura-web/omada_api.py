import os, requests, urllib3
from datetime import datetime
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class OmadaError(RuntimeError): pass

class OmadaClient:
    def __init__(self):
        self.base=os.environ.get("AURA_OMADA_URL","https://127.0.0.1:8043").rstrip("/")
        self.user=os.environ.get("AURA_OMADA_USER","")
        self.password=os.environ.get("AURA_OMADA_PASSWORD","")
        self.site=os.environ.get("AURA_OMADA_SITE_ID","")
        self.rate=os.environ.get("AURA_OMADA_RATE_LIMIT_ID","")
        self.verify=os.environ.get("AURA_OMADA_VERIFY_TLS","false").lower()=="true"
        self.s=requests.Session(); self.oid=None; self.token=None

    def info(self):
        r=self.s.get(self.base+"/api/info",verify=self.verify,timeout=5); r.raise_for_status()
        d=r.json(); x=d.get("result") or d
        self.oid=x.get("omadacId") or d.get("omadacId")
        return {"version":x.get("controllerVer"),"api_version":x.get("apiVer"),"omadac_id":self.oid}

    def probe(self):
        try:
            x=self.info()
            return {"online":True,"error":None,**x}
        except Exception as e:
            return {"online":False,"error":str(e),"version":"Unknown","api_version":"Unknown","omadac_id":"Unknown"}

    def login(self):
        if not self.user or not self.password: raise OmadaError("Omada credentials not configured.")
        if not self.oid: self.info()
        r=self.s.post(f"{self.base}/{self.oid}/api/v2/login",
            json={"username":self.user,"password":self.password},verify=self.verify,timeout=10)
        r.raise_for_status(); d=r.json()
        if d.get("errorCode")!=0: raise OmadaError(d.get("msg","Login failed"))
        self.token=(d.get("result") or {}).get("token")
        if not self.token: raise OmadaError("No token returned.")

    def create_voucher(self, plan_name, minutes):
        if not self.site or not self.rate: raise OmadaError("Site ID / Rate Limit ID not configured.")
        self.login()
        headers={"Csrf-Token":self.token,"Content-Type":"application/json"}
        name=f"AURA-{plan_name.replace(' ','-')}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        payload={"amount":1,"applyToAllPortals":True,"codeForm":[0,1],"codeLength":6,
          "description":f"Aura Web Admin - {plan_name}","duration":int(minutes),"durationType":1,
          "maxUsers":1,"name":name,"rateLimitId":self.rate,"trafficLimit":None,
          "trafficLimitEnable":False,"type":0,"upTimeLimitEnable":False,"voucherValidityEnable":False}
        r=self.s.post(f"{self.base}/{self.oid}/api/v2/hotspot/sites/{self.site}/voucherGroups",
          params={"token":self.token},headers=headers,json=payload,verify=self.verify,timeout=15)
        r.raise_for_status(); d=r.json()
        if d.get("errorCode")!=0: raise OmadaError(d.get("msg","Voucher create failed"))
        gid=(d.get("result") or {}).get("id")
        if not gid: raise OmadaError("No group ID returned.")
        r=self.s.get(f"{self.base}/{self.oid}/api/v2/hotspot/sites/{self.site}/voucherGroups/{gid}",
          params={"token":self.token,"currentPage":1,"currentPageSize":20},
          headers={"Csrf-Token":self.token},verify=self.verify,timeout=10)
        r.raise_for_status(); detail=r.json()
        rows=(detail.get("result") or {}).get("data",[]) or []
        if not rows or not rows[0].get("code"): raise OmadaError("No voucher code returned.")
        v=rows[0]
        return {"code":v["code"],"group_id":gid,"voucher_id":v.get("id"),"status":v.get("status"),
                "start_time":v.get("startTime"),"end_time":v.get("endTime")}
