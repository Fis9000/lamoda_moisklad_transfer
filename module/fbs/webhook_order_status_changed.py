# webhook_order_status_changed.py
from pathlib import Path
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from globals import GlobalConfig
from globals import get_logger
from .order_create.transfer_order_to_ms import get_order_from_lamoda


logger_orders_create = get_logger("webhook_create", "module/fbs/webhook_orders_create.log", logs_dir="logs")
logger_order_status_changed = get_logger("webhook_status_changed", "module/fbs/webhook_order_status_changed.log", logs_dir="logs")
logger_transfer_order_to_ms = get_logger("transfer_order_to_ms", "module/fbs/order_create/transfer_order_to_ms.log", logs_dir="logs")

router = APIRouter()
app = FastAPI()

@router.get("/webhook/order_status_changed")
def get_jsonData():
    return GlobalConfig.status_string

@router.post("/webhook/order_status_changed")
async def create_json_data(request: Request):
    try:
        body = await request.json()

        event_type = body.get("type")
        tracking_id = body.get("trackingId")
        sequence_number = body.get("sequenceNumber")

        data = body.get("data", {})

        order_id = data.get("id")
        status = data.get("status")

        logger_order_status_changed.info(
            f"Lamoda event={event_type}, "
            f"trackingId={tracking_id}, "
            f"sequenceNumber={sequence_number}, "
            f"order_id={order_id}, "
            f"status={status}"
        )

        """Новый заказ"""
        if status == "Confirmed":
            logger_order_status_changed.info(f"Новый заказ {order_id} ")
            logger_orders_create.info(f"Новый заказ {order_id} ")
            logger_transfer_order_to_ms.warning(f"▫︎" * 50)
            logger_transfer_order_to_ms.info(f"Из вебхука поступил заказ {order_id} ")
            await get_order_from_lamoda(order_id)

        return GlobalConfig.status_string

    except Exception as e:
        logger_order_status_changed.error(f"Ошибка обработки webhook: {e}")

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e)
            }
        )

@router.get(f"/webhook/order_status_changed/logs", response_class=PlainTextResponse) 
async def read_logs():
    """Логи"""
    log_path = Path("logs/module/fbs/webhook_order_status_changed.log")
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        return PlainTextResponse(content)
    else:
        return PlainTextResponse("Лог-файл не найден", status_code=404)