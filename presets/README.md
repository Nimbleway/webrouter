# Starter presets

Presets for common tasks, to use as they are or copy and change. Load a folder, then name a preset on any call:

```python
from web_router import WebRouter

router = WebRouter()  # auto, for search and answers
router.presets.load("presets/ecommerce")  # relative to the root of the clone
results = router.search("UPC 049000028911", preset="ecommerce.product-by-gtin@1.0.0")
```

Every setting a starter preset fixes can be overridden on the call, so treat each one as a starting point. Settings a preset leaves out work as usual.

## Which backends serve them

A preset's settings reach the backend like the same arguments would, so each preset needs a backend that maps them. Any other backend refuses the call before it bills: `UnsupportedParameterError` for a setting it doesn't map, `UnsupportedFormatError` for a format it can't return.

- The three `search` presets set `include_domains` or `exclude_domains`. Nimble, Tavily, You.com, Brave, Exa, Parallel, Perplexity, Linkup, WebIQ and AWS AgentCore Web Search map them, and so do You.com MCP and Exa MCP. With no key, `auto` serves them through Tavily's keyless tier, then You.com MCP and Exa MCP. AgentCore maps them only on a gateway target with connector 1.2.0 or later. DeepSeek maps the lists too but writes them into the query as `site:` operators, and each list here is longer than its 128-character query limit, so it refuses the call with `ProviderError` before sending it. Keenable takes one `include_domains` entry at most and no `exclude_domains`, and Parallel MCP maps neither.
- The two `answer` presets set `exclude_domains` or `recency`. Linkup, Perplexity and You.com map them. Perplexity and You.com take one domain list, so on them `ecommerce.product-similarity` can't be combined with `include_domains`.
- The `extract` part of `ecommerce.shipping-info` sets only `format`, so any extract backend that returns Markdown serves it.

A call can replace a preset's domain list with its own. Passing `[]` replaces it with an empty one, which still counts as set, so a backend that doesn't map the setting refuses it. Passing `None` leaves the preset's list in place. To search without the preset's domains, or on a backend that doesn't map them, call without the preset.

## E-commerce

| Preset | Method | What it sets |
| --- | --- | --- |
| `ecommerce.product-by-gtin@1.0.0` | `search` | Searches only barcode databases and large retailers, for a GTIN, UPC, EAN or barcode number. Up to five results. |
| `ecommerce.coupons@1.0.0` | `search` | Searches only coupon sites, for a store's or product's promo codes. Up to ten results. |
| `ecommerce.product-by-name@1.0.0` | `search` | Leaves out social, video and forum sites, for finding where a product is sold. Up to ten results. |
| `ecommerce.product-similarity@1.0.0` | `answer` | Leaves out Pinterest, Facebook, TikTok and Instagram as sources, for comparing products. |
| `ecommerce.shipping-info@1.0.0` | `answer`, `extract` | Answers only from sources of the past year, for shipping and returns questions. Reads a page as Markdown. |

### Examples

What came back through `auto` routing in November 2026, on the router above with keys for the backends above. The `answer()` examples need a Linkup, Perplexity or You.com key, since `auto` only tries configured answer backends. Results change as the web does.

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
```

To read a product page with the same preset, put the page's URL in place of this placeholder:

```python
router.extract("https://shop.example.com/product/123", preset="ecommerce.shipping-info@1.0.0")
```

Barcode lookups are only as good as the databases they search, which sometimes hold a wrong record for a number. Putting `UPC` or `EAN` before the number helps.
