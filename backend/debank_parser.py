"""
DeBank Protocol Parser
Извлекает тип протокола для заданного адреса кошелька с DeBank
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import sys


def get_protocol_type(wallet_address: str, timeout: int = 30000) -> dict:
    """
    Получает информацию о протоколе для указанного адреса кошелька

    Args:
        wallet_address: Адрес кошелька (например, 0x6bfce69d1df30fd2b2c8e478edec9daa643ae3b8)
        timeout: Максимальное время ожидания загрузки страницы в миллисекундах

    Returns:
        dict: Словарь с информацией {
            'address': адрес кошелька,
            'protocol': тип протокола,
            'balance': баланс (если доступен),
            'success': True/False
        }
    """
    url = f"https://debank.com/profile/{wallet_address}"
    result = {
        'address': wallet_address,
        'protocol': None,
        'balance': None,
        'success': False,
        'error': None
    }

    try:
        with sync_playwright() as p:
            # Запускаем браузер (headless режим для работы в фоне)
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()

            print(f"Загрузка страницы: {url}")
            page.goto(url, wait_until="networkidle", timeout=timeout)

            # Ждем загрузки основного контента
            page.wait_for_selector('.DesktopFrame_main__owMKN', timeout=10000)

            # Извлекаем Protocol
            try:
                protocol_element = page.locator('text=Protocol:').locator('..').locator('.db-user-tag-content')
                if protocol_element.count() > 0:
                    result['protocol'] = protocol_element.first.text_content().strip()
                    print(f"Найден протокол: {result['protocol']}")
            except Exception as e:
                print(f"Протокол не найден: {e}")

            # Извлекаем баланс
            try:
                balance_element = page.locator('text=/\\$[0-9,]+/').first
                if balance_element.count() > 0:
                    result['balance'] = balance_element.text_content().strip()
                    print(f"Баланс: {result['balance']}")
            except Exception as e:
                print(f"Баланс не найден: {e}")

            result['success'] = True
            browser.close()

    except PlaywrightTimeout:
        result['error'] = f"Timeout: страница не загрузилась за {timeout/1000} секунд"
        print(f"Ошибка: {result['error']}")
    except Exception as e:
        result['error'] = str(e)
        print(f"Ошибка: {result['error']}")

    return result


def main():
    """Основная функция для запуска из командной строки"""
    if len(sys.argv) < 2:
        print("Использование: python debank_parser.py <wallet_address>")
        print("Пример: python debank_parser.py 0x6bfce69d1df30fd2b2c8e478edec9daa643ae3b8")
        sys.exit(1)

    wallet_address = sys.argv[1]

    print(f"\n{'='*60}")
    print(f"DeBank Protocol Parser")
    print(f"{'='*60}\n")

    result = get_protocol_type(wallet_address)

    print(f"\n{'='*60}")
    print("Результат:")
    print(f"{'='*60}")
    print(f"Адрес: {result['address']}")
    print(f"Протокол: {result['protocol'] or 'Не найден'}")
    print(f"Баланс: {result['balance'] or 'Не найден'}")
    print(f"Статус: {'Успешно' if result['success'] else 'Ошибка'}")
    if result['error']:
        print(f"Ошибка: {result['error']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
