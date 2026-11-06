# Starter presets

Ready-to-run presets for common tasks. Load a folder, then name a preset on any call:

```python
from web_router import WebRouter

with WebRouter() as router:
    router.presets.load("presets/ecommerce")
    results = router.search("UPC 049000028911", preset="ecommerce.product-by-gtin@1.0.0")
```

Every setting a starter preset fixes can be overridden on the call, so treat each one as a starting point. Settings a preset leaves out work as usual.

## E-commerce

| Preset | Method | What it does |
| --- | --- | --- |
| `ecommerce.product-by-gtin@1.0.0` | `search` | Looks up a GTIN, UPC, EAN or barcode number in barcode databases and large retailers. Five results. |
| `ecommerce.coupons@1.0.0` | `search` | Finds current promo codes for a store or product on coupon sites. Ten results. |
| `ecommerce.product-by-name@1.0.0` | `search` | Finds where a product is sold from its name or model, leaving out social and video sites. Ten results. |
| `ecommerce.product-similarity@1.0.0` | `answer` | Compares two or more products and says how similar they are, with sources. |
| `ecommerce.shipping-info@1.0.0` | `answer`, `extract` | Answers questions about shipping times, costs and returns from recent sources, or reads a product page as Markdown. |

### Examples

What came back through `auto` routing in November 2026. Results change as the web does.

```python
router.search("UPC 049000028911", preset="ecommerce.product-by-gtin@1.0.0")
# upcitemdb.com: "UPC 049000028911 - Diet Coke - 12pk/12 fl oz Cans", then Amazon listings

router.search("Nike promo code", preset="ecommerce.coupons@1.0.0")
# promo-code pages from couponfollow.com, couponbirds.com, knoji.com, dealspotr.com and retailmenot.com

router.search("Sony WH-1000XM5 headphones price", preset="ecommerce.product-by-name@1.0.0")
# Sony's own store, Walmart, eBay and Verizon listings

router.answer(
    "How similar are the Sony WH-1000XM5 and Bose QuietComfort Ultra headphones? Compare the key specs.",
    preset="ecommerce.product-similarity@1.0.0",
)
# "They're very similar at the big-picture level..." with a spec table and its citations

router.answer(
    "What is the standard shipping time and cost at Best Buy for online orders in the US?",
    preset="ecommerce.shipping-info@1.0.0",
)
# delivery date and cost depend on the item and are shown at checkout; many orders ship free

router.extract(
    "https://shop.example.com/product/123",
    preset="ecommerce.shipping-info@1.0.0",
)
# the page as Markdown, including any shipping and returns section
```

Barcode lookups are only as good as the databases they search, which sometimes hold a wrong record for a number. Putting `UPC` or `EAN` before the number helps.
