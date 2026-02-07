"""
Модуль для работы с категориями кошельков.
Управляет файлом data/categories.json.
"""

import json
import os
from typing import Dict, List, Optional
from pathlib import Path
import uuid

CATEGORIES_FILE = "data/categories.json"

def ensure_categories_file():
    """Создаёт файл категорий с дефолтной структурой, если его нет."""
    os.makedirs("data", exist_ok=True)

    if not os.path.exists(CATEGORIES_FILE):
        default_data = {
            "categories": [],
            "walletCategories": {}
        }
        with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)

def load_categories() -> Dict:
    """Загружает данные о категориях из файла."""
    ensure_categories_file()

    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_categories(data: Dict):
    """Сохраняет данные о категориях в файл."""
    ensure_categories_file()

    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_all_categories() -> List[Dict]:
    """Возвращает список всех категорий."""
    data = load_categories()
    return data.get("categories", [])

def get_category_by_id(category_id: str) -> Optional[Dict]:
    """Возвращает категорию по ID."""
    categories = get_all_categories()
    for category in categories:
        if category["id"] == category_id:
            return category
    return None

def create_category(name: str, color: str = "#3b82f6") -> Dict:
    """
    Создаёт новую категорию.

    Args:
        name: Название категории
        color: Цвет в формате hex (по умолчанию синий)

    Returns:
        Созданная категория
    """
    data = load_categories()

    new_category = {
        "id": str(uuid.uuid4()),
        "name": name,
        "color": color,
        "expanded": True
    }

    data["categories"].append(new_category)
    save_categories(data)

    return new_category

def update_category(category_id: str, name: Optional[str] = None,
                   color: Optional[str] = None, expanded: Optional[bool] = None) -> Optional[Dict]:
    """
    Обновляет категорию.

    Args:
        category_id: ID категории
        name: Новое название (опционально)
        color: Новый цвет (опционально)
        expanded: Состояние сворачивания (опционально)

    Returns:
        Обновлённая категория или None, если не найдена
    """
    data = load_categories()

    for i, category in enumerate(data["categories"]):
        if category["id"] == category_id:
            if name is not None:
                category["name"] = name
            if color is not None:
                category["color"] = color
            if expanded is not None:
                category["expanded"] = expanded

            data["categories"][i] = category
            save_categories(data)
            return category

    return None

def delete_category(category_id: str) -> bool:
    """
    Удаляет категорию и убирает её у всех кошельков.

    Args:
        category_id: ID категории для удаления

    Returns:
        True если категория была удалена, False если не найдена
    """
    data = load_categories()

    # Удаляем категорию из списка
    initial_len = len(data["categories"])
    data["categories"] = [c for c in data["categories"] if c["id"] != category_id]

    if len(data["categories"]) == initial_len:
        return False  # Категория не найдена

    # Убираем категорию у всех кошельков
    for wallet in data["walletCategories"]:
        if data["walletCategories"][wallet] == category_id:
            data["walletCategories"][wallet] = None

    save_categories(data)
    return True

def get_wallet_category(wallet_address: str) -> Optional[str]:
    """
    Возвращает ID категории кошелька.

    Args:
        wallet_address: Адрес кошелька

    Returns:
        ID категории или None если кошелёк без категории
    """
    data = load_categories()
    return data.get("walletCategories", {}).get(wallet_address)

def set_wallet_category(wallet_address: str, category_id: Optional[str]) -> bool:
    """
    Устанавливает категорию для кошелька.

    Args:
        wallet_address: Адрес кошелька
        category_id: ID категории или None чтобы убрать категорию

    Returns:
        True если успешно, False если категория не существует
    """
    data = load_categories()

    # Проверяем, что категория существует (если не None)
    if category_id is not None:
        if not get_category_by_id(category_id):
            return False

    data["walletCategories"][wallet_address] = category_id
    save_categories(data)
    return True

def get_wallets_by_category(category_id: Optional[str] = None) -> List[str]:
    """
    Возвращает список адресов кошельков в категории.

    Args:
        category_id: ID категории или None для кошельков без категории

    Returns:
        Список адресов кошельков
    """
    data = load_categories()
    wallet_categories = data.get("walletCategories", {})

    return [
        wallet for wallet, cat_id in wallet_categories.items()
        if cat_id == category_id
    ]

def get_category_stats() -> Dict[str, int]:
    """
    Возвращает статистику: количество кошельков в каждой категории.

    Returns:
        Словарь {category_id: count}
    """
    data = load_categories()
    wallet_categories = data.get("walletCategories", {})

    stats = {}
    for category in data["categories"]:
        cat_id = category["id"]
        stats[cat_id] = sum(1 for c in wallet_categories.values() if c == cat_id)

    # Добавляем счётчик для кошельков без категории
    stats["uncategorized"] = sum(1 for c in wallet_categories.values() if c is None)

    return stats
