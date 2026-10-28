from fastapi import APIRouter

module_router = APIRouter()

# Вебхук изменения статуса заказа
from module.fbs.webhook_order_status_changed import router as webhook_order_status_changed_router
module_router.include_router(webhook_order_status_changed_router)

# Заказ из Ламоды в Мой Склад
from module.fbs.order_create.transfer_order_to_ms import router as transfer_order_to_ms_logs_router
module_router.include_router(transfer_order_to_ms_logs_router)
