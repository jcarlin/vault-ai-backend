from fastapi import APIRouter, Request

from app.schemas.setup import (
    SetupAdminRequest,
    SetupAdminResponse,
    SetupCompleteResponse,
    SetupModelRequest,
    SetupNetworkRequest,
    SetupSsoRequest,
    SetupStatusResponse,
    SetupTlsRequest,
    SetupVerifyResponse,
)
from app.services.setup import SetupService

router = APIRouter()


@router.get("/vault/setup/status")
async def get_setup_status() -> SetupStatusResponse:
    service = SetupService()
    status = await service.get_status()
    return SetupStatusResponse(**status)


@router.post("/vault/setup/network")
async def configure_network(body: SetupNetworkRequest) -> dict:
    service = SetupService()
    result = await service.configure_network(
        hostname=body.hostname,
        ip_mode=body.ip_mode,
        ip_address=body.ip_address,
        subnet_mask=body.subnet_mask,
        gateway=body.gateway,
        dns_servers=body.dns_servers,
    )
    return result


@router.post("/vault/setup/admin", status_code=201)
async def create_admin(body: SetupAdminRequest) -> SetupAdminResponse:
    service = SetupService()
    result = await service.create_admin(name=body.name, email=body.email)
    return SetupAdminResponse(**result)


@router.post("/vault/setup/sso")
async def configure_sso(body: SetupSsoRequest) -> dict:
    service = SetupService()
    result = await service.configure_sso(
        enabled=body.enabled,
        url=body.url,
        bind_dn=body.bind_dn,
        bind_password=body.bind_password,
        user_search_base=body.user_search_base,
        group_search_base=body.group_search_base,
        user_search_filter=body.user_search_filter,
        use_ssl=body.use_ssl,
        test_connection=body.test_connection,
    )
    return result


@router.post("/vault/setup/sso/skip")
async def skip_sso() -> dict:
    service = SetupService()
    return await service.skip_sso()


@router.post("/vault/setup/tls")
async def configure_tls(body: SetupTlsRequest) -> dict:
    service = SetupService()
    result = await service.configure_tls(
        mode=body.mode,
        certificate=body.certificate,
        private_key=body.private_key,
    )
    return result


@router.post("/vault/setup/model")
async def select_model(body: SetupModelRequest) -> dict:
    service = SetupService()
    result = await service.select_model(model_id=body.model_id)
    return result


@router.get("/vault/setup/verify")
async def verify_setup(request: Request) -> SetupVerifyResponse:
    service = SetupService()
    backend = request.app.state.inference_backend
    result = await service.run_verification(inference_backend=backend)
    return SetupVerifyResponse(**result)


@router.post("/vault/setup/complete")
async def complete_setup(request: Request) -> SetupCompleteResponse:
    service = SetupService()
    result = await service.complete_setup()
    # Update in-memory flag so middleware locks out setup endpoints immediately
    request.app.state.setup_complete = True
    return SetupCompleteResponse(**result)
