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
12 vendors, 85 endpoints.

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

### observability

| Vendor | What it holds | Endpoints | Auth | Variables | Checked |
|---|---|---|---|---|---|
| [Datadog](https://docs.datadoghq.com/api/latest/) `datadog` | Metrics, logs, events, monitors, dashboards, hosts, incidents and SLOs from your Datadog organization. | 13 read | key in header | `DD_API_KEY`, `DD_APPLICATION_KEY` | docs 2026-11-08 |
| [Oh Dear](https://ohdear.app/docs/api/introduction) `ohdear` | Website monitoring: monitors, uptime and downtime, response-time metrics, certificate health, broken links and per-check summaries. | 9 read | key in header | `OHDEAR_API_KEY` | docs 2026-11-09 |
| [Sentry](https://docs.sentry.io/api/) `sentry` | Error and performance monitoring: issues, their events and stack traces, explore queries over errors, logs and spans, event volume stats, projects and releases. | 8 read, 1 write | key in header | `SENTRY_AUTH_TOKEN` | docs 2026-11-09 |
<!-- /vendors -->
