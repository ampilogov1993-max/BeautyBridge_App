import asyncio
import logging
import json
from playwright.async_api import async_playwright

class BOCRMManualAdapter:
    def __init__(self, email, password, branch_id):
        self.email = email
        self.password = password
        self.branch_id = branch_id

    def create_visit_sync(self, specialist_id, service_id, date_str, time_str, client_name, client_phone):
        return asyncio.run(self._async_create_visit(
            specialist_id, service_id, date_str, time_str, client_name, client_phone
        ))

    async def _async_create_visit(self, specialist_id, service_id, date_str, time_str, client_name, client_phone):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            context = await browser.new_context()

            try:
                page = await context.new_page()
                
                logging.info("Navigating to BOCRM...")
                await page.goto("https://my.binotel.ua/b/bocrm", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
                
                # Логин если нужен
                if await page.query_selector('input[type="password"]'):
                    logging.info("Logging in...")
                    await page.goto("https://my.binotel.ua/", wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(1000)
                    await page.fill('input[type="text"]:not([type="hidden"])', self.email)
                    await page.fill('input[type="password"]', self.password)
                    await page.click('button[type="submit"]')
                    await page.wait_for_timeout(5000)
                    await page.goto("https://my.binotel.ua/b/bocrm", wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)
                
                logging.info("Successfully logged in")
                
                # Нормализуем телефон
                phone_clean = client_phone.strip()
                if phone_clean.startswith('0'):
                    phone_clean = '38' + phone_clean
                
                logging.info(f"Phone: {phone_clean}")
                
                # Ищем клиента через page.request (передаст cookies автоматически)
                search_url = f"https://my.binotel.ua/b/bocrm/customer?page=1&search={phone_clean}"
                logging.info(f"Searching: {search_url}")
                
                search_res = await page.request.get(search_url)
                search_status = search_res.status
                logging.info(f"Search status: {search_status}")
                
                if search_status == 200:
                    search_data = await search_res.json()
                    logging.info(f"Search data: {search_data}")
                    
                    client_id = None
                    if search_data.get('data') and len(search_data['data']) > 0:
                        client_id = search_data['data'][0]['id']
                        logging.info(f"Found client: {client_id}")
                    else:
                        logging.info("No client found, creating...")
                        create_res = await page.request.post(
                            "https://my.binotel.ua/b/bocrm/customer",
                            data={"name": client_name, "phones": [phone_clean]}
                        )
                        logging.info(f"Create status: {create_res.status}")
                        create_data = await create_res.json()
                        logging.info(f"Create data: {create_data}")
                        
                        if create_data.get('data'):
                            client_id = create_data['data']['id']
                            logging.info(f"Created client: {client_id}")
                        else:
                            return {"ok": False, "message": "Failed to create client"}
                    
                    # Получаем услугу
                    services_res = await page.request.get("https://my.binotel.ua/b/bocrm/service")
                    if services_res.status == 200:
                        services_data = await services_res.json()
                        service_info = None
                        
                        if services_data.get('data'):
                            for svc in services_data['data']:
                                if str(svc.get('id')) == str(service_id):
                                    service_info = svc
                                    break
                        
                        if service_info:
                            logging.info(f"Service found: {service_info}")
                            
                            # Создаём визит
                            visit_payload = {
                                "branchId": self.branch_id,
                                "clientId": client_id,
                                "clientPhone": phone_clean,
                                "specialistId": specialist_id,
                                "resourceId": 1,
                                "time": f"{date_str} {time_str}:00",
                                "status": "isRecorded",
                                "color": 0,
                                "duration": service_info.get('duration', 60),
                                "force": 0,
                                "isFiscalized": False,
                                "isPaid": False,
                                "isPayment": False,
                                "onlinePayment": 0,
                                "cardPayment": 0,
                                "cashPayment": 0,
                                "cashbackPayment": 0,
                                "cashChange": 0,
                                "rounding": 0,
                                "subscriptionsPayment": 0,
                                "sum": service_info.get('price', 0),
                                "services": [
                                    {
                                        "id": service_info.get('id'),
                                        "uuid": service_info.get('uuid', ''),
                                        "name": service_info.get('name'),
                                        "price": service_info.get('price', 0),
                                        "duration": service_info.get('duration', 60),
                                        "isMinPrice": True,
                                        "priceDiff": 0,
                                        "priceDiffType": "service"
                                    }
                                ]
                            }
                            
                            logging.info(f"Visit payload: {visit_payload}")
                            visit_res = await page.request.post(
                                "https://my.binotel.ua/b/bocrm/visit",
                                data=visit_payload
                            )
                            logging.info(f"Visit status: {visit_res.status}")
                            visit_data = await visit_res.json()
                            logging.info(f"Visit response: {visit_data}")
                            
                            if visit_res.status == 200 or visit_res.status == 201:
                                return {"ok": True, "crm_id": visit_data.get("id")}
                            else:
                                return {"ok": False, "message": f"Visit error: {visit_data}"}
                        else:
                            return {"ok": False, "message": f"Service not found"}
                    else:
                        return {"ok": False, "message": f"Services fetch failed"}
                else:
                    return {"ok": False, "message": f"Search failed: {search_status}"}
            
            except Exception as e:
                logging.error(f"Exception: {e}", exc_info=True)
                return {"ok": False, "message": f"Error: {str(e)}"}
            finally:
                await browser.close()
