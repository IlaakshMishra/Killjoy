from app.service import OrderService


def handle_get_order(service: OrderService, order_id: int) -> dict:
    total = service.calculate_total(order_id)
    return {"order_id": order_id, "total": total}
