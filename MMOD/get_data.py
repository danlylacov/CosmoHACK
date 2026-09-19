#!/usr/bin/env python3
"""
Получение всех сближений МКС (NORAD 25544) с космическими объектами.
Источник: CelesTrak SOCRATES Plus (регистрация не требуется)
"""

import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone


def fetch_iss_conjunctions(order="TCA", max_records=1000):
    """
    Запрашивает сближения МКС с CelesTrak SOCRATES.

    Args:
        order: TCA | MAXPROB | RELSPEED | MINRANGE
        max_records: Максимальное число записей

    Returns:
        Список словарей с данными о сближениях.
    """
    params = {
        "CATNR": "25544",       # NORAD ID МКС
        "ORDER": order,         # Сортировка
        "MAX": max_records,     # Лимит записей
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ISS-Conjunction-Fetcher/1.0)"
    }

    print(f"📡 Запрос к SOCRATES (CATNR=25544, ORDER={order})...")
    resp = None
    last_exc = None
    for host in ("celestrak.org", "celestrak.com"):
        url = f"https://{host}/SOCRATES/table-socrates.php"
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            last_exc = exc
            print(f"⚠️  {host} недоступен: {exc}")
            resp = None
    if resp is None:
        raise last_exc or RuntimeError("SOCRATES unavailable: no CelesTrak host responded")

    soup = BeautifulSoup(resp.text, "html.parser")

    # Ищем таблицу с результатами
    table = None
    for tbl in soup.find_all("table"):
        text = tbl.get_text()
        if "TCA" in text and "Min" in text:
            table = tbl
            break

    if table is None:
        print("⚠️  Таблица с результатами не найдена (возможно, сближений нет)")
        return []

    rows = table.find_all("tr")
    data_rows = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 6:
            data_rows.append([c.get_text(strip=True) for c in cells])

    # Каждое сближение описывается двумя строками: основной и вторичный объект
    events = []
    i = 0
    while i < len(data_rows) - 1:
        r1, r2 = data_rows[i], data_rows[i + 1]

        norad_1 = r1[1] if len(r1) > 1 else ""
        norad_2 = r2[1] if len(r2) > 1 else ""

        # NORAD ID должны быть числами
        if not (norad_1.isdigit() and norad_2.isdigit()):
            i += 1
            continue

        # Определяем, какая строка соответствует МКС
        if norad_1 == "25544":
            iss_row, other_row = r1, r2
        elif norad_2 == "25544":
            iss_row, other_row = r2, r1
        else:
            i += 2
            continue

        event = {
            "tca_utc": iss_row[4] if len(iss_row) > 4 else "",
            "min_range_km": iss_row[5] if len(iss_row) > 5 else "",
            "rel_speed_km_s": iss_row[6] if len(iss_row) > 6 else "",
            "iss_norad": "25544",
            "other_norad": other_row[1] if len(other_row) > 1 else "",
            "other_name": other_row[2] if len(other_row) > 2 else "",
            "max_probability": other_row[4] if len(other_row) > 4 else "",
            "dilution_km": other_row[5] if len(other_row) > 5 else "",
        }
        events.append(event)
        i += 2

    return events


if __name__ == "__main__":
    events = fetch_iss_conjunctions(order="TCA", max_records=1000)

    print(f"\n✅ Найдено сближений МКС: {len(events)}\n")
    print(f"{'TCA (UTC)':<26} {'NORAD':<10} {'Название':<20} "
          f"{'Дист. км':<10} {'Скорость км/с':<15} {'Вероятность'}")
    print("-" * 105)

    for e in events:
        print(
            f"{e['tca_utc']:<26} {e['other_norad']:<10} "
            f"{e['other_name'][:19]:<20} {e['min_range_km']:<10} "
            f"{e['rel_speed_km_s']:<15} {e['max_probability']}"
        )

    # Сохранение в JSON
    output = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "CelesTrak SOCRATES Plus",
        "url": "https://celestrak.org/SOCRATES/table-socrates.php?CATNR=25544",
        "count": len(events),
        "events": events,
    }
    with open("iss_conjunctions.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Результат сохранён: iss_conjunctions.json")