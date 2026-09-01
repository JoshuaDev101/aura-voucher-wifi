"""Omada 5.15 adapter placeholder.
Live 5.15 API must be captured/verified before enabling voucher generation.
"""

class OmadaNotConfigured(RuntimeError):
    pass

def create_voucher(*args, **kwargs):
    raise OmadaNotConfigured("Omada 5.15 API has not been captured/verified yet.")
