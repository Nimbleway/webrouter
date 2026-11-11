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
55 vendors, 520 endpoints.

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
| [Clay](https://developers.clay.com/public-api/quickstart) `clay` | Clay's Public API: people and company search, saved enrichment routines, and queries over your Clay tables. | 7 read, 1 write | key in header | `CLAY_PUBLIC_API_KEY` | docs 2026-11-09 |
| [ContactOut](https://api.contactout.com/) `contactout` | Find work and personal emails and phones from LinkedIn profiles, search people and companies, and verify emails. | 20 read | key in header | `CONTACTOUT_API_KEY` | docs 2026-11-08 |
| [Enrow](https://docs.enrow.io/api-overview) `enrow` | Find and verify professional emails and find phone numbers, asynchronously, by name, company or LinkedIn URL. | 7 read | key in header | `ENROW_API_KEY` | docs 2026-11-08 |
| [LeadMagic](https://leadmagic.io/docs/api-reference/email-validation) `leadmagic` | Find and validate work emails, personal emails and mobiles from names or profile URLs, and look up profiles and companies. | 8 read | key in header | `LEADMAGIC_API_KEY` | docs 2026-11-08 |
| [LeadOcean](https://leadocean.io/docs) `leadocean` | B2B people and company search, person and company enrichment, work and personal email, phone and reverse lookups, and bulk CSV exports. | 14 read, 1 write | key in header | `LEADOCEAN_API_KEY` | docs 2026-11-08 |
| [Prospeo](https://prospeo.io/api-docs) `prospeo` | Find and verify emails and mobiles, enrich people and companies, and search a B2B people and company database. | 7 read | key in header | `PROSPEO_API_KEY` | docs 2026-11-08 |

### developer

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Context7](https://context7.com/docs/api-guide) `context7` | Up-to-date, version-specific documentation and code snippets for software libraries, ranked for a question; plus reading one web page narrowed to a question. | 5 read | key in header | `CONTEXT7_API_KEY` | docs 2026-11-09 |
| [GitHub](https://docs.github.com/en/rest) `github` | Repositories, issues, pull requests, commits and code search on GitHub through its REST API. | 9 read, 1 write | key in header | `GITHUB_TOKEN` | docs 2026-11-09 |
| [Supabase](https://supabase.com/docs/reference/api/introduction) `supabase` | Supabase Management API: the user's projects and organisations, SQL against a project's Postgres, logs, health, advisors, edge functions and storage buckets. | 9 read, 1 write | key in header | `SUPABASE_ACCESS_TOKEN` | docs 2026-11-09 |
<!-- /vendors -->
