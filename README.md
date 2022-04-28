## For Skanestas

### Requirements:
    - Python 3.9

### How to run:
    - clone repo
    - create and activate env
    - pip install requirements.txt
    - hypercorn main:app --reload
    - Open http://127.0.0.1:8000

### About:
Реализация сервиса по генерации realtime данных.
Сервис должен возвращать раз в секунду цены для 100 искусственных торговых инструментов: `ticker_00`, `ticker_01`, `…`, `ticker_99`.
В качестве функции изменения цены для каждого инструмента предлагается использовать следующий код:

```python
from random import random

def generate_movement():
    movement = -1 if random() < 0.5 else 1
    return movement
```


Код выше моделирует изменение цены за один шаг времени.
[x] Цена каждого инструмента будет складываться кумулятивно из всех изменений.
[x] Цену в начальный момент времени принимаем равной нулю.

Реализация веб-сервиса по визуализации цены в режиме реального времени.
Необходимо вывести:
[x] Селектор инструмента в виде выпадающего списка
[x] График цены по выбранному инструменту от начального момента с добавлением данных по мере поступления.

### Notes:

    - No persisitent storage, all data will be lost after restart (no special requirements for this).
    - www.chartjs.org for charting lib.
    - Janus for threadsafe queues.
    - Logging with loguru.
    - API input validation with pydantic.

### Websocket API:
endpoint: `ws://127.0.0.1:8000/ws`

> Subscribing to ticker data:

```json
{
    "command": "subscribe",
    "ticker": "08"
}
```
> Example response, returning whole price history:
```json
["08", [{"time": 1651157955, "price": 0}, {"time": 1651157956, "price": -1}, {"time": 1651157957, "price": -2}, {"time": 1651157958, "price": -3}, {"time": 1651157959, "price": -4}, {"time": 1651157960, "price": -3}]]
```
With subsequent updates coming every second:
```json
["08", [{"time": 1651157961, "price": -4}]]
["08", [{"time": 1651157962, "price": -3}]]
["08", [{"time": 1651157963, "price": -4}]]
["08", [{"time": 1651157964, "price": -5}]]
["08", [{"time": 1651157965, "price": -4}]]
["08", [{"time": 1651157966, "price": -3}]]
```

> Unsbuscribing from ticker data:

```json
{
    "command": "unsubscribe",
    "ticker": "08"
}
```


