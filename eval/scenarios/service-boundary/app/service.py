from app.repository import InMemoryOrderRepository

BULK_DISCOUNT_THRESHOLD = 5
BULK_DISCOUNT_RATE = 0.10


class OrderService:
    def __init__(self, repository: InMemoryOrderRepository):
        self._repository = repository

    def calculate_total(self, order_id: int) -> float:
        item_ids = self._repository.get_order_items(order_id)
        subtotal = sum(self._repository.get_price(item_id) for item_id in item_ids)
        if len(item_ids) > BULK_DISCOUNT_THRESHOLD:
            subtotal *= 1 - BULK_DISCOUNT_RATE
        return round(subtotal, 2)
