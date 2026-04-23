from .bridge import OdooChatBridge
from .deployment import OdooDeployment
from .employee import OdooEmployee
from .product import OdooProduct
from .query import OdooQuery, OdooQueryVariable
from .sales_order import OdooSaleFactor, OdooSaleFactorProductRule, OdooSaleOrderTemplate

__all__ = [
    "OdooChatBridge",
    "OdooDeployment",
    "OdooEmployee",
    "OdooProduct",
    "OdooQuery",
    "OdooQueryVariable",
    "OdooSaleFactor",
    "OdooSaleFactorProductRule",
    "OdooSaleOrderTemplate",
]
