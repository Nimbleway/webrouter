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
244 vendors, 2075 endpoints.

### advertising

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Adyntel](https://docs.adyntel.com/) `adyntel` | Competitor ad intelligence: the ads a company runs on Meta, Google, LinkedIn and TikTok, keyword search across ad libraries, and a domain's paid and organic keywords. | 9 read | key in body | `ADYNTEL_API_KEY`, `ADYNTEL_EMAIL` | docs 2026-11-08 |
| [Foreplay](https://public.api.foreplay.co/docs) `foreplay` | Search a library of Meta, TikTok and other platform ads and brands: creatives, transcripts, brand lookups by name or domain, and running-ad analytics. | 7 read | key in header | `FOREPLAY_API_KEY` | docs 2026-11-09 |
| [Google Ads](https://developers.google.com/google-ads/api/rest/overview) `google-ads` | Your Google Ads accounts: GAQL reports on campaigns, spend, clicks and conversions, Keyword Planner ideas and volumes, and budget changes. | 6 read, 2 write | OAuth refresh token | `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` | docs 2026-11-08 |
| [Meta Ad Library](https://www.facebook.com/ads/library/api/) `meta-ad-library` | Search the public Meta Ad Library: ads running on Facebook, Instagram and other Meta apps by keyword or page, including political and issue ads. | 1 read | key in query | `META_AD_LIBRARY_ACCESS_TOKEN` | docs 2026-11-08 |
| [Meta Ads (Marketing API)](https://developers.facebook.com/docs/marketing-apis) `meta-ads` | Your Facebook and Instagram ad accounts: account details, campaigns, and performance insights (spend, reach, clicks, conversions) at any level and breakdown. | 3 read | key in query | `META_ADS_ACCESS_TOKEN` | docs 2026-11-08 |
| [Pinterest Ads](https://developers.pinterest.com/docs/api/v5/) `pinterest-ads` | Your Pinterest ad accounts: list accounts, campaigns, ad groups and ads, and pull analytics for accounts and campaigns. | 6 read | OAuth refresh token | `PINTEREST_APP_ID`, `PINTEREST_APP_SECRET`, `PINTEREST_REFRESH_TOKEN` | docs 2026-11-08 |
| [Snapchat Ads](https://developers.snap.com/api/marketing-api/Ads-API/authentication) `snapchat-ads` | Your Snapchat ad accounts: organizations, ad accounts, campaigns and ads, plus delivery stats for any of them. | 7 read | OAuth refresh token | `SNAPCHAT_CLIENT_ID`, `SNAPCHAT_CLIENT_SECRET`, `SNAPCHAT_REFRESH_TOKEN` | docs 2026-11-08 |
| [TikTok Ads (Marketing API)](https://business-api.tiktok.com/portal/docs) `tiktok-ads` | TikTok API for Business: ad accounts, campaigns, ad groups, ads and synchronous performance reports for the advertisers that authorized your app. | 5 read | key in header | `TIKTOK_ADS_ACCESS_TOKEN` | docs 2026-11-09 |
| [Upspring](https://public-api.upspring.ai/v1/docs/ui) `upspring` | Competitive ad intelligence: live ads across Meta, TikTok, YouTube, Google, LinkedIn and more, broken into creative dimensions, with advertisers graded D to A+ on estimated spend, traffic and sales. | 8 read | key in header | `UPSPRING_API_KEY` | docs 2026-11-09 |

### analytics

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Cloudflare](https://developers.cloudflare.com/api/) `cloudflare` | Zone traffic, firewall and security analytics through Cloudflare's GraphQL Analytics API, plus zones and DNS records from the v4 REST API. | 5 read | key in header | `CLOUDFLARE_API_TOKEN` | docs 2026-11-09 |
| [Google Analytics 4 (Data API)](https://developers.google.com/analytics/devguides/reporting/data/v1) `google-analytics` | Reports from your Google Analytics 4 properties: users, sessions, events and conversions by any dimension, plus realtime and the dimension/metric catalog. | 3 read | OAuth refresh token | `GOOGLE_ANALYTICS_CLIENT_ID`, `GOOGLE_ANALYTICS_CLIENT_SECRET`, `GOOGLE_ANALYTICS_REFRESH_TOKEN` | docs 2026-11-08 |
| [Google Tag Manager](https://developers.google.com/tag-platform/tag-manager/api/reference/rest/v2) `google-tag-manager` | Your Tag Manager accounts, containers and workspaces: read tags, triggers and variables, create container versions, and publish them live. | 7 read, 2 write | OAuth refresh token | `GOOGLE_TAG_MANAGER_CLIENT_ID`, `GOOGLE_TAG_MANAGER_CLIENT_SECRET`, `GOOGLE_TAG_MANAGER_REFRESH_TOKEN` | docs 2026-11-08 |
| [HubSpot](https://developers.hubspot.com/docs/api-reference/legacy/crm/understanding-the-crm) `hubspot` | HubSpot CRM records (contacts, companies, deals, tickets), CRM search, deal pipelines and stages, and owners through the CRM v3 API. | 6 read | key in header | `HUBSPOT_ACCESS_TOKEN` | docs 2026-11-09 |
| [Klaviyo](https://developers.klaviyo.com/en/reference/api_overview) `klaviyo` | Email, SMS and push campaign and flow performance (opens, clicks, conversions, revenue), metrics and their aggregates from a Klaviyo account. | 6 read | key in header | `KLAVIYO_API_KEY` | docs 2026-11-09 |
| [PostHog](https://posthog.com/docs/api) `posthog` | Product analytics from a PostHog project: SQL (HogQL) queries over events and persons, saved insights and dashboards, persons, events and feature flags. | 9 read | key in header | `POSTHOG_PERSONAL_API_KEY` | docs 2026-11-09 |
| [Similarweb](https://developers.similarweb.com/docs/similarweb-web-traffic-api) `similarweb` | Estimated website traffic and engagement, marketing channel mix, referrals, country split, global rank, similar sites and search keywords for any domain. | 8 read | key in query | `SIMILARWEB_API_KEY` | docs 2026-11-09 |

### seo

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Ahrefs](https://docs.ahrefs.com/api/docs/introduction.md) `ahrefs` | Ahrefs API v3: Domain Rating, backlinks, referring domains, organic keywords, top pages and keyword metrics. | 10 read | key in header | `AHREFS_API_KEY` | docs 2026-11-09 |
| [cloro](https://cloro.dev/docs/api-reference/introduction) `cloro` | Structured answers and sources from ChatGPT, Gemini, Perplexity, Google AI Mode, Bing AI Mode, Google Search and Google News, by country. | 12 read | key in header | `CLORO_API_KEY` | docs 2026-11-08 |
| [DataForSEO](https://docs.dataforseo.com/v3/) `dataforseo` | DataForSEO APIs: live and queued Google SERPs, keyword data, DataForSEO Labs and backlinks, billed per task in USD. | 9 read | HTTP Basic | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | docs 2026-11-08 |
| [Google Search Console](https://developers.google.com/webmaster-tools/v1/api_reference_index) `google-search-console` | Your Search Console properties: search performance (clicks, impressions, CTR, position by query, page, country, device and date) and sitemaps. | 3 read, 1 write | OAuth refresh token | `GOOGLE_SEARCH_CONSOLE_CLIENT_ID`, `GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET`, `GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN` | docs 2026-11-08 |
| [Keywords Everywhere](https://api.keywordseverywhere.com/docs/#/) `keywordseverywhere` | Keyword search volume, CPC and competition, related and People Also Search For keywords, domain and URL keywords, traffic and backlinks. | 9 read | key in header | `KEYWORDSEVERYWHERE_API_KEY` | docs 2026-11-09 |
| [Majestic](https://developer-support.majestic.com/api/) `majestic` | Majestic link intelligence: Trust Flow, Citation Flow, backlinks, referring domains, anchors and top pages. | 6 read | key in query | `MAJESTIC_API_KEY` | docs 2026-11-08 |
| [Moz](https://moz.com/help/links-api) `moz` | Moz Links API: Domain Authority, Page Authority, backlinks, anchor text, linking domains and top pages. | 9 read | key in header | `MOZ_API_TOKEN` | docs 2026-11-08 |
| [Semrush](https://developer.semrush.com/api/v3/analytics/basic-docs/) `semrush` | Semrush Analytics API: domain overviews, organic and paid keywords, keyword research and backlink reports. | 14 read | key in query | `SEMRUSH_API_KEY` | docs 2026-11-08 |
| [SE Ranking](https://seranking.com/api/data/) `seranking` | SE Ranking Data API: domain analysis, keyword research and backlink data. | 11 read | key in header | `SERANKING_API_KEY` | docs 2026-11-08 |
| [SpyFu](https://developer.spyfu.com/) `spyfu` | SpyFu competitive research API: domain stats, organic and paid keywords, competitors, top pages and keyword data. | 8 read | key in query | `SPYFU_API_KEY` | docs 2026-11-08 |

### observability

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Datadog](https://docs.datadoghq.com/api/latest/) `datadog` | Metrics, logs, events, monitors, dashboards, hosts, incidents and SLOs from your Datadog organization. | 13 read | key in header | `DD_API_KEY`, `DD_APPLICATION_KEY` | docs 2026-11-08 |
| [Oh Dear](https://ohdear.app/docs/api/introduction) `ohdear` | Website monitoring: monitors, uptime and downtime, response-time metrics, certificate health, broken links and per-check summaries. | 9 read | key in header | `OHDEAR_API_KEY` | docs 2026-11-09 |
| [Sentry](https://docs.sentry.io/api/) `sentry` | Error and performance monitoring: issues, their events and stack traces, explore queries over errors, logs and spans, event volume stats, projects and releases. | 8 read, 1 write | key in header | `SENTRY_AUTH_TOKEN` | docs 2026-11-09 |

### social

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [AnyAPI](https://getanyapi.com/docs) `anyapi` | One pay-per-call wallet in front of many data sources: social profiles, posts, comments and search across Facebook, Instagram, LinkedIn, TikTok, X and Google. | 31 read | key in header | `ANYAPI_API_KEY` | docs 2026-11-08 |
| [Edges](https://docs.edges.run/v1/api/introduction) `edges` | LinkedIn and Sales Navigator data over REST: extract people, companies and posts, run searches, and find profile or company URLs. | 8 read | key in header | `EDGES_API_KEY` | docs 2026-11-09 |
| [Facebook Pages (Graph API)](https://developers.facebook.com/docs/pages-api) `facebook` | Facebook Pages you manage through Meta's Graph API: Page details, the Page feed, and publishing posts and photos. | 3 read, 2 write | key in query | `FACEBOOK_ACCESS_TOKEN` | docs 2026-11-08 |
| [FastMoss](https://developer.fastmoss.com/api/docs/guide/quickStart.html) `fastmoss` | TikTok Shop data: products, shops, creators, videos and rankings, with sales and revenue estimates by region. | 8 read | key in header | `FASTMOSS_API_KEY` | docs 2026-11-09 |
| [Fetchin](https://docs.fetchin.io/) `fetchinio` | Professional-network profiles, posts, comments, reactions and company pages as a credit-billed API. | 10 read | key in header | `FETCHIN_API_KEY` | docs 2026-11-08 |
| [HarvestAPI](https://docs.harvestapi.io) `harvestapi` | LinkedIn data without a LinkedIn login: profiles, companies, jobs, posts, comments and reactions, searchable by filters. | 18 read | key in header | `HARVESTAPI_API_KEY` | docs 2026-11-08 |
| [Influencers Club](https://docs.influencers.club/) `influencersclub` | Creator discovery, similar creators and enrichment (profile, analytics, posts, audience overlap) across social platforms. | 13 read | key in header | `INFLUENCERS_CLUB_API_KEY` | docs 2026-11-08 |
| [Instagram Graph API](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api) `instagram` | Your Instagram professional account through Meta's Graph API: profile, media, insights, comments, other businesses' public data, and publishing. | 7 read, 3 write | key in query | `INSTAGRAM_ACCESS_TOKEN` | docs 2026-11-08 |
| [Just One API](https://docs.justoneapi.com/en/usage) `justoneapi` | Social and e-commerce data from Chinese and global platforms: Xiaohongshu, Weibo, TikTok, Instagram and YouTube profiles, posts, comments and search. | 20 read | key in query | `JUSTONEAPI_TOKEN` | docs 2026-11-08 |
| [LinkedIn](https://learn.microsoft.com/en-us/linkedin/) `linkedin` | LinkedIn's official API for the signed-in member: identity, reading and publishing posts for a member or company page. | 3 read, 2 write | key in header | `LINKEDIN_ACCESS_TOKEN` | docs 2026-11-08 |
| [Postiz](https://docs.postiz.com/public-api/introduction) `postiz` | Schedule and publish posts to connected social channels (X, LinkedIn, Instagram, TikTok, YouTube and more) and read their analytics. | 6 read, 2 write | key in header | `POSTIZ_API_KEY` | docs 2026-11-09 |
| [Scrape Creators](https://docs.scrapecreators.com) `scrapecreators` | Public social media data over one API: TikTok, Instagram, YouTube, X, LinkedIn and Reddit profiles, posts, comments and search. | 26 read | key in header | `SCRAPECREATORS_API_KEY` | docs 2026-11-08 |
| [TikHub](https://docs.tikhub.io/) `tikhub` | Public data from TikTok, Instagram, YouTube and X (and many other platforms) over one API: profiles, posts, comments and search. | 20 read | key in header | `TIKHUB_API_KEY` | docs 2026-11-08 |
| [TikTok Display API](https://developers.tiktok.com/doc/display-api-overview/) `tiktok` | The TikTok Display API: profile and videos of the TikTok user who authorized your app. | 3 read | key in header | `TIKTOK_ACCESS_TOKEN` | docs 2026-11-08 |
| [TwitterScore](https://twitterscore.gitbook.io/twitterscore/developers/api-documentation) `twitterscore` | Influence scores for X (Twitter) accounts, mostly crypto: score history, smart followers by category and tag, mentions and trending accounts. | 10 read | key in query | `TWITTERSCORE_API_KEY` | docs 2026-11-09 |
| [X (Twitter) API](https://docs.x.com/x-api) `x` | X API v2: search recent posts, look up users and posts, read timelines and followers, and publish posts. | 5 read, 1 write | key in header | `X_BEARER_TOKEN` | docs 2026-11-08 |
| [YouTube Data API](https://developers.google.com/youtube/v3/docs) `youtube` | Public YouTube data: videos, channels, playlists, comments and search, read with a Google API key. | 6 read | key in query | `YOUTUBE_API_KEY` | docs 2026-11-08 |

### enrichment

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [AI Ark](https://docs.ai-ark.com) `aiark` | B2B people and company search, verified email and mobile finding, job posts and lookalike company discovery from AI Ark. | 10 read, 1 write | key in header | `AIARK_API_KEY` | docs 2026-11-08 |
| [Airscale](https://docs.airscale.io/api-reference/api-overview) `airscale` | Find professional and personal emails and mobile numbers, search people and companies, and extract LinkedIn profiles. | 10 read | key in header | `AIRSCALE_API_KEY` | docs 2026-11-09 |
| [Anymail Finder](https://anymailfinder.com/email-finder-api/docs) `anymailfinder` | Find and verify business emails by person, company, decision-maker role or LinkedIn URL, charged only for valid hits. | 8 read | key in header | `ANYMAILFINDER_API_KEY` | docs 2026-11-08 |
| [Apollo.io](https://docs.apollo.io/reference/) `apollo` | Search and enrich B2B people and companies, with email and phone reveal, from Apollo's contact database. | 6 read | key in header | `APOLLO_API_KEY` | docs 2026-11-08 |
| [BlitzAPI](https://docs.blitz-api.ai) `blitzapi` | B2B people and company search, LinkedIn-based enrichment, verified work emails, phone numbers and job postings from BlitzAPI. | 19 read | key in header | `BLITZ_API_KEY` | docs 2026-11-08 |
| [Captain Data](https://docs.captaindata.com/v1/introduction) `captaindata` | LinkedIn people and company data: find, search via Sales Navigator queries, enrich profiles, work history, skills and company employees. | 10 read | key in header | `CAPTAINDATA_API_KEY` | docs 2026-11-09 |
| [Clado](https://docs.clado.ai/api-reference/introduction) `clado` | Natural-language people search over LinkedIn profiles, deep research jobs, profile scraping and email/phone enrichment. | 9 read | key in header | `CLADO_API_KEY` | docs 2026-11-09 |
| [Clay](https://developers.clay.com/public-api/quickstart) `clay` | Clay's Public API: people and company search, saved enrichment routines, and queries over your Clay tables. | 7 read, 1 write | key in header | `CLAY_PUBLIC_API_KEY` | docs 2026-11-09 |
| [ContactOut](https://api.contactout.com/) `contactout` | Find work and personal emails and phones from LinkedIn profiles, search people and companies, and verify emails. | 20 read | key in header | `CONTACTOUT_API_KEY` | docs 2026-11-08 |
| [Datagma](https://datagmaapi.readme.io/reference/getting-started-with-your-api) `datagma` | Find work emails and phones, enrich people and companies, and detect job changes. | 6 read | key in query | `DATAGMA_API_KEY` | docs 2026-11-08 |
| [Data Legion](https://www.datalegion.ai/docs/api-reference/overview) `datalegion` | Person and company enrichment and SQL-style search over 188M+ person and 71M+ company profiles, with optional contact data. | 7 read | key in header | `DATALEGION_API_KEY` | docs 2026-11-09 |
| [Dropcontact](https://developer.dropcontact.com/) `dropcontact` | Find and verify professional emails and add company data to contact lists, asynchronously, paying only for verified emails. | 3 read | key in header | `DROPCONTACT_API_KEY` | docs 2026-11-08 |
| [DropLeads](https://dropleads.readme.io/) `dropleads` | Prime-DB people and company search, verified or simple person enrichment, company enrichment and credit balance. | 11 read | key in header | `DROPLEADS_API_KEY` | docs 2026-11-08 |
| [Enrich Layer](https://enrichlayer.com/docs) `enrichlayer` | People, company, contact, job and school data: profile enrichment, lookups, search, emails and phone numbers from Enrich Layer. | 24 read | key in header | `ENRICHLAYER_API_KEY` | docs 2026-11-08 |
| [Enrow](https://docs.enrow.io/api-overview) `enrow` | Find and verify professional emails and find phone numbers, asynchronously, by name, company or LinkedIn URL. | 7 read | key in header | `ENROW_API_KEY` | docs 2026-11-08 |
| [Findymail](https://app.findymail.com/docs/) `findymail` | Find and verify emails and phones from names, domains or LinkedIn profiles, and look up companies and employees. | 11 read, 2 write | key in header | `FINDYMAIL_API_KEY` | docs 2026-11-08 |
| [FullEnrich](https://docs.fullenrich.com/api/v2/general/introduction) `fullenrich` | Waterfall enrichment of work emails, personal emails and mobile phones across 20+ providers, plus people and company search and reverse email lookup. | 10 read | key in header | `FULLENRICH_API_KEY` | docs 2026-11-09 |
| [GetLeads](https://www.getleads.io/docs/) `getleadsio` | B2B contact and company database with structured search, counts, async CSV exports, and person and company enrichment. | 18 read | key in header | `GETLEADS_API_KEY` | docs 2026-11-08 |
| [Happenstance](https://developer.happenstance.ai/api-reference/introduction) `happenstance` | Research a person from a description, and search for people across your network, groups and friends' connections. | 9 read | key in header | `HAPPENSTANCE_API_KEY` | docs 2026-11-09 |
| [Hunter](https://hunter.io/api-documentation/v2) `hunter` | Find and verify professional email addresses by domain or person, and enrich people and companies. | 10 read | key in header | `HUNTER_API_KEY` | docs 2026-11-08 |
| [Icypeas](https://api-doc.icypeas.com/) `icypeas` | Find and verify professional emails, search a people and company database, and scrape profile and company data. | 16 read | key in header | `ICYPEAS_API_KEY` | docs 2026-11-08 |
| [LeadMagic](https://leadmagic.io/docs/api-reference/email-validation) `leadmagic` | Find and validate work emails, personal emails and mobiles from names or profile URLs, and look up profiles and companies. | 8 read | key in header | `LEADMAGIC_API_KEY` | docs 2026-11-08 |
| [LeadOcean](https://leadocean.io/docs) `leadocean` | B2B people and company search, person and company enrichment, work and personal email, phone and reverse lookups, and bulk CSV exports. | 14 read, 1 write | key in header | `LEADOCEAN_API_KEY` | docs 2026-11-08 |
| [Leadsforge](https://api.leadsforge.ai/public/swagger/index.html) `leadsforge` | B2B lead search with email, phone and LinkedIn enrichment, lookalike companies, company followers and Google Maps business discovery. | 16 read | key in header | `LEADSFORGE_API_KEY` | docs 2026-11-08 |
| [Lusha](https://docs.lusha.com/) `lusha` | Search B2B contacts and companies, then reveal emails, phones and firmographics for the ones you pick. | 11 read | key in header | `LUSHA_API_KEY` | docs 2026-11-08 |
| [MoltSets](https://developer.moltsets.com) `moltsets` | B2B people and company search, email and mobile lookup, email validation and reverse lookups. | 15 read | key in header | `MOLTSETS_API_KEY` | docs 2026-11-08 |
| [Nyne.ai](https://api.nyne.ai/documentation) `nyne` | People and company intelligence: person enrichment and search with contacts and social profiles, company enrichment, search, employees and funding. | 9 read | key in header | `NYNE_API_KEY`, `NYNE_API_SECRET` | docs 2026-11-09 |
| [People Data Labs](https://docs.peopledatalabs.com/docs/api-overview) `pdl` | Person and company enrichment, identify and dataset search over PDL's person and company data. | 5 read | key in header | `PDL_API_KEY` | docs 2026-11-08 |
| [Prospeo](https://prospeo.io/api-docs) `prospeo` | Find and verify emails and mobiles, enrich people and companies, and search a B2B people and company database. | 7 read | key in header | `PROSPEO_API_KEY` | docs 2026-11-08 |
| [Quick Enrich](https://app.quickenrich.io/docs) `quickenrich` | Work email and phone lookup by LinkedIn URL or name and company, reverse email lookup, domain and contact discovery, and company search. | 8 read | key in header | `QUICKENRICH_API_KEY` | docs 2026-11-08 |
| [RocketReach](https://docs.rocketreach.co/reference/rocketreach-api) `rocketreach` | Search and look up professionals and companies: work and personal emails, phones, job history and firmographics. | 8 read | key in header | `ROCKETREACH_API_KEY` | docs 2026-11-09 |
| [Sixtyfour](https://docs.sixtyfour.ai/introduction) `sixtyfour` | Research agents and data for people and companies: enrichment, email and phone finding, reverse lookups and filter search. | 15 read | key in header | `SIXTYFOUR_API_KEY` | docs 2026-11-08 |
| [Tomba](https://docs.tomba.io/api) `tomba` | Find, verify and enrich professional emails, phone numbers and company data by domain, name, LinkedIn URL or article. | 17 read | key in header | `TOMBA_API_KEY`, `TOMBA_SECRET_KEY` | docs 2026-11-08 |
| [Village](https://docs.village.ai/api-reference/introduction) `village` | Relationship intelligence: warm-introduction paths to people and companies through your network, with people and company enrichment. | 13 read | key in header | `VILLAGE_API_TOKEN` | docs 2026-11-08 |
| [Whitepages Pro](https://api.whitepages.com/docs) `whitepages` | US people search, reverse phone lookup and property records (owners, residents, tax and mortgage history). | 7 read | key in header | `WHITEPAGES_API_KEY` | docs 2026-11-09 |
| [Wiza](https://docs.wiza.co/) `wiza` | Reveal emails and phones from profile URLs or names, enrich companies, and search prospects. | 5 read | key in header | `WIZA_API_KEY` | docs 2026-11-08 |
| [ZoomInfo](https://docs.gtm.ai/reference/api-overview) `zoominfo` | B2B contact and company search and enrichment, scoops, intent and lookups from ZoomInfo's Data API. | 9 read | OAuth refresh token | `ZOOMINFO_CLIENT_ID`, `ZOOMINFO_CLIENT_SECRET` | docs 2026-11-09 |

### email-verification

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [BounceBan](https://bounceban.com/public/doc/api.html) `bounceban` | Email verification that also scores catch-all and gateway-protected addresses, with single, bulk-list and account calls. | 9 read, 2 write | key in header | `BOUNCEBAN_API_KEY` | docs 2026-11-08 |
| [Filtrat](https://filtrat.io/v1/docs) `filtrat` | Email verification, work-email finding from a name and domain or a LinkedIn URL, bulk list jobs, and email-to-LinkedIn lookup. | 8 read, 1 write | key in header | `FILTRAT_API_KEY` | docs 2026-11-08 |
| [MillionVerifier](https://developer.millionverifier.com/) `millionverifier` | Real-time email verification with a quality grade, result code, role and free-provider flags, and a credit balance check. | 2 read | key in query | `MILLIONVERIFIER_API_KEY` | docs 2026-11-08 |
| [OrbiSearch](https://github.com/OrbiSearch/orbisearch-api/blob/main/docs/api-reference.md) `orbisearch` | Email verification that resolves catch-all domains, single or in bulk. | 4 read | key in header | `ORBISEARCH_API_KEY` | docs 2026-11-09 |
| [Scrubby](https://docs.scrubby.io/) `scrubby` | Email validation with instant single checks, queued bulk batches and slower deep verification. | 5 read | key in header | `SCRUBBY_API_KEY` | docs 2026-11-08 |
| [Trestle](https://docs.trestleiq.com/api-reference) `trestleiq` | Phone validation, caller identification and lead-contact grading (phone, email, address, IP) from Trestle's identity data. | 3 read | key in header | `TRESTLEIQ_API_KEY` | docs 2026-11-08 |
| [Kitt](https://documenter.getpostman.com/view/479833/2s93m62NHf) `trykitt` | Find and verify work emails in real time, with a credit balance and job lookup. | 5 read | key in header | `TRYKITT_API_KEY` | docs 2026-11-08 |
| [ZeroBounce](https://www.zerobounce.net/docs/email-validation-api-quickstart) `zerobounce` | Real-time email validation with status, sub-status, domain and activity signals, plus credit and usage checks. | 3 read | key in query | `ZEROBOUNCE_API_KEY` | docs 2026-11-08 |

### company-data

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [akta.pro](https://docs.akta.pro) `akta` | Company data, headcount, traffic, jobs, reviews, news signals and generated company lists from akta.pro. | 10 read | key in header | `AKTA_API_KEY` | docs 2026-11-08 |
| [Ashby Job Board](https://developers.ashbyhq.com/docs/public-job-posting-api) `ashby` | A company's public Ashby job board: listed roles with descriptions, location, workplace type and optional compensation. | 1 read | none | none | docs 2026-11-09 |
| [Aviato](https://docs.data.aviato.co) `aviato` | Company, funding, investor and people data, contact info and search through the Aviato data engine. | 33 read | key in header | `AVIATO_API_KEY` | docs 2026-11-08 |
| [Context.dev](https://docs.context.dev) `branddev` | Brand and company data, natural-language company and people search, person enrichment, news, plus web scrape, crawl, map and search from Context.dev. | 14 read | key in header | `CONTEXT_DEV_API_KEY` | docs 2026-11-08 |
| [Brandfetch](https://docs.brandfetch.com) `brandfetch` | Brand data by domain, ticker, ISIN or crypto symbol: logos, colors, fonts, descriptions and company facts; plus brand-name search and brand context. | 7 read | key in header and query | `BRANDFETCH_API_KEY`, `BRANDFETCH_CLIENT_ID` | docs 2026-11-09 |
| [BuiltWith](https://api.builtwith.com) `builtwith` | Website technology profiling: the tech stack and metadata of a domain, sites using a technology, related sites and domains for a company name. | 8 read | key in header | `BUILTWITH_API_KEY` | docs 2026-11-09 |
| [CNPJá](https://api.cnpja.com/docs) `cnpja` | Brazilian company registry (CNPJ) data: establishments, companies with partners, Simples Nacional and Receita Federal records, and filtered searches. | 9 read | key in header | `CNPJA_API_KEY` | docs 2026-11-09 |
| [Companies House](https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference) `companies-house` | The UK company register: company profiles, officers, filing history, persons with significant control, charges and insolvency. | 9 read | HTTP Basic | `COMPANIES_HOUSE_API_KEY` | docs 2026-11-09 |
| [CompanyEnrich](https://docs.companyenrich.com) `companyenrich` | Company and people search, enrichment, workforce insights, lookalikes and work-email lookup from CompanyEnrich. | 25 read | key in header | `COMPANYENRICH_API_KEY` | docs 2026-11-08 |
| [Coresignal](https://docs.coresignal.com) `coresignal` | Fresh company, employee and job data from professional-network sources: search for IDs, then collect or enrich records. | 6 read | key in header | `CORESIGNAL_API_KEY` | docs 2026-11-08 |
| [Corpo API](https://docs.corpoapi.com) `corpoapi` | Official company-register lookups by jurisdiction: profiles, officers, charges, insolvency, shareholders, financials, ownership and VAT checks. | 16 read | key in header | `CORPOAPI_API_KEY` | docs 2026-11-08 |
| [Crunchbase](https://data.crunchbase.com/docs/using-the-api) `crunchbase` | Company, funding, investor and people data from the Crunchbase REST API (v4): entity lookup, search and autocomplete. | 6 read | key in header | `CRUNCHBASE_API_KEY` | docs 2026-11-08 |
| [Crustdata](https://docs.crustdata.com) `crustdata` | Company and people search, enrichment, job listings and web search from Crustdata's datasets. | 11 read | key in header | `CRUSTDATA_API_KEY` | docs 2026-11-08 |
| [Diffbot](https://www.diffbot.com/docs/) `diffbot` | Diffbot Knowledge Graph: search organizations, people and articles with DQL, and enhance records from partial data. | 4 read | key in query | `DIFFBOT_TOKEN` | docs 2026-11-08 |
| [Extruct AI](https://docs.extruct.ai/api-reference/introduction) `extruct` | Company search by natural-language description, lookalikes of a reference company, company profiles, and async Deep Search and Deep Research tasks. | 9 read | key in header | `EXTRUCT_API_TOKEN` | docs 2026-11-09 |
| [Fantastic.jobs](https://developer.fantastic.jobs) `fantasticjobs` | A job-postings feed from 58 ATS platforms and LinkedIn, Wellfound and Y Combinator, with AI-derived salary, seniority and remote fields and company details. | 6 read | key in header | `FANTASTICJOBS_API_KEY` | docs 2026-11-09 |
| [Fiber AI](https://api.fiber.ai/llms.txt) `fiber-ai` | B2B company and people search, LinkedIn live enrichment, and email and phone reveal. | 13 read | key in header | `FIBER_API_KEY` | docs 2026-11-08 |
| [Fundable](https://docs.tryfundable.ai/api-reference/introduction) `fundable` | Venture funding data: deals (rounds), startups with their latest round, investor firms and people, by filters or identifier. | 10 read | key in header | `FUNDABLE_API_KEY` | docs 2026-11-09 |
| [GLEIF](https://documenter.getpostman.com/view/7679680/SVYrrxuU) `gleif` | The Global LEI Index: legal entity records by LEI, name, BIC or ISIN, with direct and ultimate parents and children, keyless. | 9 read | none | none | docs 2026-11-09 |
| [Greenhouse Job Board](https://docs.greenhouse.io/job-board.html) `greenhouse` | A company's public Greenhouse job board: open roles with descriptions, departments, offices and pay ranges, keyless. | 5 read | none | none | docs 2026-11-09 |
| [Lever Postings](https://github.com/lever/postings-api) `lever` | A company's published Lever job postings, filterable by location, team, department and commitment, keyless. | 2 read | none | none | docs 2026-11-09 |
| [Limadata](https://limadata.com/api) `limadata` | Real-time B2B people and company enrichment, database search, contact finding and company job postings. | 4 read | key in header | `LIMADATA_API_KEY` | docs 2026-11-08 |
| [Ocean.io](https://app.ocean.io/docs) `oceanio` | Lookalike company and people search, company and person enrichment and bulk lookups. | 15 read | key in header | `OCEAN_API_TOKEN` | docs 2026-11-08 |
| [OpenFunnel](https://docs.openfunnel.dev) `openfunnel` | Find lookalike companies from seeds or a description, companies by the tech in their job posts, and qualify one company against criteria. | 8 read | key in header | `OPENFUNNEL_API_KEY` | docs 2026-11-09 |
| [Openmart](https://app.openmart.com/api-docs) `openmart` | A local business database: search businesses and brands, with company enrichment from a website. | 5 read | key in header | `OPENMART_API_KEY` | docs 2026-11-08 |
| [Origami](https://docs.origami.chat/v3/overview) `origami` | An AI agent that builds researched lead lists from a plain-English brief; read the lists and rows back. | 7 read, 2 write | key in header | `ORIGAMI_API_KEY` | docs 2026-11-09 |
| [PredictLeads](https://docs.predictleads.com/v3/api_endpoints) `predictleads` | Company intelligence: job openings, technology detections, news and financing events, connections and more for 120M+ companies. | 26 read | key in header | `PREDICTLEADS_API_KEY`, `PREDICTLEADS_API_TOKEN` | docs 2026-11-08 |
| [Recent Funding](https://recentfunding.com/docs/) `recentfunding` | Recently funded startups with sourced round amounts, investors, founders, acquisitions and new VC funds. | 10 read | key in header | `RECENTFUNDING_API_KEY` | docs 2026-11-09 |
| [Signalbase](https://docs.trysignalbase.com) `signalbase` | Funding, acquisition, hiring and job-change signals, investors, companies and people. | 7 read | key in header | `SIGNALBASE_API_KEY` | docs 2026-11-09 |
| [SmartRecruiters](https://developers.smartrecruiters.com/docs/posting-api) `smartrecruiters` | Public job postings and departments of companies hiring on SmartRecruiters (Posting API). | 3 read | key in header | `SMARTRECRUITERS_API_KEY` | docs 2026-11-09 |
| [Sumble](https://docs.sumble.com/api/api) `sumble` | Organization, people, job post and team data: what companies use, who works there and buying signals. | 10 read | key in header | `SUMBLE_API_KEY` | docs 2026-11-08 |
| [The Companies API](https://www.thecompaniesapi.com/api) `thecompaniesapi` | Company enrichment from a domain, email or social URL, plus segmentation search over millions of companies. | 10 read | key in header | `THECOMPANIESAPI_API_TOKEN` | docs 2026-11-08 |
| [TheirStack](https://theirstack.com/en/docs/what-is-theirstack) `theirstack` | Job postings and company technographics: search jobs and companies by hiring signals, technologies and firmographics. | 9 read | key in header | `THEIRSTACK_API_KEY` | docs 2026-11-08 |
| [VOYGR](https://api.voygr.tech/docs) `voygr` | Check that a business exists at an address, enrich place records (hours, contacts, socials, custom fields), and place AI phone calls to US businesses. | 8 read, 1 write | key in header | `VOYGR_API_KEY` | docs 2026-11-09 |
| [Workable](https://help.workable.com/hc/en-us/articles/115012771647) `workable` | Published jobs, locations and departments from a company's public Workable careers page. | 3 read | none | none | docs 2026-11-09 |

### finance

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Alpha Vantage](https://www.alphavantage.co/documentation/) `alphavantage` | Stock, forex and crypto prices, company fundamentals, earnings, market news sentiment and technical indicators. | 10 read | key in query | `ALPHAVANTAGE_API_KEY` | docs 2026-11-09 |
| [Bank of Canada](https://www.bankofcanada.ca/valet/docs) `bankofcanada` | Bank of Canada Valet API: exchange rates, interest rates and other Bank of Canada statistical series, keyless. | 7 read | none | none | docs 2026-11-09 |
| [Bank of England](https://www.bankofengland.co.uk/boeapps/database/help.asp) `bankofengland` | Bank of England Statistical Interactive Database: Bank Rate, SONIA, yields, exchange rates and other series by code, as CSV or XML, keyless. | 2 read | none | none | docs 2026-11-09 |
| [Benzinga](https://docs.benzinga.com/introduction/introduction) `benzinga` | Benzinga financial news, analyst ratings, consensus price targets, and earnings, guidance and dividend calendars. | 7 read | key in query | `BENZINGA_API_KEY` | docs 2026-11-09 |
| [Binance](https://developers.binance.com/docs/binance-spot-api-docs/rest-api) `binance` | Binance spot exchange public market data: prices, 24h tickers, order books, trades, candles and symbol rules, keyless. | 8 read | none | none | docs 2026-11-09 |
| [US Bureau of Labor Statistics](https://www.bls.gov/developers/api_signature_v2.htm) `bls` | US labor and price statistics (CPI, PPI, employment, unemployment, wages) as time series from the BLS Public Data API v2. | 5 read | key in query | `BLS_API_KEY` | docs 2026-11-09 |
| [US CFTC Commitments of Traders](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ExplanatoryNotes/index.htm) `cftc` | Weekly Commitments of Traders futures and options positioning (Legacy, Disaggregated, TFF, Supplemental) from the CFTC Public Reporting Environment. | 4 read | key in header | `CFTC_API_KEY` | docs 2026-11-09 |
| [CoinGecko](https://docs.coingecko.com/reference/introduction) `coingecko` | Crypto prices, market data, history, OHLC candles and trending coins from CoinGecko. | 11 read | key in header | `COINGECKO_DEMO_API_KEY`, `COINGECKO_PRO_API_KEY` | docs 2026-11-08 |
| [CoinMarketCap](https://coinmarketcap.com/api/documentation/) `coinmarketcap` | Crypto listings, latest and historical quotes, OHLCV, coin metadata, global market metrics and the Fear and Greed index from CoinMarketCap. | 10 read | key in header | `CMC_PRO_API_KEY` | docs 2026-11-09 |
| [Commodity Price API](https://commoditypriceapi.com/docs) `commoditypriceapi` | Latest, historical, intraday and time-series prices for metals, oil, agricultural and other commodities. | 8 read | key in header | `COMMODITYPRICEAPI_API_KEY` | docs 2026-11-09 |
| [DefiLlama](https://api-docs.defillama.com/) `defillama` | DeFi protocol and chain TVL, token prices by contract, stablecoin supply, yield pools, DEX volumes and protocol fees, keyless. | 10 read | none | none | docs 2026-11-09 |
| [European Central Bank](https://data.ecb.europa.eu/help/api/overview) `ecb` | ECB Data Portal SDMX API: euro reference exchange rates, ECB interest rates, inflation, money and banking statistics, keyless. | 4 read | none | none | docs 2026-11-09 |
| [EDINET](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf) `edinet` | Japan FSA's EDINET disclosure system: daily lists of securities filings (annual and quarterly reports, large shareholding reports) and the filings themselves as XBRL, PDF or CSV. | 2 read | key in query | `EDINET_API_KEY` | docs 2026-11-09 |
| [US Energy Information Administration](https://www.eia.gov/opendata/documentation.php) `eia` | US energy statistics (electricity, petroleum, natural gas, coal, prices, production, consumption) from the EIA Open Data API v2. | 8 read | key in query | `EIA_API_KEY` | docs 2026-11-09 |
| [EODHD](https://eodhd.com/financial-apis/) `eodhd` | End-of-day and delayed prices, ticker search and fundamentals for stocks, ETFs, forex and crypto on global exchanges. | 4 read | key in query | `EODHD_API_TOKEN` | docs 2026-11-08 |
| [Ethplorer](https://github.com/EverexIO/Ethplorer/wiki/Ethplorer-API) `ethplorer` | Ethereum token and wallet data from Ethplorer: token info and prices, wallet balances, token transfers, transactions and top holders. | 8 read | key in query | `ETHPLORER_API_KEY` | docs 2026-11-09 |
| [Eurostat](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access) `eurostat` | Eurostat EU statistics (prices and inflation, energy, labour, trade, GDP, population, regions) as JSON-stat or SDMX, keyless. | 4 read | none | none | docs 2026-11-09 |
| [Financial Datasets](https://docs.financialdatasets.ai/introduction) `financialdatasets` | US public-company prices, financial statements, metrics, SEC filings, insider and institutional holdings, and macro series. | 29 read | key in header | `FINANCIAL_DATASETS_API_KEY` | docs 2026-11-08 |
| [Finnhub](https://finnhub.io/docs/api) `finnhub` | Stock quotes, candles, company profiles, fundamentals, earnings and market news from Finnhub. | 10 read | key in query | `FINNHUB_API_KEY` | docs 2026-11-08 |
| [Fiscal.ai](https://docs.fiscal.ai/) `fiscal-ai` | Public company financials from Fiscal.ai: as-reported and standardized statements, ratios, segments and KPIs, stock prices, filings, news and earnings call transcripts for companies in the US, Canada, UK and EU. | 10 read | key in header | `FISCAL_API_KEY` | docs 2026-11-09 |
| [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs) `fmp` | Stock quotes, end-of-day prices, company profiles, financial statements, key metrics, news, earnings calendar and Treasury rates from Financial Modeling Prep (FMP). | 10 read | key in header | `FMP_API_KEY` | docs 2026-11-09 |
| [FRED (St. Louis Fed)](https://fred.stlouisfed.org/docs/api/fred/) `fred` | US and international economic time series from the Federal Reserve Bank of St. Louis: series search, metadata, observations, categories and release calendars. | 7 read | key in query | `FRED_API_KEY` | docs 2026-11-09 |
| [Kalshi](https://docs.kalshi.com/getting_started/quick_start_market_data) `kalshi` | Public market data from Kalshi, a prediction market exchange: series, events, markets, order books, trades and candlesticks. | 8 read | none | none | docs 2026-11-09 |
| [Marketstack](https://docs.apilayer.com/marketstack/docs/marketstack-api-v2-v-2-0-0) `marketstack` | Worldwide end-of-day and intraday stock prices, tickers, exchanges, dividends and splits from Marketstack (APILayer). | 9 read | key in query | `MARKETSTACK_API_KEY` | docs 2026-11-08 |
| [Nasdaq Data Link](https://data.nasdaq.com/tablesapi) `nasdaq-data-link` | Financial, economic and alternative datasets from Nasdaq Data Link through its Tables API: filter rows and columns of any table you can access, and read table metadata. | 2 read | key in query | `NASDAQ_DATA_LINK_API_KEY` | docs 2026-11-09 |
| [Federal Reserve Bank of New York Markets Data](https://markets.newyorkfed.org/static/docs/markets-api.html) `newyorkfed` | US reference rates (SOFR, EFFR, OBFR, TGCR, BGCR), SOMA holdings, repo and reverse repo operations and primary dealer statistics from the New York Fed. | 10 read | none | none | docs 2026-11-09 |
| [Polygon.io (Massive)](https://polygon.io/docs/rest/stocks/aggregates) `polygon` | US stock bars, snapshots, trades, reference data, dividends and news from Polygon.io, now branded Massive. | 10 read | key in query | `POLYGON_API_KEY` | docs 2026-11-08 |
| [Polymarket](https://docs.polymarket.com/) `polymarket` | Public prediction-market data from Polymarket: search, events and markets (Gamma API), order books, prices and price history (CLOB API), trades, holders and open interest (Data API). | 10 read | none | none | docs 2026-11-09 |
| [SEC API (sec-api.io)](https://sec-api.io/docs) `sec-api` | SEC EDGAR filings as JSON from sec-api.io: filing search, full-text search, insider trades (Forms 3/4/5), 13F institutional holdings, XBRL-to-JSON financials, filing section extraction and ticker/CIK/CUSIP mapping. | 10 read | key in header | `SEC_API_KEY` | docs 2026-11-09 |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) `sec-edgar` | US public company filing histories and XBRL financial data (company facts, single concepts, cross-company frames) from the SEC's free EDGAR APIs. | 5 read | none | none | docs 2026-11-09 |
| [Stripe](https://docs.stripe.com/api) `stripe` | Payments data from a Stripe account: charges, customers, payment intents, invoices, subscriptions and the balance. | 10 read | key in header | `STRIPE_SECRET_KEY` | docs 2026-11-09 |
| [Tiingo](https://www.tiingo.com/documentation/general/overview) `tiingo` | End-of-day and intraday stock prices, crypto prices and financial news from Tiingo. | 7 read | key in header | `TIINGO_API_KEY` | docs 2026-11-08 |
| [Twelve Data](https://twelvedata.com/docs) `twelvedata` | Prices, quotes, time series, symbol search and fundamentals for stocks, forex, crypto and ETFs worldwide. | 9 read | key in header | `TWELVEDATA_API_KEY` | docs 2026-11-08 |
| [World Bank Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/topics/125589) `worldbank` | World Bank development indicators (GDP, population, poverty, health, education...) for every country and aggregate, by year. | 7 read | none | none | docs 2026-11-09 |

### search

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Andi AI Search](https://docs.andiai.com/api-reference/introduction) `andi` | Web search API for agents with adjustable effort, optional full page content, a page fetch/extract endpoint and curated topic news feeds. | 4 read | key in header | `ANDI_API_KEY` | docs 2026-11-09 |
| [arXiv](https://info.arxiv.org/help/api/user-manual.html) `arxiv` | Search and look up arXiv preprints (titles, authors, abstracts, categories, links) through arXiv's free query API. | 1 read | none | none | docs 2026-11-09 |
| [Atly](https://agentic-api.atly.com/v2/docs) `atly` | Review-based place discovery: where to go for an intent (work-friendly, gluten-free, first date) near a location, with reasons. | 5 read | key in header | `ATLY_API_KEY` | docs 2026-11-08 |
| [Brave Search](https://api-dashboard.search.brave.com/documentation) `bravesearch` | Web, news, video and image search plus LLM-ready page context from Brave's independent index. | 7 read | key in header | `BRAVE_SEARCH_API_KEY` | docs 2026-11-08 |
| [BytePlus Searchinfinity](https://docs.byteplus.com/en/docs/searchinfinity/Searchinfinity_available_APIs) `byteplus-search` | Web and image search and static page fetch from BytePlus (ByteDance) Searchinfinity: Flux and Omnira search engines plus a Fetch API. | 3 read | key in header | `BYTEPLUS_SEARCH_API_KEY` | docs 2026-11-09 |
| [Dappier](https://docs.dappier.com/quickstart) `dappier` | Real-time answers from Dappier AI models (live web search, news, stocks) and semantic article recommendations from licensed publisher data models. | 2 read | key in header | `DAPPIER_API_KEY` | docs 2026-11-09 |
| [Exa](https://exa.ai/docs/reference/getting-started) `exa` | Neural web search, page contents, cited answers, Agent runs and Websets from Exa. | 9 read, 3 write | key in header | `EXA_API_KEY` | docs 2026-11-08 |
| [GDELT Project](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) `gdelt` | Free search over worldwide online news monitored by GDELT: article lists and coverage-volume, tone, language and source-country timelines. | 3 read | none | none | docs 2026-11-09 |
| [Hacker News Search (Algolia)](https://hn.algolia.com/api) `hn-algolia` | Full-text search over Hacker News stories, comments, polls and users, plus whole items with their comment trees, from the free HN Search API built on Algolia. | 4 read | none | none | docs 2026-11-09 |
| [Jina AI](https://jina.ai/reader) `jina` | Jina Reader (any URL to LLM-ready markdown), Jina web search returning read pages, and Jina's embeddings and reranker models. | 5 read | key in header | `JINA_API_KEY` | docs 2026-11-09 |
| [Keenable](https://docs.keenable.ai/api-reference) `keenable` | Web search and page fetch on Keenable's own index, built for AI agents. | 2 read | key in header | `KEENABLE_API_KEY` | docs 2026-11-08 |
| [Linkup](https://docs.linkup.so/pages/documentation/get-started/introduction) `linkup` | Agentic web search, page fetch and asynchronous research with sourced answers from Linkup. | 6 read | key in header | `LINKUP_API_KEY` | docs 2026-11-08 |
| [Listen Notes](https://www.listennotes.com/api/docs/) `listennotes` | Podcast search engine API: full-text search of podcasts and episodes, podcast and episode metadata with transcripts, best-of lists, recommendations and genres. | 8 read | key in header | `LISTENNOTES_API_KEY` | docs 2026-11-09 |
| [Litescrape](https://litescrape.com/docs) `litescrape` | Low-cost SERP and web-data API: Google, Bing, DuckDuckGo, maps, app stores, review sites and page fetch. | 37 read | key in header | `LITESCRAPE_API_KEY` | docs 2026-11-08 |
| [NewsAPI.org](https://newsapi.org/docs) `newsapi` | Search news articles from worldwide sources and blogs, top headlines by country, category or source, and the list of sources. | 3 read | key in header | `NEWSAPI_API_KEY` | docs 2026-11-09 |
| [NewsData.io](https://newsdata.io/documentation) `newsdata` | Worldwide news API: latest and archived articles with country, category, language, domain and sentiment filters, plus crypto and market news, sources and article counts. | 6 read | key in header | `NEWSDATA_API_KEY` | docs 2026-11-09 |
| [Octen](https://docs.octen.ai/) `octen` | Real-time web, news and broad search plus URL content extraction from Octen. | 4 read | key in header | `OCTEN_API_KEY` | docs 2026-11-08 |
| [OpenAlex](https://help.openalex.org/how-to-use-the-api/api-overview) `openalex` | Open catalog of scholarly works, authors, institutions, sources and topics, with citations, open-access status and grouping counts. | 8 read | key in query | `OPENALEX_API_KEY` | docs 2026-11-09 |
| [Opoint](https://api-docs.opoint.com/guides/search) `opoint` | Global news monitoring: boolean search over Opoint's news archive, stored-search feeds and SafeFeed firehoses. | 6 read | key in header | `OPOINT_API_KEY` | docs 2026-11-09 |
| [Parallel](https://docs.parallel.ai/getting-started/overview) `parallel` | Web search, URL extraction, deep-research Tasks and people/company discovery from Parallel Web Systems. | 8 read | key in header | `PARALLEL_API_KEY` | docs 2026-11-08 |
| [Perplexity](https://docs.perplexity.ai/docs/getting-started/overview) `perplexity` | Perplexity's Search API, Agent API with web search, and embeddings. | 5 read, 1 write | key in header | `PERPLEXITY_API_KEY` | docs 2026-11-08 |
| [PubMed (NCBI E-utilities)](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html) `pubmed` | NCBI's Entrez E-utilities over PubMed and the other NCBI databases: search, summaries, full records and links between records. | 5 read | key in query | `NCBI_API_KEY` | docs 2026-11-09 |
| [Querit](https://www.querit.ai/en/docs/overview/about) `querit` | Web search API for AI agents, with highlights, optional full page text and code and finance verticals. | 1 read | key in header | `QUERIT_API_KEY` | docs 2026-11-09 |
| [Search1API](https://s1.dev/docs) `search1api` | Web, news and Maps search, page reading, screenshots, extraction and site crawling through one API. | 12 read | key in header | `SEARCH1API_KEY` | docs 2026-11-08 |
| [Seltz](https://docs.seltz.ai/) `seltz` | Web search, cited answers, URL fetch, research agent runs and content monitors, scoped to web, news, wikipedia, people, companies or financial reports. | 7 read | key in header | `SELTZ_API_KEY` | docs 2026-11-09 |
| [Semantic Scholar](https://api.semanticscholar.org/api-docs/graph) `semanticscholar` | Academic Graph API from Ai2: paper and author search, paper details, citations, references and full-text snippet search. | 10 read | key in header | `SEMANTIC_SCHOLAR_API_KEY` | docs 2026-11-09 |
| [SerpApi](https://serpapi.com/search-api) `serpapi` | Structured search results from Google, Google News, Maps, Play and YouTube, with an archive of past searches. | 8 read | key in query | `SERPAPI_API_KEY` | docs 2026-11-08 |
| [Tako](https://docs.tako.com/) `tako` | Knowledge search that returns sourced data cards (charts and tables), cited answers, card data exports, a browsable data graph and answer agents. | 8 read | key in header | `TAKO_API_KEY` | docs 2026-11-09 |
| [Tavily](https://docs.tavily.com/documentation/api-reference) `tavily` | Tavily's web search, extraction, crawl, map and research APIs for AI agents. | 7 read | key in header | `TAVILY_API_KEY` | docs 2026-11-08 |
| [Valyu](https://docs.valyu.ai/api-reference/overview) `valyu` | Search API for agents over the web, SEC filings, market data, economics, papers, trials and patents, plus async DeepResearch reports. | 9 read, 1 write | key in header | `VALYU_API_KEY` | docs 2026-11-08 |
| [You.com](https://you.com/docs/welcome) `you` | You.com's cited Answer, Research and Finance Research APIs. | 5 read | key in header | `YOU_API_KEY` | docs 2026-11-08 |

### scraping

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Apify](https://docs.apify.com/api/v2) `apify` | Run Apify Actors (ready-made scrapers for Google, LinkedIn, Meta ads, TikTok, Lazada, Weibo and more), poll their runs and read the datasets they produce. | 11 read, 4 write | key in header | `APIFY_API_TOKEN` | docs 2026-11-08 |
| [Bright Data](https://docs.brightdata.com/) `brightdata` | Pre-built site scrapers, Web Unlocker, SERP API and Marketplace datasets from Bright Data. | 9 read | key in header | `BRIGHTDATA_API_TOKEN` | docs 2026-11-08 |
| [Browserbase](https://docs.browserbase.com/reference/api/overview.md) `browserbase` | Fetch pages, search the web and run hosted browser agents on Browserbase, and inspect its browser sessions. | 7 read, 2 write | key in header | `BROWSERBASE_API_KEY` | docs 2026-11-09 |
| [Browser Use](https://docs.browser-use.com/cloud/api-reference.md) `browseruse` | Run Browser Use Cloud agents that carry out a task in a hosted stealth browser, and read their status, steps and results. | 5 read, 2 write | key in header | `BROWSER_USE_API_KEY` | docs 2026-11-09 |
| [Crawl4AI Cloud](https://api.crawl4ai.com/docs) `crawl4ai` | Crawl4AI's hosted API: scrape pages to markdown, extract JSON, search the web, Google results as data, direct answers and ready-made site recipes. | 7 read | key in header | `CRAWL4AI_KEY` | docs 2026-11-09 |
| [Decodo](https://help.decodo.com/docs/web-data-api-introduction.md) `decodo` | Decodo's Web Data API: scrape a page or a supported site's template, Google results, crawl a site up to 10,000 pages, or map its URLs. | 8 read | key in header | `DECODO_API_KEY` | docs 2026-11-09 |
| [Firecrawl](https://docs.firecrawl.dev/introduction) `firecrawl` | Scrape, map, crawl, search and extract the web, returning clean markdown and structured data. | 12 read, 1 write | key in header | `FIRECRAWL_API_KEY` | docs 2026-11-08 |
| [Massive (Web Render)](https://docs.joinmassive.com/web-render/overview.md) `joinmassive` | Massive's Web Render API: unblock and render web pages, Google results pages, and ChatGPT, Gemini, Perplexity or AI Overview answers, from chosen locations. | 4 read | key in header | `MASSIVE_TOKEN` | docs 2026-11-09 |
| [Notte](https://docs.notte.cc/api-reference/authentication.md) `notte` | Notte's API: scrape pages to markdown or structured JSON, search the web, and drive hosted browser sessions with plain-language actions. | 6 read, 3 write | key in header | `NOTTE_API_KEY` | docs 2026-11-09 |
| [Olostep](https://docs.olostep.com/) `olostep` | Scrape, crawl, map, search and answer over the web with Olostep's API. | 12 read | key in header | `OLOSTEP_API_KEY` | docs 2026-11-08 |
| [Outscraper](https://app.outscraper.com/api-docs) `outscraper` | Google Maps places and reviews, Google Search, website contacts, company firmographics and Amazon products from Outscraper's scraping API. | 8 read | key in header | `OUTSCRAPER_API_KEY` | docs 2026-11-09 |
| [Oxylabs Web Scraper API](https://developers.oxylabs.io/products/web-scraper-api.md) `oxylabs` | Oxylabs Web Scraper API (Classic): scrape any URL, Google results and Amazon products as HTML, Markdown or parsed JSON, live or as async jobs. | 6 read | HTTP Basic | `OXYLABS_USERNAME`, `OXYLABS_PASSWORD` | docs 2026-11-09 |
| [Riveter](https://docs.riveterhq.com/) `riveter` | Riveter's AI web data API: scrape a page, quick web search, one-off research answers, and enrichment runs that fill new columns for rows of companies, people or URLs. | 6 read, 2 write | key in header | `RIVETER_API_KEY` | docs 2026-11-09 |
| [ScrapeGraphAI](https://docs.scrapegraphai.com/api-reference/introduction) `scrapegraphai` | AI-powered scrape, extract, search and crawl with natural-language prompts (ScrapeGraphAI v2 API). | 8 read, 3 write | key in header | `SGAI_API_KEY` | docs 2026-11-08 |
| [ScreenshotOne](https://screenshotone.com/docs/getting-started/) `screenshotone` | Render a web page, HTML or Markdown to an image or PDF in a real browser, with ad and cookie-banner blocking, element capture and page metadata. | 3 read | key in query | `SCREENSHOTONE_API_KEY` | docs 2026-11-09 |
| [Spider Cloud](https://spider.cloud/docs/api) `spidercloud` | Spider's crawl, scrape, links, search and screenshot API for AI agents and RAG. | 6 read | key in header | `SPIDER_API_KEY` | docs 2026-11-08 |
| [Supadata](https://docs.supadata.ai/api-reference/introduction) `supadata` | Transcripts and metadata of YouTube, TikTok, Instagram, X and Facebook videos, YouTube search, AI extraction from video, and web page scraping to Markdown. | 10 read | key in header | `SUPADATA_API_KEY` | docs 2026-11-09 |
| [TinyFish](https://docs.tinyfish.ai/) `tinyfish` | Hosted browser-agent automation and deep research from TinyFish. | 4 read, 3 write | key in header | `TINYFISH_API_KEY` | docs 2026-11-08 |

### ai

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/APIReference/welcome.html) `amazon-bedrock` | Foundation models (Anthropic, Meta, Mistral, Amazon and others) served on AWS: Converse chat, token counting and the model catalog, with a Bedrock API key. | 5 read | key in header | `AWS_BEARER_TOKEN_BEDROCK` | docs 2026-11-09 |
| [Anthropic (Claude API)](https://platform.claude.com/docs/en/api/overview) `anthropic` | Claude models through Anthropic's own API: messages, token counting, model list and batch jobs. | 7 read | key in header | `ANTHROPIC_API_KEY` | docs 2026-11-09 |
| [Artificial Analysis](https://artificialanalysis.ai/data-api/docs) `artificial-analysis` | Independent benchmarks of AI models: intelligence indices, speed, latency and price for LLMs, and Elo arena rankings for image, video, speech and music models. | 8 read | key in header | `ARTIFICIAL_ANALYSIS_API_KEY` | docs 2026-11-09 |
| [AssemblyAI](https://www.assemblyai.com/docs/api-reference/overview) `assemblyai` | Speech-to-text for audio and video files by URL, with speaker labels, PII redaction, subtitles and word search. | 7 read, 1 write | key in header | `ASSEMBLYAI_API_KEY` | docs 2026-11-09 |
| [Baseten](https://docs.baseten.co/reference/inference-api/overview) `baseten` | Inference for open-source LLMs through Baseten Model APIs (OpenAI-compatible chat), plus your Baseten models and token usage. | 5 read | key in header | `BASETEN_API_KEY` | docs 2026-11-09 |
| [Deepgram](https://developers.deepgram.com/reference/deepgram-api-overview) `deepgram` | Speech-to-text transcription of recorded audio, text-to-speech, and text analysis (summaries, topics, sentiment, intents). | 7 read | key in header | `DEEPGRAM_API_KEY` | docs 2026-11-09 |
| [DeepL](https://developers.deepl.com/docs) `deepl` | Machine translation and text improvement (rephrasing in a style or tone), with glossaries, supported-language lookups and usage. | 6 read | key in header | `DEEPL_AUTH_KEY` | docs 2026-11-09 |
| [DeepSeek](https://api-docs.deepseek.com/) `deepseek` | DeepSeek chat and reasoning models through an OpenAI-compatible API, plus web Search and Fetch tools. | 5 read | key in header | `DEEPSEEK_API_KEY` | docs 2026-11-09 |
| [Gladia](https://docs.gladia.io/api-reference/index) `gladia` | Multilingual speech-to-text for recorded audio, with diarization, translation, summaries, sentiment and subtitles as add-ons. | 3 read, 1 write | key in header | `GLADIA_API_KEY` | docs 2026-11-09 |
| [Google AI (Gemini API)](https://ai.google.dev/gemini-api/docs) `google-ai` | Gemini models for text, images (Nano Banana), video (Omni), speech (TTS) and music (Lyria) through the Gemini Interactions API. | 7 read | key in header | `GEMINI_API_KEY` | docs 2026-11-08 |
| [Groq](https://console.groq.com/docs/api-reference) `groq` | Fast inference of open LLMs (and text-to-speech) on Groq LPUs, through an OpenAI-compatible API. | 6 read | key in header | `GROQ_API_KEY` | docs 2026-11-09 |
| [Hugging Face Hub](https://huggingface.co/docs/hub/api) `huggingface` | Search the Hugging Face Hub's models, datasets, Spaces and papers, and read paper pages and daily papers. | 7 read | key in header | `HF_TOKEN` | docs 2026-11-09 |
| [Klippa DocHorizon (Doxis AI.dp)](https://dochorizon.klippa.com/docs/api/getting-started) `klippa` | Document OCR and data capture: invoices and receipts, identity documents, and generic key-value and table extraction from any document. | 8 read | key in header | `KLIPPA_API_KEY` | docs 2026-11-09 |
| [Mathpix](https://docs.mathpix.com/) `mathpix` | OCR for math, science and general documents: images to Mathpix Markdown/LaTeX, and PDFs or ebooks to Markdown, DOCX, LaTeX, HTML and more. | 6 read, 1 write | key in header | `MATHPIX_APP_ID`, `MATHPIX_APP_KEY` | docs 2026-11-09 |
| [Mistral AI](https://docs.mistral.ai/api/) `mistral` | Mistral language models for chat, embeddings, document OCR, moderation and speech. | 7 read | key in header | `MISTRAL_API_KEY` | docs 2026-11-09 |
| [OpenAI](https://developers.openai.com/api/docs) `openai` | GPT models through the Responses and Chat Completions APIs, plus embeddings, image generation, text-to-speech and moderation. | 8 read | key in header | `OPENAI_API_KEY` | docs 2026-11-09 |
| [OpenRouter](https://openrouter.ai/docs/api-reference/overview) `openrouter` | One OpenAI-style API in front of hundreds of language, image, speech and video models from many providers. | 11 read | key in header | `OPENROUTER_API_KEY` | docs 2026-11-08 |
| [Speechmatics](https://docs.speechmatics.com/api-ref/batch/create-a-new-job) `speechmatics` | Batch speech-to-text jobs on Speechmatics: list jobs, read their status and transcripts, and check usage. | 4 read, 1 write | key in header | `SPEECHMATICS_API_KEY` | docs 2026-11-09 |
| [TypeSafe](https://docs.typesafe.ai/introduction) `typesafe` | Jev, a System One model that answers typed questions (choice, score, yes/no, image box) about text or images with calibrated probabilities instead of generated prose. | 2 read | key in header | `TYPESAFE_API_KEY` | docs 2026-11-09 |
| [Wolfram|Alpha](https://products.wolframalpha.com/api) `wolframalpha` | Computational answers to natural-language questions (math, science, units, data on places, finance, dates and more): LLM-ready text, one-line answers, structured result pods or a rendered result image. | 4 read | key in query | `WOLFRAM_ALPHA_APPID` | docs 2026-11-09 |
| [xAI (Grok)](https://docs.x.ai/developers/rest-api-reference/inference.md) `xai` | Grok language models with server-side web and X search, image generation and model listings through the xAI API. | 7 read | key in header | `XAI_API_KEY` | docs 2026-11-09 |
| [Zhipu AI (BigModel)](https://docs.bigmodel.cn/cn/guide/start/introduction) `zhipu` | GLM chat models, a web search API built for LLMs, a web page reader, embeddings and image generation on Zhipu's BigModel open platform (mainland China). | 6 read | key in header | `ZAI_API_KEY` | docs 2026-11-09 |

### media-generation

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Creatify](https://docs.creatify.ai/introduction) `creatify` | AI video ads from a product link: scrape a URL, generate avatar-led short videos, and list avatars and voices. | 6 read | key in header | `CREATIFY_API_ID`, `CREATIFY_API_KEY` | docs 2026-11-09 |
| [fal.ai](https://fal.ai/docs/api-reference/inference-api/index) `fal` | Hosted image, video, audio and 3D generation models run through fal's queue API, plus model search, pricing and usage. | 5 read | key in header | `FAL_KEY` | docs 2026-11-09 |
| [Higgsfield](https://docs.higgsfield.ai/docs) `higgsfield` | Image and video generation (SOUL, Kling, Seedance, Wan and more) through one asynchronous request API. | 4 read, 2 write | key in header | `HF_API_KEY_ID`, `HF_API_KEY_SECRET` | docs 2026-11-09 |
| [Ideogram](https://developer.ideogram.ai/v2/ideogram-api/api-overview) `ideogram` | Text-to-image with strong typography (Ideogram 4.5), prompt enhancement, and hosted video models. | 5 read | key in header | `IDEOGRAM_API_KEY` | docs 2026-11-09 |
| [Runway](https://docs.dev.runwayml.com/api/) `runway` | Video, image and audio generation (Gen-4.5 and hosted third-party models) as asynchronous tasks. | 6 read, 1 write | key in header | `RUNWAYML_API_SECRET` | docs 2026-11-09 |
| [Shotstack](https://shotstack.io/docs/api/) `shotstack` | Render videos, images and audio from a JSON timeline or saved template, generate AI assets, and inspect media. | 9 read | key in header | `SHOTSTACK_API_KEY` | docs 2026-11-09 |

### developer

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Context7](https://context7.com/docs/api-guide) `context7` | Up-to-date, version-specific documentation and code snippets for software libraries, ranked for a question; plus reading one web page narrowed to a question. | 5 read | key in header | `CONTEXT7_API_KEY` | docs 2026-11-09 |
| [GitHub](https://docs.github.com/en/rest) `github` | Repositories, issues, pull requests, commits and code search on GitHub through its REST API. | 9 read, 1 write | key in header | `GITHUB_TOKEN` | docs 2026-11-09 |
| [Supabase](https://supabase.com/docs/reference/api/introduction) `supabase` | Supabase Management API: the user's projects and organisations, SQL against a project's Postgres, logs, health, advisors, edge functions and storage buckets. | 9 read, 1 write | key in header | `SUPABASE_ACCESS_TOKEN` | docs 2026-11-09 |

### other

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [IPinfo](https://ipinfo.io/developers/ipinfo-api) `ipinfo` | IP geolocation and ASN data: free country-level Lite lookups, and city-level geolocation with hosting, VPN/anonymous, mobile and anycast flags on paid plans. | 3 read | key in header | `IPINFO_TOKEN` | docs 2026-11-09 |
| [NOAA National Hurricane Center](https://www.nhc.noaa.gov/productexamples/) `nhc` | Keyless feeds of active Atlantic and Pacific tropical cyclones: a JSON summary of every active storm with links to its advisories, plus basin and per-storm RSS and GIS feeds. | 4 read | none | none | docs 2026-11-09 |
| [OFAC-API (sanctions screening)](https://docs.ofac-api.com/) `ofacapi` | Fuzzy screening of people, companies, vessels and aircraft against OFAC SDN and 20+ other sanctions, PEP and watch lists, single, batch or as async bulk jobs. | 5 read | key in header | `OFAC_API_KEY` | docs 2026-11-09 |
| [Service Victoria Servo Saver](https://service.vic.gov.au/find-services/transport-and-driving/servo-saver/help-centre/servo-saver-public-api) `servosaver` | Victoria (Australia) government Fair Fuel open data: every registered service station's fuel prices in cents per litre, plus station, brand and fuel-type reference lists. | 4 read | key in header | `SERVOSAVER_API_KEY` | docs 2026-11-09 |
<!-- /vendors -->
