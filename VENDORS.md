# Vendor catalog

`call_vendor()` reaches a vendor's own API with your account's credentials:
your Google Ads reports, your Datadog logs, a contact-data vendor you pay
for. Unlike `search` or `extract`, nothing here is interchangeable or
normalized: each vendor answers in its own shape, and the router adds the
credentials, finds the endpoint, and keeps three guarantees (credentials go
only to the vendor's host, no input is dropped or invented, writes are
opt-in). See the README's "Vendor data" section for the API.

Every entry is written from the vendor's own documentation. **Checked** says
how: `docs <date>` means the entry was transcribed from the vendor's reference
on that date and has not yet been called live; `live <date>` means a real call
matched it. Add your key and run the vendor's endpoints to move one to `live`.

The tables below are generated from `src/web_router/data/vendors/` by
`make vendors`; edit the JSON files, never the tables.

<!-- vendors -->
119 vendors, 1234 endpoints.

### advertising

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Adyntel](https://docs.adyntel.com/) `adyntel` | Competitor ad intelligence: the ads a company runs on Meta, Google, LinkedIn and TikTok, keyword search across ad libraries, and a domain's paid and organic keywords. | 9 read | key in body | `ADYNTEL_API_KEY`, `ADYNTEL_EMAIL` | docs 2026-11-08 |
| [Google Ads](https://developers.google.com/google-ads/api/rest/overview) `google-ads` | Your Google Ads accounts: GAQL reports on campaigns, spend, clicks and conversions, Keyword Planner ideas and volumes, and budget changes. | 6 read, 2 write | OAuth refresh token | `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` | docs 2026-11-08 |
| [Meta Ad Library](https://www.facebook.com/ads/library/api/) `meta-ad-library` | Search the public Meta Ad Library: ads running on Facebook, Instagram and other Meta apps by keyword or page, including political and issue ads. | 1 read | key in query | `META_AD_LIBRARY_ACCESS_TOKEN` | docs 2026-11-08 |
| [Meta Ads (Marketing API)](https://developers.facebook.com/docs/marketing-apis) `meta-ads` | Your Facebook and Instagram ad accounts: account details, campaigns, and performance insights (spend, reach, clicks, conversions) at any level and breakdown. | 3 read | key in query | `META_ADS_ACCESS_TOKEN` | docs 2026-11-08 |
| [Pinterest Ads](https://developers.pinterest.com/docs/api/v5/) `pinterest-ads` | Your Pinterest ad accounts: list accounts, campaigns, ad groups and ads, and pull analytics for accounts and campaigns. | 6 read | OAuth refresh token | `PINTEREST_APP_ID`, `PINTEREST_APP_SECRET`, `PINTEREST_REFRESH_TOKEN` | docs 2026-11-08 |
| [Snapchat Ads](https://developers.snap.com/api/marketing-api/Ads-API/authentication) `snapchat-ads` | Your Snapchat ad accounts: organizations, ad accounts, campaigns and ads, plus delivery stats for any of them. | 7 read | OAuth refresh token | `SNAPCHAT_CLIENT_ID`, `SNAPCHAT_CLIENT_SECRET`, `SNAPCHAT_REFRESH_TOKEN` | docs 2026-11-08 |

### analytics

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Google Analytics 4 (Data API)](https://developers.google.com/analytics/devguides/reporting/data/v1) `google-analytics` | Reports from your Google Analytics 4 properties: users, sessions, events and conversions by any dimension, plus realtime and the dimension/metric catalog. | 3 read | OAuth refresh token | `GOOGLE_ANALYTICS_CLIENT_ID`, `GOOGLE_ANALYTICS_CLIENT_SECRET`, `GOOGLE_ANALYTICS_REFRESH_TOKEN` | docs 2026-11-08 |
| [Google Tag Manager](https://developers.google.com/tag-platform/tag-manager/api/reference/rest/v2) `google-tag-manager` | Your Tag Manager accounts, containers and workspaces: read tags, triggers and variables, create container versions, and publish them live. | 7 read, 2 write | OAuth refresh token | `GOOGLE_TAG_MANAGER_CLIENT_ID`, `GOOGLE_TAG_MANAGER_CLIENT_SECRET`, `GOOGLE_TAG_MANAGER_REFRESH_TOKEN` | docs 2026-11-08 |

### seo

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [cloro](https://cloro.dev/docs/api-reference/introduction) `cloro` | Structured answers and sources from ChatGPT, Gemini, Perplexity, Google AI Mode, Bing AI Mode, Google Search and Google News, by country. | 12 read | key in header | `CLORO_API_KEY` | docs 2026-11-08 |
| [DataForSEO](https://docs.dataforseo.com/v3/) `dataforseo` | DataForSEO APIs: live and queued Google SERPs, keyword data, DataForSEO Labs and backlinks, billed per task in USD. | 9 read | HTTP Basic | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | docs 2026-11-08 |
| [Google Search Console](https://developers.google.com/webmaster-tools/v1/api_reference_index) `google-search-console` | Your Search Console properties: search performance (clicks, impressions, CTR, position by query, page, country, device and date) and sitemaps. | 3 read, 1 write | OAuth refresh token | `GOOGLE_SEARCH_CONSOLE_CLIENT_ID`, `GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET`, `GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN` | docs 2026-11-08 |
| [Majestic](https://developer-support.majestic.com/api/) `majestic` | Majestic link intelligence: Trust Flow, Citation Flow, backlinks, referring domains, anchors and top pages. | 6 read | key in query | `MAJESTIC_API_KEY` | docs 2026-11-08 |
| [Moz](https://moz.com/help/links-api) `moz` | Moz Links API: Domain Authority, Page Authority, backlinks, anchor text, linking domains and top pages. | 9 read | key in header | `MOZ_API_TOKEN` | docs 2026-11-08 |
| [Semrush](https://developer.semrush.com/api/v3/analytics/basic-docs/) `semrush` | Semrush Analytics API: domain overviews, organic and paid keywords, keyword research and backlink reports. | 14 read | key in query | `SEMRUSH_API_KEY` | docs 2026-11-08 |
| [SE Ranking](https://seranking.com/api/data/) `seranking` | SE Ranking Data API: domain analysis, keyword research and backlink data. | 11 read | key in header | `SERANKING_API_KEY` | docs 2026-11-08 |
| [SpyFu](https://developer.spyfu.com/) `spyfu` | SpyFu competitive research API: domain stats, organic and paid keywords, competitors, top pages and keyword data. | 8 read | key in query | `SPYFU_API_KEY` | docs 2026-11-08 |

### observability

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Datadog](https://docs.datadoghq.com/api/latest/) `datadog` | Metrics, logs, events, monitors, dashboards, hosts, incidents and SLOs from your Datadog organization. | 13 read | key in header | `DD_API_KEY`, `DD_APPLICATION_KEY` | docs 2026-11-08 |

### social

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [AnyAPI](https://getanyapi.com/docs) `anyapi` | One pay-per-call wallet in front of many data sources: social profiles, posts, comments and search across Facebook, Instagram, LinkedIn, TikTok, X and Google. | 31 read | key in header | `ANYAPI_API_KEY` | docs 2026-11-08 |
| [Facebook Pages (Graph API)](https://developers.facebook.com/docs/pages-api) `facebook` | Facebook Pages you manage through Meta's Graph API: Page details, the Page feed, and publishing posts and photos. | 3 read, 2 write | key in query | `FACEBOOK_ACCESS_TOKEN` | docs 2026-11-08 |
| [Fetchin](https://docs.fetchin.io/) `fetchinio` | Professional-network profiles, posts, comments, reactions and company pages as a credit-billed API. | 10 read | key in header | `FETCHIN_API_KEY` | docs 2026-11-08 |
| [HarvestAPI](https://docs.harvestapi.io) `harvestapi` | LinkedIn data without a LinkedIn login: profiles, companies, jobs, posts, comments and reactions, searchable by filters. | 18 read | key in header | `HARVESTAPI_API_KEY` | docs 2026-11-08 |
| [Influencers Club](https://docs.influencers.club/) `influencersclub` | Creator discovery, similar creators and enrichment (profile, analytics, posts, audience overlap) across social platforms. | 13 read | key in header | `INFLUENCERS_CLUB_API_KEY` | docs 2026-11-08 |
| [Instagram Graph API](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api) `instagram` | Your Instagram professional account through Meta's Graph API: profile, media, insights, comments, other businesses' public data, and publishing. | 7 read, 3 write | key in query | `INSTAGRAM_ACCESS_TOKEN` | docs 2026-11-08 |
| [Just One API](https://docs.justoneapi.com/en/usage) `justoneapi` | Social and e-commerce data from Chinese and global platforms: Xiaohongshu, Weibo, TikTok, Instagram and YouTube profiles, posts, comments and search. | 20 read | key in query | `JUSTONEAPI_TOKEN` | docs 2026-11-08 |
| [LinkedIn](https://learn.microsoft.com/en-us/linkedin/) `linkedin` | LinkedIn's official API for the signed-in member: identity, reading and publishing posts for a member or company page. | 3 read, 2 write | key in header | `LINKEDIN_ACCESS_TOKEN` | docs 2026-11-08 |
| [Scrape Creators](https://docs.scrapecreators.com) `scrapecreators` | Public social media data over one API: TikTok, Instagram, YouTube, X, LinkedIn and Reddit profiles, posts, comments and search. | 26 read | key in header | `SCRAPECREATORS_API_KEY` | docs 2026-11-08 |
| [TikHub](https://docs.tikhub.io/) `tikhub` | Public data from TikTok, Instagram, YouTube and X (and many other platforms) over one API: profiles, posts, comments and search. | 20 read | key in header | `TIKHUB_API_KEY` | docs 2026-11-08 |
| [TikTok Display API](https://developers.tiktok.com/doc/display-api-overview/) `tiktok` | The TikTok Display API: profile and videos of the TikTok user who authorized your app. | 3 read | key in header | `TIKTOK_ACCESS_TOKEN` | docs 2026-11-08 |
| [X (Twitter) API](https://docs.x.com/x-api) `x` | X API v2: search recent posts, look up users and posts, read timelines and followers, and publish posts. | 5 read, 1 write | key in header | `X_BEARER_TOKEN` | docs 2026-11-08 |
| [YouTube Data API](https://developers.google.com/youtube/v3/docs) `youtube` | Public YouTube data: videos, channels, playlists, comments and search, read with a Google API key. | 6 read | key in query | `YOUTUBE_API_KEY` | docs 2026-11-08 |

### enrichment

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [AI Ark](https://docs.ai-ark.com) `aiark` | B2B people and company search, verified email and mobile finding, job posts and lookalike company discovery from AI Ark. | 10 read, 1 write | key in header | `AIARK_API_KEY` | docs 2026-11-08 |
| [Anymail Finder](https://anymailfinder.com/email-finder-api/docs) `anymailfinder` | Find and verify business emails by person, company, decision-maker role or LinkedIn URL, charged only for valid hits. | 8 read | key in header | `ANYMAILFINDER_API_KEY` | docs 2026-11-08 |
| [Apollo.io](https://docs.apollo.io/reference/) `apollo` | Search and enrich B2B people and companies, with email and phone reveal, from Apollo's contact database. | 6 read | key in header | `APOLLO_API_KEY` | docs 2026-11-08 |
| [BlitzAPI](https://docs.blitz-api.ai) `blitzapi` | B2B people and company search, LinkedIn-based enrichment, verified work emails, phone numbers and job postings from BlitzAPI. | 19 read | key in header | `BLITZ_API_KEY` | docs 2026-11-08 |
| [ContactOut](https://api.contactout.com/) `contactout` | Find work and personal emails and phones from LinkedIn profiles, search people and companies, and verify emails. | 20 read | key in header | `CONTACTOUT_API_KEY` | docs 2026-11-08 |
| [Datagma](https://datagmaapi.readme.io/reference/getting-started-with-your-api) `datagma` | Find work emails and phones, enrich people and companies, and detect job changes. | 6 read | key in query | `DATAGMA_API_KEY` | docs 2026-11-08 |
| [Dropcontact](https://developer.dropcontact.com/) `dropcontact` | Find and verify professional emails and add company data to contact lists, asynchronously, paying only for verified emails. | 3 read | key in header | `DROPCONTACT_API_KEY` | docs 2026-11-08 |
| [DropLeads](https://dropleads.readme.io/) `dropleads` | Prime-DB people and company search, verified or simple person enrichment, company enrichment and credit balance. | 11 read | key in header | `DROPLEADS_API_KEY` | docs 2026-11-08 |
| [Enrich Layer](https://enrichlayer.com/docs) `enrichlayer` | People, company, contact, job and school data: profile enrichment, lookups, search, emails and phone numbers from Enrich Layer. | 24 read | key in header | `ENRICHLAYER_API_KEY` | docs 2026-11-08 |
| [Enrow](https://docs.enrow.io/api-overview) `enrow` | Find and verify professional emails and find phone numbers, asynchronously, by name, company or LinkedIn URL. | 7 read | key in header | `ENROW_API_KEY` | docs 2026-11-08 |
| [Findymail](https://app.findymail.com/docs/) `findymail` | Find and verify emails and phones from names, domains or LinkedIn profiles, and look up companies and employees. | 11 read, 2 write | key in header | `FINDYMAIL_API_KEY` | docs 2026-11-08 |
| [GetLeads](https://www.getleads.io/docs/) `getleadsio` | B2B contact and company database with structured search, counts, async CSV exports, and person and company enrichment. | 18 read | key in header | `GETLEADS_API_KEY` | docs 2026-11-08 |
| [Hunter](https://hunter.io/api-documentation/v2) `hunter` | Find and verify professional email addresses by domain or person, and enrich people and companies. | 10 read | key in header | `HUNTER_API_KEY` | docs 2026-11-08 |
| [Icypeas](https://api-doc.icypeas.com/) `icypeas` | Find and verify professional emails, search a people and company database, and scrape profile and company data. | 16 read | key in header | `ICYPEAS_API_KEY` | docs 2026-11-08 |
| [LeadMagic](https://leadmagic.io/docs/api-reference/email-validation) `leadmagic` | Find and validate work emails, personal emails and mobiles from names or profile URLs, and look up profiles and companies. | 8 read | key in header | `LEADMAGIC_API_KEY` | docs 2026-11-08 |
| [LeadOcean](https://leadocean.io/docs) `leadocean` | B2B people and company search, person and company enrichment, work and personal email, phone and reverse lookups, and bulk CSV exports. | 14 read, 1 write | key in header | `LEADOCEAN_API_KEY` | docs 2026-11-08 |
| [Leadsforge](https://api.leadsforge.ai/public/swagger/index.html) `leadsforge` | B2B lead search with email, phone and LinkedIn enrichment, lookalike companies, company followers and Google Maps business discovery. | 19 read | key in header | `LEADSFORGE_API_KEY` | docs 2026-11-08 |
| [Lusha](https://docs.lusha.com/) `lusha` | Search B2B contacts and companies, then reveal emails, phones and firmographics for the ones you pick. | 11 read | key in header | `LUSHA_API_KEY` | docs 2026-11-08 |
| [MoltSets](https://developer.moltsets.com) `moltsets` | B2B people and company search, email and mobile lookup, email validation and reverse lookups. | 15 read | key in header | `MOLTSETS_API_KEY` | docs 2026-11-08 |
| [People Data Labs](https://docs.peopledatalabs.com/docs/api-overview) `pdl` | Person and company enrichment, identify and dataset search over PDL's person and company data. | 5 read | key in header | `PDL_API_KEY` | docs 2026-11-08 |
| [Prospeo](https://prospeo.io/api-docs) `prospeo` | Find and verify emails and mobiles, enrich people and companies, and search a B2B people and company database. | 7 read | key in header | `PROSPEO_API_KEY` | docs 2026-11-08 |
| [Quick Enrich](https://app.quickenrich.io/docs) `quickenrich` | Work email and phone lookup by LinkedIn URL or name and company, reverse email lookup, domain and contact discovery, and company search. | 8 read | key in header | `QUICKENRICH_API_KEY` | docs 2026-11-08 |
| [Sixtyfour](https://docs.sixtyfour.ai/introduction) `sixtyfour` | Research agents and data for people and companies: enrichment, email and phone finding, reverse lookups and filter search. | 15 read | key in header | `SIXTYFOUR_API_KEY` | docs 2026-11-08 |
| [Tomba](https://docs.tomba.io/api) `tomba` | Find, verify and enrich professional emails, phone numbers and company data by domain, name, LinkedIn URL or article. | 17 read | key in header | `TOMBA_API_KEY`, `TOMBA_SECRET_KEY` | docs 2026-11-08 |
| [Village](https://docs.village.ai/api-reference/introduction) `village` | Relationship intelligence: warm-introduction paths to people and companies through your network, with people and company enrichment. | 13 read | key in header | `VILLAGE_API_TOKEN` | docs 2026-11-08 |
| [Wiza](https://docs.wiza.co/) `wiza` | Reveal emails and phones from profile URLs or names, enrich companies, and search prospects. | 5 read | key in header | `WIZA_API_KEY` | docs 2026-11-08 |

### email-verification

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [BounceBan](https://bounceban.com/public/doc/api.html) `bounceban` | Email verification that also scores catch-all and gateway-protected addresses, with single, bulk-list and account calls. | 9 read, 2 write | key in header | `BOUNCEBAN_API_KEY` | docs 2026-11-08 |
| [Filtrat](https://filtrat.io/v1/docs) `filtrat` | Email verification, work-email finding from a name and domain or a LinkedIn URL, bulk list jobs, and email-to-LinkedIn lookup. | 8 read, 1 write | key in header | `FILTRAT_API_KEY` | docs 2026-11-08 |
| [MillionVerifier](https://developer.millionverifier.com/) `millionverifier` | Real-time email verification with a quality grade, result code, role and free-provider flags, and a credit balance check. | 2 read | key in query | `MILLIONVERIFIER_API_KEY` | docs 2026-11-08 |
| [Scrubby](https://docs.scrubby.io/) `scrubby` | Email validation with instant single checks, queued bulk batches and slower deep verification. | 5 read | key in header | `SCRUBBY_API_KEY` | docs 2026-11-08 |
| [Trestle](https://docs.trestleiq.com/api-reference) `trestleiq` | Phone validation, caller identification and lead-contact grading (phone, email, address, IP) from Trestle's identity data. | 3 read | key in header | `TRESTLEIQ_API_KEY` | docs 2026-11-08 |
| [Kitt](https://documenter.getpostman.com/view/479833/2s93m62NHf) `trykitt` | Find and verify work emails in real time, with a credit balance and job lookup. | 5 read | key in header | `TRYKITT_API_KEY` | docs 2026-11-08 |
| [ZeroBounce](https://www.zerobounce.net/docs/email-validation-api-quickstart) `zerobounce` | Real-time email validation with status, sub-status, domain and activity signals, plus credit and usage checks. | 3 read | key in query | `ZEROBOUNCE_API_KEY` | docs 2026-11-08 |

### company-data

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [akta.pro](https://docs.akta.pro) `akta` | Company data, headcount, traffic, jobs, reviews, news signals and generated company lists from akta.pro. | 10 read | key in header | `AKTA_API_KEY` | docs 2026-11-08 |
| [Aviato](https://docs.data.aviato.co) `aviato` | Company, funding, investor and people data, contact info and search through the Aviato data engine. | 33 read | key in header | `AVIATO_API_KEY` | docs 2026-11-08 |
| [Context.dev](https://docs.context.dev) `branddev` | Brand and company data, natural-language company and people search, person enrichment, news, plus web scrape, crawl, map and search from Context.dev. | 14 read | key in header | `CONTEXT_DEV_API_KEY` | docs 2026-11-08 |
| [CompanyEnrich](https://docs.companyenrich.com) `companyenrich` | Company and people search, enrichment, workforce insights, lookalikes and work-email lookup from CompanyEnrich. | 25 read | key in header | `COMPANYENRICH_API_KEY` | docs 2026-11-08 |
| [Coresignal](https://docs.coresignal.com) `coresignal` | Fresh company, employee and job data from professional-network sources: search for IDs, then collect or enrich records. | 6 read | key in header | `CORESIGNAL_API_KEY` | docs 2026-11-08 |
| [Corpo API](https://docs.corpoapi.com) `corpoapi` | Official company-register lookups by jurisdiction: profiles, officers, charges, insolvency, shareholders, financials, ownership and VAT checks. | 16 read | key in header | `CORPOAPI_API_KEY` | docs 2026-11-08 |
| [Crunchbase](https://data.crunchbase.com/docs/using-the-api) `crunchbase` | Company, funding, investor and people data from the Crunchbase REST API (v4): entity lookup, search and autocomplete. | 6 read | key in header | `CRUNCHBASE_API_KEY` | docs 2026-11-08 |
| [Crustdata](https://docs.crustdata.com) `crustdata` | Company and people search, enrichment, job listings and web search from Crustdata's datasets. | 11 read | key in header | `CRUSTDATA_API_KEY` | docs 2026-11-08 |
| [Diffbot](https://www.diffbot.com/docs/) `diffbot` | Diffbot Knowledge Graph: search organizations, people and articles with DQL, and enhance records from partial data. | 4 read | key in query | `DIFFBOT_TOKEN` | docs 2026-11-08 |
| [Fiber AI](https://api.fiber.ai/llms.txt) `fiber-ai` | B2B company and people search, LinkedIn live enrichment, and email and phone reveal. | 13 read | key in header | `FIBER_API_KEY` | docs 2026-11-08 |
| [Limadata](https://limadata.com/api) `limadata` | Real-time B2B people and company enrichment, database search, contact finding and company job postings. | 4 read | key in header | `LIMADATA_API_KEY` | docs 2026-11-08 |
| [Ocean.io](https://app.ocean.io/docs) `oceanio` | Lookalike company and people search, company and person enrichment and bulk lookups. | 15 read | key in header | `OCEAN_API_TOKEN` | docs 2026-11-08 |
| [Openmart](https://app.openmart.com/api-docs) `openmart` | A local business database: search businesses and brands, with company enrichment from a website. | 5 read | key in header | `OPENMART_API_KEY` | docs 2026-11-08 |
| [PredictLeads](https://docs.predictleads.com/v3/api_endpoints) `predictleads` | Company intelligence: job openings, technology detections, news and financing events, connections and more for 120M+ companies. | 26 read | key in header | `PREDICTLEADS_API_KEY`, `PREDICTLEADS_API_TOKEN` | docs 2026-11-08 |
| [Sumble](https://docs.sumble.com/api/api) `sumble` | Organization, people, job post and team data: what companies use, who works there and buying signals. | 10 read | key in header | `SUMBLE_API_KEY` | docs 2026-11-08 |
| [The Companies API](https://www.thecompaniesapi.com/api) `thecompaniesapi` | Company enrichment from a domain, email or social URL, plus segmentation search over millions of companies. | 10 read | key in header | `THECOMPANIESAPI_API_TOKEN` | docs 2026-11-08 |
| [TheirStack](https://theirstack.com/en/docs/what-is-theirstack) `theirstack` | Job postings and company technographics: search jobs and companies by hiring signals, technologies and firmographics. | 9 read | key in header | `THEIRSTACK_API_KEY` | docs 2026-11-08 |

### finance

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [CoinGecko](https://docs.coingecko.com/reference/introduction) `coingecko` | Crypto prices, market data, history, OHLC candles and trending coins from CoinGecko. | 11 read | key in header | `COINGECKO_DEMO_API_KEY`, `COINGECKO_PRO_API_KEY` | docs 2026-11-08 |
| [EODHD](https://eodhd.com/financial-apis/) `eodhd` | End-of-day and delayed prices, ticker search and fundamentals for stocks, ETFs, forex and crypto on global exchanges. | 4 read | key in query | `EODHD_API_TOKEN` | docs 2026-11-08 |
| [Financial Datasets](https://docs.financialdatasets.ai/introduction) `financialdatasets` | US public-company prices, financial statements, metrics, SEC filings, insider and institutional holdings, and macro series. | 29 read | key in header | `FINANCIAL_DATASETS_API_KEY` | docs 2026-11-08 |
| [Finnhub](https://finnhub.io/docs/api) `finnhub` | Stock quotes, candles, company profiles, fundamentals, earnings and market news from Finnhub. | 10 read | key in query | `FINNHUB_API_KEY` | docs 2026-11-08 |
| [Marketstack](https://docs.apilayer.com/marketstack/docs/marketstack-api-v2-v-2-0-0) `marketstack` | Worldwide end-of-day and intraday stock prices, tickers, exchanges, dividends and splits from Marketstack (APILayer). | 9 read | key in query | `MARKETSTACK_API_KEY` | docs 2026-11-08 |
| [Polygon.io (Massive)](https://polygon.io/docs/rest/stocks/aggregates) `polygon` | US stock bars, snapshots, trades, reference data, dividends and news from Polygon.io, now branded Massive. | 10 read | key in query | `POLYGON_API_KEY` | docs 2026-11-08 |
| [Tiingo](https://www.tiingo.com/documentation/general/overview) `tiingo` | End-of-day and intraday stock prices, crypto prices and financial news from Tiingo. | 7 read | key in header | `TIINGO_API_KEY` | docs 2026-11-08 |
| [Twelve Data](https://twelvedata.com/docs) `twelvedata` | Prices, quotes, time series, symbol search and fundamentals for stocks, forex, crypto and ETFs worldwide. | 9 read | key in header | `TWELVEDATA_API_KEY` | docs 2026-11-08 |

### search

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Atly](https://agentic-api.atly.com/v2/docs) `atly` | Review-based place discovery: where to go for an intent (work-friendly, gluten-free, first date) near a location, with reasons. | 5 read | key in header | `ATLY_API_KEY` | docs 2026-11-08 |
| [Brave Search](https://api-dashboard.search.brave.com/documentation) `bravesearch` | Web, news, video and image search plus LLM-ready page context from Brave's independent index. | 7 read | key in header | `BRAVE_SEARCH_API_KEY` | docs 2026-11-08 |
| [Exa](https://exa.ai/docs/reference/getting-started) `exa` | Neural web search, page contents, cited answers, Agent runs and Websets from Exa. | 9 read, 3 write | key in header | `EXA_API_KEY` | docs 2026-11-08 |
| [Keenable](https://docs.keenable.ai/api-reference) `keenable` | Web search and page fetch on Keenable's own index, built for AI agents. | 2 read | key in header | `KEENABLE_API_KEY` | docs 2026-11-08 |
| [Linkup](https://docs.linkup.so/pages/documentation/get-started/introduction) `linkup` | Agentic web search, page fetch and asynchronous research with sourced answers from Linkup. | 6 read | key in header | `LINKUP_API_KEY` | docs 2026-11-08 |
| [Litescrape](https://litescrape.com/docs) `litescrape` | Low-cost SERP and web-data API: Google, Bing, DuckDuckGo, maps, app stores, review sites and page fetch. | 37 read | key in header | `LITESCRAPE_API_KEY` | docs 2026-11-08 |
| [Octen](https://docs.octen.ai/) `octen` | Real-time web, news and broad search plus URL content extraction from Octen. | 4 read | key in header | `OCTEN_API_KEY` | docs 2026-11-08 |
| [Parallel](https://docs.parallel.ai/getting-started/overview) `parallel` | Web search, URL extraction, deep-research Tasks and people/company discovery from Parallel Web Systems. | 8 read | key in header | `PARALLEL_API_KEY` | docs 2026-11-08 |
| [Perplexity](https://docs.perplexity.ai/docs/getting-started/overview) `perplexity` | Perplexity's Search API, Agent API with web search, and embeddings. | 5 read, 1 write | key in header | `PERPLEXITY_API_KEY` | docs 2026-11-08 |
| [Search1API](https://s1.dev/docs) `search1api` | Web, news and Maps search, page reading, screenshots, extraction and site crawling through one API. | 12 read | key in header | `SEARCH1API_KEY` | docs 2026-11-08 |
| [SerpApi](https://serpapi.com/search-api) `serpapi` | Structured search results from Google, Google News, Maps, Play and YouTube, with an archive of past searches. | 8 read | key in query | `SERPAPI_API_KEY` | docs 2026-11-08 |
| [Tavily](https://docs.tavily.com/documentation/api-reference) `tavily` | Tavily's web search, extraction, crawl, map and research APIs for AI agents. | 7 read | key in header | `TAVILY_API_KEY` | docs 2026-11-08 |
| [Valyu](https://docs.valyu.ai/api-reference/overview) `valyu` | Search API for agents over the web, SEC filings, market data, economics, papers, trials and patents, plus async DeepResearch reports. | 9 read, 1 write | key in header | `VALYU_API_KEY` | docs 2026-11-08 |
| [You.com](https://you.com/docs/welcome) `you` | You.com's cited Answer, Research and Finance Research APIs. | 5 read | key in header | `YOU_API_KEY` | docs 2026-11-08 |

### scraping

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Apify](https://docs.apify.com/api/v2) `apify` | Run Apify Actors (ready-made scrapers for Google, LinkedIn, Meta ads, TikTok, Lazada, Weibo and more), poll their runs and read the datasets they produce. | 11 read, 4 write | key in header | `APIFY_API_TOKEN` | docs 2026-11-08 |
| [Bright Data](https://docs.brightdata.com/) `brightdata` | Pre-built site scrapers, Web Unlocker, SERP API and Marketplace datasets from Bright Data. | 9 read | key in header | `BRIGHTDATA_API_TOKEN` | docs 2026-11-08 |
| [Firecrawl](https://docs.firecrawl.dev/introduction) `firecrawl` | Scrape, map, crawl, search and extract the web, returning clean markdown and structured data. | 12 read, 1 write | key in header | `FIRECRAWL_API_KEY` | docs 2026-11-08 |
| [Olostep](https://docs.olostep.com/) `olostep` | Scrape, crawl, map, search and answer over the web with Olostep's API. | 12 read | key in header | `OLOSTEP_API_KEY` | docs 2026-11-08 |
| [ScrapeGraphAI](https://docs.scrapegraphai.com/api-reference/introduction) `scrapegraphai` | AI-powered scrape, extract, search and crawl with natural-language prompts (ScrapeGraphAI v2 API). | 8 read, 3 write | key in header | `SGAI_API_KEY` | docs 2026-11-08 |
| [Spider Cloud](https://spider.cloud/docs/api) `spidercloud` | Spider's crawl, scrape, links, search and screenshot API for AI agents and RAG. | 6 read | key in header | `SPIDER_API_KEY` | docs 2026-11-08 |
| [TinyFish](https://docs.tinyfish.ai/) `tinyfish` | Hosted browser-agent automation and deep research from TinyFish. | 4 read, 3 write | key in header | `TINYFISH_API_KEY` | docs 2026-11-08 |

### ai

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Google AI (Gemini API)](https://ai.google.dev/gemini-api/docs) `google-ai` | Gemini models for text, images (Nano Banana), video (Omni), speech (TTS) and music (Lyria) through the Gemini Interactions API. | 7 read | key in header | `GEMINI_API_KEY` | docs 2026-11-08 |
| [OpenRouter](https://openrouter.ai/docs/api-reference/overview) `openrouter` | One OpenAI-style API in front of hundreds of language, image, speech and video models from many providers. | 11 read | key in header | `OPENROUTER_API_KEY` | docs 2026-11-08 |

### media-generation

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [DansUGC](https://dansugc.com/docs) `dansugc` | A marketplace of real-creator UGC videos: search and license the B-roll library, browse creator video formats, and track custom video orders. | 6 read, 1 write | key in header | `DANSUGC_API_KEY` | docs 2026-11-08 |
| [Fish Audio](https://docs.fish.audio/api-reference/introduction) `fishaudio` | Text-to-speech, voice design and voice-model management from Fish Audio. | 6 read, 1 write | key in header | `FISH_API_KEY` | docs 2026-11-08 |
| [MiniMax](https://platform.minimax.io/docs/api-reference/api-overview) `minimax` | MiniMax speech, video (H3), image, music and chat models through the MiniMax API Platform. | 9 read | key in header | `MINIMAX_API_KEY` | docs 2026-11-08 |
| [PiAPI](https://piapi.ai/docs/overview) `piapi` | Unified gateway to image, video and audio generation models (Flux, Kling, Midjourney, Luma and more) through two task endpoints. | 4 read, 1 write | key in header | `PIAPI_API_KEY` | docs 2026-11-08 |
| [reAPI](https://reapi.ai/docs) `reapi` | One API and prepaid credits for many image, video, audio and chat models, with an asynchronous task model. | 4 read | key in header | `REAPI_API_KEY` | docs 2026-11-08 |
| [Replicate](https://replicate.com/docs/reference/http) `replicate` | Run open-source and official image, video, audio and language models as asynchronous predictions. | 8 read, 1 write | key in header | `REPLICATE_API_TOKEN` | docs 2026-11-08 |

### messaging

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Slack](https://docs.slack.dev/reference/methods/) `slack` | Your Slack workspace: read channels, history, threads and users, search messages, and post messages and reactions. | 7 read, 2 write | key in header | `SLACK_BOT_TOKEN` | docs 2026-11-08 |

### other

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Google Business Profile](https://developers.google.com/my-business) `google-business-profile` | Your Google Business Profile locations and reviews: list locations, read customer reviews, and reply to them. | 3 read, 2 write | OAuth refresh token | `GOOGLE_BUSINESS_PROFILE_CLIENT_ID`, `GOOGLE_BUSINESS_PROFILE_CLIENT_SECRET`, `GOOGLE_BUSINESS_PROFILE_REFRESH_TOKEN` | docs 2026-11-08 |
<!-- /vendors -->
