# transfer_order_to_ms.py
import base64
import http.client
from io import BytesIO
import json
import gzip
from datetime import datetime
import asyncio
from pathlib import Path
import re
from fastapi import APIRouter, FastAPI, Query
from urllib.parse import urlparse, quote
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from secret import moi_sklad_api_key
from globals import get_logger
from .. import get_lamoda_acces_token
from .db import create_order_log_db, update_order_stage_db, update_stage_7_db, update_ms_order_db

app = FastAPI()
router = APIRouter()

logger_transfer_order_to_ms = get_logger("transfer_order_to_ms", "module/fbs/order_create/transfer_order_to_ms.log", logs_dir="logs")

"""1. Получить токен Lamoda"""
"""Либо он уже есть (при запуске сервиса), либо его нужно получить"""

async def get_order_from_lamoda(order_id: str):
    """2. Получить заказ из Lamoda [GET]"""
    try:
        conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")
        payload = ''
        headers = {
            "Authorization": f"Bearer {get_lamoda_acces_token.access_token}"
        }

        conn.request("GET", f"/api/v2/orders/{order_id}?sellerId={get_lamoda_acces_token.seller_id}", payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if json_data.get("error", {}).get("code") == "UNAUTHORIZED":
            json_data = await unauthorized(f"/api/v2/orders/{order_id}?sellerId={get_lamoda_acces_token.seller_id}", payload, "GET", order_id)

        elif json_data.get("error", {}):
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 2. {order_id} при получении заказа из Lamoda: {json_data.get('error', {}).get('message')}")
            update_order_stage_db(order_id, "2_stage", "error")
            return None
            
        # Данные заказа
        name = json_data.get("id")
        created_at = json_data.get("createdAt")

        create_order_log_db(order_id, created_at)

        # Адрес / способ доставки
        delivery_method = json_data.get("deliveryMethod", {})
        address = delivery_method.get("shippingMethodName")

        # Время окончания доставки
        cut_off = delivery_method.get("cutOff")

        if cut_off:
            date = datetime.fromisoformat(cut_off.replace("Z", "+00:00"))
            end_time = date.strftime("%Y-%m-%d %H:%M:%S.000")
        else:
            end_time = ""

        # Данные товаров
        invent_number = {}
        code = {}
        price = {}

        items = json_data.get("items", [])

        for index, item in enumerate(items):
            invent_number[f"invent_number_{index}"] = item.get("id")
            code[f"code_{index}"] = item.get("externalSku")

            seller_agreed_price = item.get("sellerAgreedPrice", {})
            price[f"price_{index}"] = seller_agreed_price.get("amount")

        logger_transfer_order_to_ms.info(f"Используется токен Lamoda {name}: {get_lamoda_acces_token.access_token[:20]}...{get_lamoda_acces_token.access_token[-20:]}")
        logger_transfer_order_to_ms.info(f"Этап 2. Успех. {name} Получен из Lamoda")
        update_order_stage_db(name, "2_stage", "success")

        pack_number = None
        pack_image_base64 = None
        items_file_url = None
        items_base64 = None

        # 3. Сгенерировать номер пака
        await asyncio.sleep(.2)
        pack_number = await create_pack_number(name)

        # 4. Сгенерировать наклейку пака
        if pack_number is not None:
            await asyncio.sleep(.2)
            pack_image_base64 = await create_pack_image(pack_number, name)
        else:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 4. {order_id} Нельзя сгенерировать наклейку пака без номер пака")
            update_order_stage_db(name, "4_stage", "error")

        # 5. Сгенерировать наклейки товаров
        await asyncio.sleep(.2)
        items_file_url = await generate_labels(invent_number, name)

        # 5.1. Наклейки товаров в формат Base64
        await asyncio.sleep(.2)
        items_base64 = await labels_to_base64(items_file_url, name)

        result_lamoda = {
            "name": name,
            "address": address,
            "end_time": end_time,
            "invent_number": invent_number,
            "code": code,
            "price": price,
            "pack_number": pack_number,
            "pack_image_base64": f"{pack_image_base64[:20]}..." if pack_image_base64 else None,
            "items_file_url": f"{items_file_url[:20]}..." if items_file_url else None,
            "items_base64": f"{items_base64[:20]}..." if items_base64 else None
        }

        logger_transfer_order_to_ms.info(f"Результат LAMODA {name}: {result_lamoda}")

        # 6. Проверка наличия заказа в МС
        check_order_in_ms = await check_moysklad_order(name)

        if check_order_in_ms is None:
            return None
        else:
            # 7. Найти товары из заказа LM в МС
            await asyncio.sleep(.2)
            ms_variants = await check_moysklad_products(code, name)

        # 8. Создать заказ в МойСклад
        await asyncio.sleep(.2)
        created_order = await create_order_in_ms(
            name=name,
            address=address,
            end_time=end_time,
            invent_number=invent_number,
            price=price,
            pack_number=pack_number,
            ms_variants=ms_variants,
            items=items
        )
        if created_order is None:
            return None

        # 9. Добавить файлы наклеек в заказ МС
        order_id = created_order.get("id")

        if not order_id:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 9. {name} Не удалось получить ID созданного заказа в МойСклад.")
            return None

        files_added = await add_files_to_order_ms(name, order_id, pack_image_base64, items_base64)

        if files_added is None:
            return None

        return {
            "name": name,
            "address": address,
            "end_time": end_time,
            "invent_number": invent_number,
            "code": code,
            "price": price,
            "pack_number": pack_number,
            "pack_image_base64": pack_image_base64,
            "items_file_url": items_file_url,
            "items_base64": items_base64,
            "ms_variants": ms_variants,
            "items": items
        }

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 2. {order_id} при получении заказа из Lamoda: {e}")
        update_order_stage_db(order_id, "2_stage", "error")
        return None

async def create_pack_number(order_name: str):
    """3. Сгенерировать номер пака [POST]"""
    try:
        conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")
        payload = json.dumps({
        "sellerId": get_lamoda_acces_token.seller_id,
        "count": 1
        })
        headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {get_lamoda_acces_token.access_token}'
        }

        conn.request("POST", f"/api/v2/orders/{order_name}/packs", payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if json_data.get("error", {}).get("code") == "UNAUTHORIZED":
            json_data = await unauthorized(f"/api/v2/orders/{order_name}/packs", payload, "POST", order_name)

        elif json_data.get("error", {}):
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 3. {order_name} при генерации номера пака: {json_data.get('error', {})}")
            update_order_stage_db(order_name, "3_stage", "error")
            return None

        pack_number = json_data["data"][0]["packNumber"]

        logger_transfer_order_to_ms.info(f"Этап 3. Успех. {order_name} Сгенерирован номер пака {pack_number} для заказа {order_name}")
        update_order_stage_db(order_name, "3_stage", "success")
        return pack_number

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 3. {order_name} при генерации номера пака: {e}")
        update_order_stage_db(order_name, "3_stage", "error")
        return None

async def create_pack_image(pack_number: str, order_name: str):
    """4. Сгенерировать наклейку пака [GET]"""
    try:
        conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")
        payload = ''
        headers = {
        'Authorization': f'Bearer {get_lamoda_acces_token.access_token}'
        }
        
        conn.request("GET", f"/api/v2/orders/{order_name}/packs/{pack_number}/label?sellerId={get_lamoda_acces_token.seller_id}", payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        if res.status == 401:
            data = await unauthorized(f"/api/v2/orders/{order_name}/packs/{pack_number}/label?sellerId={get_lamoda_acces_token.seller_id}", payload, "GET", order_name)

        if isinstance(data, dict) and data.get("error", {}):
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 4. {order_name} при генерации наклейки пака: {data.get('error', {})}")
            update_order_stage_db(order_name, "4_stage", "error")
            return None
        
        pack_file_base64 = base64.b64encode(data).decode("utf-8")

        logger_transfer_order_to_ms.info(f"Этап 4. Успех. {order_name} Сгенерирована наклейка пака - base64: {pack_file_base64[:20]}...")
        update_order_stage_db(order_name, "4_stage", "success")
        return pack_file_base64
    
    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 4. {order_name} при генерации наклейки пака: {e}")
        update_order_stage_db(order_name, "4_stage", "error")
        return None

async def generate_labels(invent_number: dict, order_name: str):
    """5. Сгенерировать наклейки товаров [POST]"""
    try:
        conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")
        items = list(invent_number.values())
        payload = json.dumps({
            "sellerId": get_lamoda_acces_token.seller_id,
            "labelFormat": "S",
            "items": items
        })
        headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {get_lamoda_acces_token.access_token}'
        }

        conn.request("POST", "/api/v2/labels/order-items", payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if json_data.get("error", {}).get("code") == "UNAUTHORIZED":
            json_data = await unauthorized("/api/v2/labels/order-items", payload, "POST", order_name)

        elif json_data.get("error", {}):
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 5. {order_name} при генерации наклеек товаров: {json_data.get('error', {})}")
            update_order_stage_db(order_name, "5_stage", "error")
            return None

        items_file_url = json_data.get("data", {}).get("fileUrl")

        logger_transfer_order_to_ms.info(f"Этап 5. Успех. {order_name} Наклейки товаров сгенерированы для заказа. URL: {items_file_url}")
        update_order_stage_db(order_name, "5_stage", "success")
        
        return items_file_url

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 5. {order_name} при генерации наклеек товаров: {e}")
        update_order_stage_db(order_name, "5_stage", "error")
        return None

async def labels_to_base64(file_url: str, order_name: str):
    """5.1. Наклейки товаров в формат Base64 [GET]"""
    try:
        parsed_url = urlparse(file_url)
        conn = http.client.HTTPSConnection(parsed_url.netloc)
        path = parsed_url.path

        if parsed_url.query:
            path += f"?{parsed_url.query}"

        conn.request("GET", path, "", {})
        res = conn.getresponse()
        data = res.read()

        if res.status != 200:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 5.1. {order_name} при получении наклеек товаров: HTTP {res.status}, {res.reason}")
            update_order_stage_db(order_name, "5_1_stage", "error")
            return None

        labels_base64 = base64.b64encode(data).decode("ascii")

        logger_transfer_order_to_ms.info(f"Этап 5.1. Успех. {order_name} Наклейки товаров получены. Base64: {labels_base64[:20]}...")
        update_order_stage_db(order_name, "5_1_stage", "success")

        return labels_base64

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 5.1. {order_name} при получении наклеек товаров в Base64: {e}")
        update_order_stage_db(order_name, "5_1_stage", "error")
        return None

async def check_moysklad_order(order_name: str):
    """6. Проверка наличия заказа в МС"""
    try:
        conn = http.client.HTTPSConnection("api.moysklad.ru")
        payload = ''
        headers = {
            'Accept': 'application/json;charset=utf-8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Authorization': f'Bearer {moi_sklad_api_key}'
        }
        conn.request("GET", f"/api/remap/1.2/entity/customerorder?filter=name={order_name}", payload, headers)
        res = conn.getresponse()
        data = res.read()
        
        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if json_data.get("meta", {}).get("size") > 0:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 6. {order_name} уже существует в МойСклад.")
            update_order_stage_db(order_name, "6_stage", "error")
            return None
        else:
            logger_transfer_order_to_ms.info(f"Этап 6. {order_name} не найден в МойСклад. Можно создавать заказ.")
            update_order_stage_db(order_name, "6_stage", "success")
            return True

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка при получении заказа из МойСклад: {e}")
        update_order_stage_db(order_name, "6_stage", "error")
        return None

async def check_moysklad_products(code: dict, order_name: str):
    """7. Найти товары из заказа LM в МС"""
    try:
        codes = []
        index = 0

        while True:
            current_code = code.get(f"code_{index}")

            if current_code is None or current_code == "":
                break

            if current_code not in codes:
                codes.append(current_code)

            index += 1

        if not codes:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 7. {order_name} В заказе нет товаров.")
            return {}

        filter_value = ";".join(
            f"code={quote(current_code)}"
            for current_code in codes
        )

        logger_transfer_order_to_ms.info(
            f"Этап 7. {order_name} Поиск товаров в МойСклад. Коды: {codes}"
        )

        conn = http.client.HTTPSConnection("api.moysklad.ru")
        payload = ''
        headers = {
            'Authorization': f'Bearer {moi_sklad_api_key}',
            'Accept': 'application/json;charset=utf-8',
            'Accept-Encoding': 'gzip, deflate, br'
        }

        conn.request(
            "GET",
            f"/api/remap/1.2/entity/variant?filter={filter_value}",
            payload,
            headers
        )

        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        rows = json_data.get("rows", [])

        variants = {}

        for row in rows:
            variant_code = row.get("code")
            variant_id = row.get("id")

            if variant_code and variant_id:
                variants[variant_code] = variant_id

        result = {}
        stage_7_results = []

        for index in range(len(code)):
            current_code = code.get(f"code_{index}")

            if current_code is None or current_code == "":
                continue

            variant_id = variants.get(current_code)

            if variant_id:
                result[f"ms_variant_id_{index}"] = variant_id

                stage_7_results.append(f"{current_code} - success")

                logger_transfer_order_to_ms.info(f"Этап 7. {order_name} Найден товар: code_{index} = {current_code} -> ms_variant_id_{index} = https://online.moysklad.ru/app/#feature/edit?id={variant_id}")
            else:
                stage_7_results.append(f"{current_code} - error")

                logger_transfer_order_to_ms.error(f"Ошибка на этапе 7. {order_name} Не найден товар в МойСклад: code_{index} = {current_code}")

        success_count = sum(" - success" in item for item in stage_7_results)
        error_count = sum(" - error" in item for item in stage_7_results)

        if success_count > 0 and error_count == 0:
            stage_7_status = "success"
        elif success_count == 0 and error_count > 0:
            stage_7_status = "error"
        else:
            stage_7_status = "partial"

        stage_7_details = "\n".join(stage_7_results)

        update_stage_7_db(order_name, stage_7_details, stage_7_status)

        return result

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 7. {order_name} при поиске товаров в МойСклад: {e}")
        update_stage_7_db(order_name, f"Ошибка при поиске товаров: {e}", "error")
        return {}
        
async def create_order_in_ms(name: str, address: str, end_time: str, invent_number: dict, price: dict, pack_number: str, ms_variants: dict, items: list):
    """8. Создать заказ в МойСклад"""
    try:
        positions = []
        invent_numbers = []

        description_lines = []

        for index, item in enumerate(items):
            external_sku = item.get("externalSku", "")
            item_description = item.get("description", "")
            item_size = item.get("size", "")
            item_status = item.get("status", "")

            if item_size:
                item_name = f"{external_sku} {item_description} ({item_size})"
            else:
                item_name = f"{external_sku} {item_description}"

            if ms_variants.get(f"ms_variant_id_{index}"):
                description_lines.append(f"{item_name} - {item_status}")
            else:
                description_lines.append(f"{item_name} - Не найден в Мой Склад")

        description = "\n".join(description_lines)

        for index, item in enumerate(items):
            variant_id = ms_variants.get(f"ms_variant_id_{index}")

            if not variant_id:
                continue

            current_price = price.get(f"price_{index}")
            current_invent_number = invent_number.get(f"invent_number_{index}")

            if current_invent_number:
                invent_numbers.append(current_invent_number)

            positions.append({
                "quantity": 1,
                "price": int(current_price),
                "assortment": {
                    "meta": {
                        "href": f"https://api.moysklad.ru/api/remap/1.2/entity/variant/{variant_id}",
                        "type": "variant",
                        "mediaType": "application/json"
                    }
                }
            })

        attributes = [
            {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/ea1574fd-a3cb-11ee-0a80-0d1c001c2816",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "ea1574fd-a3cb-11ee-0a80-0d1c001c2816",
                "name": "Инвентарный номер",
                "type": "string",
                "value": ",".join(invent_numbers)
            },
            {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/137e0486-94ca-11ee-0a80-0f52003ec1c1",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "137e0486-94ca-11ee-0a80-0f52003ec1c1",
                "name": "Lamoda Status",
                "type": "string",
                "value": "CONFIRMED"
            },
            {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/29164cfa-e042-11ee-0a80-0407000774f2",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "29164cfa-e042-11ee-0a80-0407000774f2",
                "name": "В заказе несколько коробок (посылок)",
                "type": "customentity",
                "value": {
                    "meta": {
                        "href": "https://api.moysklad.ru/api/remap/1.2/entity/customentity/a5ae0618-999d-11ee-0a80-0029000bb59b/b656e887-999d-11ee-0a80-0029000bb85f",
                        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/customentity/a5ae0618-999d-11ee-0a80-0029000bb59b",
                        "type": "customentity",
                        "mediaType": "application/json",
                        "uuidHref": "https://online.moysklad.ru/app/#custom_a5ae0618-999d-11ee-0a80-0029000bb59b/edit?id=b656e887-999d-11ee-0a80-0029000bb85f"
                    },
                    "name": "Нет"
                }
            },
            {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/8303e514-c47e-11ef-0a80-01a80000a9d9",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "8303e514-c47e-11ef-0a80-01a80000a9d9",
                "name": "Количество коробок в заказе",
                "type": "long",
                "value": 1
            },
            {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/7bb19921-bc34-11f1-0a80-0fc80001789b",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "7bb19921-bc34-11f1-0a80-0fc80001789b",
                "name": "Отгрузка на склад Lamoda",
                "type": "time",
                "value": end_time
            }
        ]

        if pack_number:
            attributes.insert(1, {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/e9b49018-a3cb-11ee-0a80-0e2f001c9d23",
                    "type": "attributemetadata",
                    "mediaType": "application/json"
                },
                "id": "e9b49018-a3cb-11ee-0a80-0e2f001c9d23",
                "name": "Штрихкод посылки",
                "type": "string",
                "value": pack_number
            })

        order = {
            "name": name,
            "description": description,

            "organization": {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/organization/68db9c1e-4f29-11ea-0a80-03be00095ea6",
                    "type": "organization",
                    "mediaType": "application/json"
                }
            },

            "agent": {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/af00d06a-e0ab-11ee-0a80-07450001d344",
                    "type": "counterparty",
                    "mediaType": "application/json"
                }
            },

            "store": {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/store/f488a822-fe17-11ee-0a80-1480000613d5",
                    "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/store/metadata",
                    "type": "store",
                    "mediaType": "application/json",
                    "uuidHref": "https://online.moysklad.ru/app/#warehouse/edit?id=f488a822-fe17-11ee-0a80-1480000613d5"
                }
            },

            "state": {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/states/fad77271-89e6-11ee-0a80-05a9004c036d",
                    "type": "state",
                    "mediaType": "application/json"
                }
            },

            "salesChannel": {
                "meta": {
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/saleschannel/497e2798-8787-11ee-0a80-0fbd00387301",
                    "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/saleschannel/metadata",
                    "type": "saleschannel",
                    "mediaType": "application/json",
                    "uuidHref": "https://online.moysklad.ru/app/#saleschannel/edit?id=497e2798-8787-11ee-0a80-0fbd00387301"
                }
            },

            "attributes": attributes,

            "shipmentAddress": address,

            "shipmentAddressFull": {
                "city": address
            },

            "positions": positions
        }

        payload = json.dumps(order)

        headers = {
            "Accept": "application/json;charset=utf-8",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {moi_sklad_api_key}"
        }

        conn = http.client.HTTPSConnection("api.moysklad.ru")

        conn.request("POST", "/api/remap/1.2/entity/customerorder", payload, headers)

        res = conn.getresponse()
        data = res.read()

        if res.getheader("Content-Encoding") == "gzip":
            data = gzip.decompress(data)

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if res.status < 200 or res.status >= 300:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 8. {name} МойСклад HTTP {res.status}: {json_data}")
            update_order_stage_db(name, "8_stage", "error")
            return None

        logger_transfer_order_to_ms.info(f"Этап 8. Успех. {name} Заказ создан в МойСклад: https://online.moysklad.ru/app/#customerorder/edit?id={json_data.get('id')}")
        update_order_stage_db(name, "8_stage", "success")
        update_ms_order_db(name, f"https://online.moysklad.ru/app/#customerorder/edit?id={json_data.get('id')}")

        
        return json_data

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 8. {name} при создании заказа в МойСклад: {e}")
        update_order_stage_db(name, "8_stage", "error")
        return None

async def add_files_to_order_ms(name: str, id: str, pack_image_base64: str, items_base64: str):
    """9. Добавить файлы наклеек в заказ МС"""
    try:
        conn = http.client.HTTPSConnection("api.moysklad.ru")

        files = []

        if pack_image_base64:
            files.append({
                "filename": f"{name}_4forms_packs.pdf",
                "content": pack_image_base64
            })

        if items_base64:
            files.append({
                "filename": f"{name}_4forms_items.pdf",
                "content": items_base64
            })

        if not files:
            logger_transfer_order_to_ms.info(f"Этап 9. {name} Нет файлов наклеек для добавления в заказ.")
            return True

        payload = json.dumps(files)

        headers = {
        'Accept': 'application/json;charset=utf-8',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip, deflate, br',
        'Authorization': f'Bearer {moi_sklad_api_key}'
        }
        conn.request("POST", f"/api/remap/1.2/entity/customerorder/{id}/files", payload, headers)
        res = conn.getresponse()
        data = res.read()

        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)

        if res.status < 200 or res.status >= 300:
            logger_transfer_order_to_ms.error(f"Ошибка на этапе 9. {name} При добавлении файлов в заказ в МойСклад HTTP {res.status}: {json_data}")
            update_order_stage_db(name, "9_stage", "error")
            return None

        logger_transfer_order_to_ms.info(f"Этап 9. Успех. {name} Файлы добавлены в заказ в МойСклад.")
        update_order_stage_db(name, "9_stage", "success")

        return json_data

    except Exception as e:
        logger_transfer_order_to_ms.error(f"Ошибка на этапе 9. {name} При добавлении файлов в заказ в МойСклад: {e}")
        update_order_stage_db(name, "9_stage", "error")
        return None

#----------------------------------------------------------------

async def unauthorized(api_url: str, payload: str, response: str, order_name: str):
    """Обновляет токен и повторяет запрос при получении ошибки UNAUTHORIZED"""
    await get_lamoda_acces_token.get_access_token()

    conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {get_lamoda_acces_token.access_token}'
    }
    logger_transfer_order_to_ms.info(f"UNAUTHORIZED - NEW TOKEN {order_name}: {get_lamoda_acces_token.access_token[:20]}...{get_lamoda_acces_token.access_token[-20:]}")

    conn.request(response, api_url, payload, headers)
    res = conn.getresponse()
    data = res.read()

    if res.getheader('Content-Encoding') == 'gzip':
        buf = BytesIO(data)
        data = gzip.decompress(buf.read())

    json_string = data.decode("utf-8")
    json_data = json.loads(json_string)

    return json_data

#----------------------------------------------------------------

# /module/fbs/order_create/manual_transfer?order_id=order_id
@router.post("/module/fbs/order_create/manual_transfer")
async def manual_pooling(order_id: str = Query(...)):
    """Ручной выгрузка заказоа"""
    result = await get_order_from_lamoda(order_id)
    return result if result else {"message": "Ошибка при выгрузке заказа"}

@router.get("/module/fbs/order_create/transfer_order_to_ms/logs_json")
async def read_logs():
    """Логи grafana"""
    log_path = Path("logs/module/fbs/order_create/transfer_order_to_ms.log")
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        logs = []

        for line in content.splitlines():
            match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[(INFO|WARNING|ERROR)\] (.*)$", line)
            if match:
                logs.append({"time": match.group(1), "level": match.group(2), "message": match.group(3)})

        logs.reverse()

        return JSONResponse(logs)
    else:
        return JSONResponse({"error": "Лог-файл не найден"}, status_code=404)

@router.get("/module/fbs/order_create/transfer_order_to_ms/logs")
async def read_logs():
    """Логи"""
    log_path = Path("logs/module/fbs/order_create/transfer_order_to_ms.log")
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        return PlainTextResponse(content)
    else:
        return PlainTextResponse("Лог-файл не найден", status_code=404)
    