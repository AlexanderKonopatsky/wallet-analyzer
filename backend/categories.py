"""
Модуль для работы с категориями кошельков.
Управляет файлом data/categories.json.
"""

import json
import os
from typing import Dict, List, Optional
from pathlib import Path
import uuid

# Path relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

def get_categories_file(user_id: int) -> Path:
    """Get user-specific categories file path."""
    user_dir = DATA_DIR / "users" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "categories.json"

def ensure_categories_file(user_id: int):
    """Создаёт файл категорий с дефолтной структурой, если его нет."""
    categories_file = get_categories_file(user_id)

    if not categories_file.exists():
        default_data = {
            "categories": [],
            "walletCategories": {}
        }
        with open(categories_file, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)

def load_categories(user_id: int) -> Dict:
    """Загружает данные о категориях из файла пользователя."""
    ensure_categories_file(user_id)
    categories_file = get_categories_file(user_id)

    with open(categories_file, "r", encoding="utf-8") as f:
        return json.load(f)

def save_categories(user_id: int, data: Dict):
    """Сохраняет данные о категориях в файл пользователя."""
    ensure_categories_file(user_id)
    categories_file = get_categories_file(user_id)

    with open(categories_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_all_categories(user_id: int) -> List[Dict]:
    """Возвращает список всех категорий пользователя."""
    data = load_categories(user_id)
    return data.get("categories", [])

def get_category_by_id(user_id: int, category_id: str) -> Optional[Dict]:
    """Возвращает категорию по ID для пользователя."""
    categories = get_all_categories(user_id)
    for category in categories:
        if category["id"] == category_id:
            return category
    return None

def create_category(user_id: int, name: str, color: str = "#3b82f6") -> Dict:
    """
    Создаёт новую категорию для пользователя.

    Args:
        user_id: ID пользователя
        name: Название категории
        color: Цвет в формате hex (по умолчанию синий)

    Returns:
        Созданная категория
    """
    data = load_categories(user_id)

    new_category = {
        "id": str(uuid.uuid4()),
        "name": name,
        "color": color,
        "expanded": True
    }

    data["categories"].append(new_category)
    save_categories(user_id, data)

    return new_category

def update_category(user_id: int, category_id: str, name: Optional[str] = None,
                   color: Optional[str] = None, expanded: Optional[bool] = None) -> Optional[Dict]:
    """
    Обновляет категорию пользователя.

    Args:
        user_id: ID пользователя
        category_id: ID категории
        name: Новое название (опционально)
        color: Новый цвет (опционально)
        expanded: Состояние сворачивания (опционально)

    Returns:
        Обновлённая категория или None, если не найдена
    """
    data = load_categories(user_id)

    for i, category in enumerate(data["categories"]):
        if category["id"] == category_id:
            if name is not None:
                category["name"] = name
            if color is not None:
                category["color"] = color
            if expanded is not None:
                category["expanded"] = expanded

            data["categories"][i] = category
            save_categories(user_id, data)
            return category

    return None

def delete_category(user_id: int, category_id: str) -> bool:
    """
    Удаляет категорию пользователя и убирает её у всех кошельков.

    Args:
        user_id: ID пользователя
        category_id: ID категории для удаления

    Returns:
        True если категория была удалена, False если не найдена
    """
    data = load_categories(user_id)

    # Удаляем категорию из списка
    initial_len = len(data["categories"])
    data["categories"] = [c for c in data["categories"] if c["id"] != category_id]

    if len(data["categories"]) == initial_len:
        return False  # Категория не найдена

    # Убираем категорию у всех кошельков
    for wallet in data["walletCategories"]:
        if data["walletCategories"][wallet] == category_id:
            data["walletCategories"][wallet] = None

    save_categories(user_id, data)
    return True

def get_wallet_category(user_id: int, wallet_address: str) -> Optional[str]:
    """
    Возвращает ID категории кошелька для пользователя.

    Args:
        user_id: ID пользователя
        wallet_address: Адрес кошелька

    Returns:
        ID категории или None если кошелёк без категории
    """
    data = load_categories(user_id)
    return data.get("walletCategories", {}).get(wallet_address)

def set_wallet_category(user_id: int, wallet_address: str, category_id: Optional[str]) -> bool:
    """
    Устанавливает категорию для кошелька пользователя.

    Args:
        user_id: ID пользователя
        wallet_address: Адрес кошелька
        category_id: ID категории или None чтобы убрать категорию

    Returns:
        True если успешно, False если категория не существует
    """
    data = load_categories(user_id)

    # Проверяем, что категория существует (если не None)
    if category_id is not None:
        if not get_category_by_id(user_id, category_id):
            return False

    data["walletCategories"][wallet_address] = category_id
    save_categories(user_id, data)
    return True

def get_wallets_by_category(user_id: int, category_id: Optional[str] = None) -> List[str]:
    """
    Возвращает список адресов кошельков в категории для пользователя.

    Args:
        user_id: ID пользователя
        category_id: ID категории или None для кошельков без категории

    Returns:
        Список адресов кошельков
    """
    data = load_categories(user_id)
    wallet_categories = data.get("walletCategories", {})

    return [
        wallet for wallet, cat_id in wallet_categories.items()
        if cat_id == category_id
    ]

def get_category_stats(user_id: int) -> Dict[str, int]:
    """
    Возвращает статистику: количество кошельков в каждой категории для пользователя.

    Args:
        user_id: ID пользователя

    Returns:
        Словарь {category_id: count}
    """
    data = load_categories(user_id)
    wallet_categories = data.get("walletCategories", {})

    stats = {}
    for category in data["categories"]:
        cat_id = category["id"]
        stats[cat_id] = sum(1 for c in wallet_categories.values() if c == cat_id)

    # Добавляем счётчик для кошельков без категории
    stats["uncategorized"] = sum(1 for c in wallet_categories.values() if c is None)

    return stats
