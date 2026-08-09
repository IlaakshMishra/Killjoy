class InMemoryOrderRepository:
    def __init__(self):
        self._prices: dict[str, float] = {}
        self._orders: dict[int, list[str]] = {}

    def add_item(self, item_id: str, price: float) -> None:
        self._prices[item_id] = price

    def add_order(self, order_id: int, item_ids: list[str]) -> None:
        self._orders[order_id] = item_ids

    def get_order(self, order_id: int) -> list[str] | None:
        return self._orders.get(order_id)

    def get_order_items(self, order_id: int) -> list[str]:
        return self._orders.get(order_id, [])

    def get_price(self, item_id: str) -> float:
        return self._prices[item_id]

    @staticmethod
    def get_page(items: list, page: int, page_size: int) -> list:
        start = page * page_size
        return items[start:page_size]
