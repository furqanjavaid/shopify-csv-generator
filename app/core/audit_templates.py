"""Expert-level CRO audit issue templates for Word reports."""

from __future__ import annotations

ISSUE_TEMPLATES = {

    # ─── PRODUCT PAGE ────────────────────────────────────────

    "atc_below_fold": {
        "title": "Add to Cart Button Not Visible Without Scrolling",
        "severity": "HIGH",
        "category": "Product Page",
        "why": """The Add to Cart button is the single most important element on any product page — it is the exact moment a browser becomes a buyer. When this button sits below the visible screen, a significant portion of visitors never see it. They land, they read, they scroll partway — and they leave. Product pages where the primary CTA is above the fold convert at 2–3x the rate of pages where it is buried. At your price point, this is not a minor UX note — it is a direct, measurable revenue leak on every single session.""",
        "fix": """Add a sticky Add to Cart bar — a slim bar that pins to the bottom of the viewport as the customer scrolls. This bar should show the product title, current price, and a prominent Add to Cart button at all times. In Shopify, this is implementable in pure Liquid and CSS without any app — typically 4 to 6 hours of development. Additionally, review your product page layout and move the core purchase block — price, variant selector, quantity, and ATC — above any long description, tabs, or specification sections.""",
        "impact": "Directly recovers lost conversions on every product page session",
    },

    "no_reviews": {
        "title": "No Customer Reviews or Star Ratings on Product Pages",
        "severity": "HIGH",
        "category": "Product Page",
        "why": """Reviews are not a nice-to-have — they are the closest thing to a guarantee a buyer gets before committing money. Research consistently shows that 93% of consumers read reviews before purchasing, and products with reviews convert at 3.5x the rate of those without. For a store selling at your price point, the absence of visible social proof on the product page creates a trust gap that even great photography and copy cannot fully close. A potential buyer who has never ordered from you before needs evidence that others have — and had a good experience.""",
        "fix": """Install Judge.me (free tier available) or Loox and display star ratings directly on your product pages — both above the fold near the price, and as a full review section below the description. Import any existing reviews from email, social media, or previous platforms. If you have zero reviews, run a post-purchase email sequence asking your last 20–30 customers for honest feedback. Even five genuine reviews will produce a measurable lift in conversion within days of going live.""",
        "impact": "One of the highest-ROI fixes available — typically 15–30% conversion lift on product pages",
    },

    "no_product_images": {
        "title": "Insufficient Product Images — Single Angle Only",
        "severity": "HIGH",
        "category": "Product Page",
        "why": """Online shopping removes the ability to touch, hold, and inspect a product before buying. Your product images are your substitute for that physical experience. A single straight-on render or one studio shot does not give a buyer enough information to feel confident in their purchase. Buyers want to see the product from multiple angles, in a real environment, alongside objects that give a sense of scale, and close enough to see texture and finish quality. Stores with 5 or more product images consistently outperform those with 1–2 images on both conversion rate and return rate.""",
        "fix": """For every product, aim for a minimum of five images: a clean hero shot, two additional angles, one lifestyle or in-context photo, and one close-up showing texture, material, or key detail. If professional photography is not yet in budget, well-lit smartphone photos in natural light will outperform a single studio render. For products with variants (colour, size, finish), show a dedicated image for each variant — this alone can reduce hesitation significantly.""",
        "impact": "Reduces purchase hesitation and return rates simultaneously",
    },

    "no_urgency": {
        "title": "No Urgency or Scarcity Signals on Product Pages",
        "severity": "MEDIUM",
        "category": "Product Page",
        "why": """Without a reason to act now, the path of least resistance for any buyer is to wait. They close the tab, intend to return, and often never do. Urgency signals — low stock indicators, limited-time offers, dispatch cutoff times — are not manipulation tactics. They are honest information that helps buyers make a decision at the moment when their intent is highest. A visitor who is on your product page right now is warmer than they will ever be again. Giving them a reason to act today rather than tomorrow is one of the most reliable conversion levers available.""",
        "fix": """Add a dispatch cutoff message near the Add to Cart button: 'Order before 2pm — ships today.' If you track inventory in Shopify, display a low stock warning when quantity drops below 5 units using Liquid's inventory_quantity variable. For seasonal or limited products, a simple 'Only 3 left' label on the collection page card is enough to lift click-through meaningfully. Avoid fake countdown timers — buyers see through them immediately and they damage trust more than they help.""",
        "impact": "Converts hesitant browsers into same-session buyers",
    },

    "no_cross_sell": {
        "title": "No Cross-sell or Related Products on Product Pages",
        "severity": "MEDIUM",
        "category": "Product Page",
        "why": """A customer on your product page has already done the hardest thing — they found you, they like what they see, and they are considering spending money. This is the highest-intent moment in your funnel, and it is the ideal time to introduce complementary products. Every product page that ends without a 'You might also need' or 'Frequently bought together' section is a missed opportunity to increase average order value from customers who are already warm and ready.""",
        "fix": """Add a related products or complementary items section below the main product content. In Shopify, this can be done using the recommendations API or by manually curating a collection of companion products per category. Keep the selection tight — 3 to 4 products maximum, chosen for genuine relevance rather than just popularity. The section headline matters: 'Complete the look', 'Frequently bought together', or 'You might also need' consistently outperform generic 'Related products' headings.""",
        "impact": "Increases average order value without any additional traffic cost",
    },

    "no_sticky_atc": {
        "title": "No Sticky Add to Cart Bar on Product Pages",
        "severity": "LOW",
        "category": "Product Page",
        "why": """On longer product pages — particularly those with detailed descriptions, specifications, FAQs, or configurators — the primary Add to Cart button can scroll out of view within seconds of a visitor landing. A sticky ATC bar ensures the purchase action is always one click away, regardless of where the customer is on the page. This is particularly important on mobile, where scroll behaviour is faster and the viewport is smaller.""",
        "fix": """Implement a sticky bar that appears after the user scrolls past the main ATC button and pins to the bottom of the screen. The bar should include the product title, price, and an Add to Cart button. This is a pure Liquid and CSS implementation — no app required. Trigger it with a small JavaScript IntersectionObserver watching the main ATC button's visibility.""",
        "impact": "Captures conversions from engaged visitors who scroll deep into product content",
    },

    # ─── CART & CHECKOUT ─────────────────────────────────────

    "no_cart_trust": {
        "title": "No Trust Signals at the Cart and Checkout Stage",
        "severity": "HIGH",
        "category": "Cart & Checkout",
        "why": """The cart page is the highest-anxiety point in the entire purchase funnel. A customer who has added a product to their cart has already made the emotional decision to buy — but they have not yet committed financially. This is the moment when doubt creeps in. 'Is this site legitimate? Is my payment secure? What if it doesn't arrive?' Trust signals at this exact moment — security badges, guarantee copy, delivery promises — directly address these doubts and reduce the gap between intent and completion.""",
        "fix": """Add a trust bar directly below the checkout button containing four elements: a padlock icon with 'Secure Checkout', your delivery promise, your returns or guarantee policy summary, and your review platform rating. Keep it compact — one line of icons and short labels is more effective than a paragraph. This is a 30-minute Liquid edit to your cart template and requires no app.""",
        "impact": "Directly reduces cart abandonment at the moment of highest purchase intent",
    },

    "no_cart_upsell": {
        "title": "Cart Page Has No Upsell or Order Bump",
        "severity": "MEDIUM",
        "category": "Cart & Checkout",
        "why": """A customer who has reached your cart has already agreed to spend money with you. Their buying resistance is at its lowest point in the funnel — which makes the cart page the single best place to introduce a low-friction add-on. A relevant accessory, a complementary product, or a 'most customers also add' suggestion placed above the checkout button captures additional revenue from buyers who are already committed, without requiring any additional marketing spend.""",
        "fix": """Add a 'You might also need' section above the checkout button featuring 2–3 low-cost, high-relevance accessories. Keep the price point of upsell items below the value of the cart where possible — a £15 add-on to a £150 cart feels easy. This can be implemented in Liquid using a manually curated product list or the Shopify recommendations API, with no app required.""",
        "impact": "Increases average order value from already-committed buyers at zero acquisition cost",
    },

    "cart_flow_broken": {
        "title": "Cart Flow Has Errors — Checkout Cannot Be Completed",
        "severity": "HIGH",
        "category": "Cart & Checkout",
        "why": """A broken cart or checkout flow is the most damaging issue a store can have. It does not matter how good your product is, how strong your photography is, or how well your ads perform — if a customer cannot complete their purchase, every other investment is wasted. Cart errors create immediate loss of sale and long-term loss of trust. A customer who hits an error at checkout rarely returns.""",
        "fix": """Test the full purchase flow immediately — add to cart, view cart, proceed to checkout, and complete a test order using Shopify's Bogus Gateway payment method. Identify and fix the specific error before any other optimisation work. Until the cart flow is confirmed working end-to-end, no other CRO work will have meaningful impact.""",
        "impact": "Critical — no other fix matters until this is resolved",
    },

    # ─── TRUST & CREDIBILITY ─────────────────────────────────

    "trust_badges_buried": {
        "title": "Trust Badges and Social Proof Hidden Below the Fold",
        "severity": "HIGH",
        "category": "Trust & Credibility",
        "why": """Your review ratings, guarantee badges, and years-in-business credentials are powerful conversion tools — but only if buyers see them at the right moment. Placing trust signals in the footer or at the bottom of a long homepage means the vast majority of visitors never encounter them. Social proof works hardest when it appears at the exact point where a buyer is deciding whether to explore further or leave. That point is within the first screen they see.""",
        "fix": """Move your trust bar — review platform rating, key guarantee, delivery promise, and one or two credential badges — to a position directly below your hero section CTA buttons. This single change often produces the largest measurable lift of any homepage optimisation. Keep it compact: five icons with short labels on one line. Test with your real Trustpilot or Google review data, linked to the verified source.""",
        "impact": "Increases scroll depth and product page visits from homepage traffic",
    },

    "no_shipping_policy": {
        "title": "Shipping Policy Page is Missing",
        "severity": "HIGH",
        "category": "Trust & Credibility",
        "why": """For any store selling physical products, the absence of a published shipping policy is a meaningful purchase barrier. Before committing to a purchase — particularly at higher price points — buyers want to know exactly what delivery is included, how long it will take, what geographic areas are covered, and what happens if something goes wrong in transit. When this information is absent or buried, buyers default to assuming the worst and abandoning rather than emailing to ask.""",
        "fix": """Create a dedicated Shipping and Delivery page at /policies/shipping-policy covering: what delivery cost is included in the price, geographic coverage and any exclusions, estimated dispatch and delivery timeframes by product type, what constitutes a damaged delivery and how to report it, and who to contact with delivery queries. Link this page from your footer, your product pages near the ATC button, and your cart page. Clear delivery information at the right moment removes one of the most common pre-checkout objections.""",
        "impact": "Removes a pre-checkout objection that silently kills conversions",
    },

    "price_mismatch": {
        "title": "Collection Page Prices Do Not Match Product Page Prices",
        "severity": "HIGH",
        "category": "Trust & Credibility",
        "why": """When a buyer sees one price on a collection card and a different price when they open the product page, their immediate reaction is confusion — and confusion at the consideration stage almost always results in abandonment rather than a purchase. At best, the buyer assumes a technical error and loses confidence in the store. At worst, they suspect bait-and-switch pricing and leave permanently. Price consistency across every touchpoint in the funnel is a non-negotiable foundation of purchase trust.""",
        "fix": """Audit every product in the affected collections and ensure the price displayed on collection cards, in search results, on product pages, and in the cart are all pulled from the same Shopify price source — the variant's compare_at_price and price fields. If your theme uses any custom pricing logic, metafields, or app-generated prices, review each one for consistency. Run a QA pass across your full product catalogue after fixing, checking at least 10 products across different price tiers.""",
        "impact": "Restores buyer trust at a critical decision point in the funnel",
    },

    # ─── MOBILE ──────────────────────────────────────────────

    "mobile_atc_issue": {
        "title": "Add to Cart Button is Difficult to Use on Mobile",
        "severity": "HIGH",
        "category": "Mobile Experience",
        "why": """More than half of ecommerce traffic now arrives on mobile devices, and for many stores the majority of purchases are completed on a phone. A button that is too small to tap confidently, positioned awkwardly, or hidden behind mobile-specific layout issues will silently kill a significant portion of your mobile conversions. Mobile buyers are less patient than desktop buyers — if the purchase action feels difficult, they leave.""",
        "fix": """Ensure your Add to Cart button on mobile meets a minimum tap target size of 44 x 44 pixels — this is the Apple Human Interface Guidelines standard and the threshold below which tap accuracy drops significantly. The button should be full-width or near-full-width on mobile, positioned above any secondary content, and not obscured by sticky headers, cookie banners, or chat widgets. Test on a real device, not just browser developer tools.""",
        "impact": "Recovers conversions lost to friction on the majority of your traffic",
    },

    # ─── SPEED & TECHNICAL ───────────────────────────────────

    "slow_page_speed": {
        "title": "Homepage Load Time is Affecting User Experience and SEO",
        "severity": "HIGH",
        "category": "Page Speed",
        "why": """Page speed is not a technical metric — it is a conversion metric. Every additional second of load time reduces conversions by approximately 7%, and Google's Core Web Vitals now directly factor page experience into search rankings. A homepage that takes more than 2 seconds to become interactive is losing both organic search position and direct revenue from visitors who bounce before the page finishes loading. For a store investing in any form of paid or organic traffic, a slow homepage is a hole in the bucket.""",
        "fix": """Start with the highest-impact changes: compress and convert all hero images to WebP format, add fetchpriority='high' to your above-the-fold hero image, and lazy-load all images below the fold. If your homepage uses a carousel or slider, evaluate whether it is necessary — static hero sections consistently load faster and convert better than carousels. Use Shopify's built-in image CDN for all product and collection images. After making changes, test with Google PageSpeed Insights and target a mobile score above 70.""",
        "impact": "Improves both conversion rate and organic search visibility simultaneously",
    },

    "carousel_risk": {
        "title": "Homepage Carousel is a Speed and Conversion Risk",
        "severity": "MEDIUM",
        "category": "Page Speed",
        "why": """Carousels are one of the most reliably underperforming homepage elements in ecommerce. Research across thousands of stores shows that the second and subsequent carousel slides are seen by fewer than 5% of visitors — meaning the effort invested in those slides produces almost no return. Beyond low engagement, carousels require multiple large images to be loaded, increasing page weight and slowing Largest Contentful Paint — one of Google's primary ranking signals. They also introduce JavaScript dependencies that can block rendering on slower connections.""",
        "fix": """Replace your carousel with a single, strong static hero section. Choose your most compelling offer or product, write a clear benefit-led headline, and use one high-quality image. If you feel strongly about showcasing multiple categories, use a static grid of category cards below the hero instead — these are scrollable, faster, and convert significantly better than auto-advancing slides.""",
        "impact": "Improves page speed score and focuses visitor attention on your primary offer",
    },

    # ─── NAVIGATION ──────────────────────────────────────────

    "hamburger_only_desktop": {
        "title": "Desktop Navigation Hides All Categories Behind a Menu Icon",
        "severity": "MEDIUM",
        "category": "Navigation",
        "why": """On desktop, buyers expect to see your main product categories immediately visible in the header — not hidden behind a click. When navigation requires an extra step to reveal, buyers who arrive with a specific product in mind face unnecessary friction finding it. Category links visible in the header reduce the number of steps between landing and finding the right product, and they signal immediately that you are a full-catalogue store rather than a single-product page.""",
        "fix": """Add visible top-level navigation links directly in your desktop header for your main product categories. For a store with 4–6 departments, a simple horizontal link list works. For stores with deeper catalogues, a mega menu with category images is more effective. Ensure mobile navigation remains a clean hamburger menu — the fix applies to desktop viewports only.""",
        "impact": "Reduces friction for buyers who arrive with purchase intent",
    },

    # ─── POLICIES ────────────────────────────────────────────

    "thin_policies": {
        "title": "Policy Pages Are Too Short to Build Buyer Confidence",
        "severity": "MEDIUM",
        "category": "Trust & Credibility",
        "why": """Policy pages are not just legal requirements — they are trust signals. A buyer who has a question about returns, delivery, or their rights as a consumer will check your policy pages before completing a purchase. A policy page with fewer than 200 words signals that these questions have not been properly thought through, and creates the impression that the store may be difficult to deal with if something goes wrong. Detailed, clear policies written in plain language actively increase conversion by reducing pre-purchase anxiety.""",
        "fix": """Expand each policy page to cover the scenarios buyers actually worry about. Your refund policy should explain: what qualifies for a refund, the process for initiating one, how long it takes, who pays return shipping, and what happens with custom or made-to-order items. Your terms should cover payment, cancellation, and delivery responsibilities. Write in plain English, not legal boilerplate — clarity builds more trust than formal language.""",
        "impact": "Removes pre-checkout hesitation for buyers who check policies before purchasing",
    },

    # ─── MARKETING ───────────────────────────────────────────

    "no_email_capture": {
        "title": "No Email Capture Mechanism Above the Fold",
        "severity": "HIGH",
        "category": "Marketing & Retention",
        "why": """The majority of first-time visitors to any ecommerce store will not purchase on their first visit. Without a mechanism to capture their email address, these visitors are lost permanently — there is no way to follow up, re-engage, or convert them later. An email list is the only marketing channel you own outright — unlike social media followers or paid traffic, your email subscribers cannot be taken away by an algorithm change or a rising cost-per-click. Building this list from day one is one of the highest-value long-term investments a store can make.""",
        "fix": """Add an email capture offer above the fold — either as a slide-in popup triggered after 10–15 seconds, or as an embedded form in your hero or mid-homepage section. The offer should be specific and valuable: a percentage discount, early access to new products, a useful guide relevant to your product category, or exclusive trade pricing. Generic 'subscribe to our newsletter' prompts convert at a fraction of the rate of offers with a clear, immediate benefit.""",
        "impact": "Builds a retargeting asset that compounds in value over time",
    },

}

# ─── PASSING CHECK MESSAGES ──────────────────────────────

PASSING_MESSAGES = {
    "trust_badges": "Trust signals present — {count} trust elements detected including {match}",
    "price_visible": "Price clearly visible on product page — {price} displayed above the fold",
    "cart_checkout": "Cart to checkout flow working — checkout button visible and functional",
    "mobile_atc": "Mobile Add to Cart accessible — above fold on 390px viewport, tap target meets 44px minimum",
    "page_speed": "Homepage response time excellent — {time}s server response",
    "email_capture": "Email capture present — {count} capture points detected",
    "cross_sell": "Cross-sell or upsell block detected on product pages",
    "announcement_bar": "Announcement bar present — delivery or promotional messaging visible",
    "nav_depth": "Expanded navigation — {count} visible links, full menu accessible without hamburger",
    "dead_links": "No broken internal links — all sampled homepage links returning 200",
    "meta_title": "Meta title present — '{title}'",
    "meta_description": "Meta description present and set",
    "canonical": "Canonical tag correctly configured",
    "reviews": "Review widget detected on product pages",
    "sticky_atc": "Sticky Add to Cart bar present",
}

# ─── EXECUTIVE SUMMARY TEMPLATES ─────────────────────────

EXEC_SUMMARY = {
    "score_high": """
{store} is in a strong position — the foundations are solid and the store is clearly built with care.
The issues identified in this audit are refinements rather than rebuilds. Addressing the priority
fixes below will remove the remaining friction between your current traffic and the conversion rate
this store is capable of achieving.
""",
    "score_medium": """
{store} has the right ingredients — good products, a functional store, and clear brand identity.
What it is missing are the conversion signals that turn browsers into buyers. The issues identified
in this audit are not cosmetic — they are the specific points in your funnel where potential customers
are deciding not to purchase. Each fix below is directly tied to a measurable outcome.
""",
    "score_low": """
{store} has real potential, but the store as it currently stands is converting a small fraction of
the visitors it could be. Several critical issues are creating friction or trust gaps at exactly the
moments when buyers are deciding whether to proceed. The good news is that every issue identified
in this report is fixable — and the fixes are prioritised below so you can start with the changes
that will have the largest immediate impact.
""",
}

# Map raw auditor finding keys → issue template keys
FINDING_TO_TEMPLATE = {
    "atc_above_fold": "atc_below_fold",
    "product_has_reviews": "no_reviews",
    "product_image_count": "no_product_images",
    "has_urgency": "no_urgency",
    "has_cross_sell": "no_cross_sell",
    "has_sticky_atc": "no_sticky_atc",
    "cart_has_trust": "no_cart_trust",
    "cart_has_upsell": "no_cart_upsell",
    "cart_has_checkout_btn": "cart_flow_broken",
    "trust_badge_count": "trust_badges_buried",
    "shipping_policy_missing": "no_shipping_policy",
    "price_mismatch_count": "price_mismatch",
    "mobile_atc_tap_target_ok": "mobile_atc_issue",
    "homepage_load_time": "slow_page_speed",
    "has_carousel": "carousel_risk",
    "nav_hamburger_only": "hamburger_only_desktop",
    "has_email_capture": "no_email_capture",
}

# Map finding check_id → template key (for report enrichment)
CHECK_ID_TO_TEMPLATE = {
    1: "atc_below_fold",
    2: "no_reviews",
    3: "trust_badges_buried",
    5: "cart_flow_broken",
    51: "no_cart_trust",
    52: "no_cart_upsell",
    6: "mobile_atc_issue",
    61: "mobile_atc_issue",
    7: "slow_page_speed",
    8: "no_email_capture",
    9: "no_urgency",
    10: "no_cross_sell",
    12: "no_product_images",
    17: "no_sticky_atc",
    201: "hamburger_only_desktop",
    202: "no_shipping_policy",  # refined in resolve_template_key()
    203: "price_mismatch",
}

# Passing check_id → PASSING_MESSAGES key
CHECK_ID_TO_PASSING = {
    2: "reviews",
    3: "trust_badges",
    4: "price_visible",
    5: "cart_checkout",
    6: "mobile_atc",
    61: "mobile_atc",
    7: "page_speed",
    8: "email_capture",
    10: "cross_sell",
    16: "announcement_bar",
    17: "sticky_atc",
    100: "meta_title",
    101: "meta_description",
    102: "canonical",
    200: "dead_links",
    201: "nav_depth",
}
