# Sendspark: candidate for the "videos" output

Researched 18/11 (Kushal asked whether our server could
integrate with https://www.sendspark.com/video-platform/ai-intros to customise videos).
Nothing built or tested yet; everything below is from Sendspark's docs.

## Verdict from that session

Yes, it's possible: REST API + webhooks. The catch is that it **fills variables into one
recorded base video**; it does not create a new video from scratch per prospect.

## How it personalises

- Someone records one base video once (in Sendspark).
- Per prospect, Sendspark clones the speaker's voice to say name + other variables and lip-syncs the face.
- Dynamic background: the prospect's website (scrolling capture), LinkedIn, Google Maps, or a custom image.
- Multi-language from one recording.
- Output: share/landing page (CTA + calendar embed), animated GIF thumbnail, MP4.

## Plan / cost

| Plan | $/mo | API + webhooks | Multiple AI variables | Dynamic video min/mo |
|------|------|----------------|-----------------------|----------------------|
| Solo | 49 | no | no | 100 |
| **Growth** | **99** | **yes** | **yes** | 250 |
| Team | 299 | yes | yes | 1,000 |
| Business | 699 | yes | yes | 3,000 |

Growth is the minimum for us. Whether a free trial includes API access is **unverified**.

## API

- Base URL: `https://api-gw.sendspark.com/v1`
- Headers: `x-api-key` (workspace-scoped), `x-api-secret` (per user; shown once at creation), `Accept: application/json`
- Credentials: https://sendspark.com/settings/api-credentials

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/workspaces` | list workspaces (`_id`, `name`) |
| POST | `/workspaces/{workspaceId}/dynamics` | create dynamic campaign (holds the base video) |
| GET | `/workspaces/{workspaceId}/dynamics` | list campaigns |
| GET | `/workspaces/{workspaceId}/dynamics/{dynamicId}` | get campaign |
| POST | `/workspaces/{workspaceId}/dynamics/{dynamicId}/prospect` | add one prospect → generates video |
| POST | `/workspaces/{workspaceId}/dynamics/{dynamicId}/prospects/bulk` | add many |
| GET | `/workspaces/{workspaceId}/dynamics/{dynamicId}/prospects/{email}` | poll a prospect's video |
| GET | `/auth/health` | health check |

**Add-prospect body:**
- `prospect` (required): `contactName`, `contactEmail`, `company`, `jobTitle`, `customField`,
  `backgroundUrl`, `originalBackgroundUrl`, `screenshotUrl`, `webhookUrl`, `audioUrl` (undocumented; might allow our own audio, needs testing)
- optional: `callbackId`, `processAndAuthorizeCharge` (allows billing beyond plan limit),
  `fallbackToEmailDomainForBackground`, `prospectDepurationConfig { forceCreation, payloadDepurationStrategy }`
- Response: campaign object with `_id` + `status: "pending"`. **No video link**: it's async.

**Rate limits:** 30 req/min; bulk 1 req/min.

## Getting the result

- **Webhook** (Settings → Webhooks; dynamic campaigns only). Events: Video Created, Video Ready to Download
  (MP4, a few minutes after Created), Video Failed to Generate, Video Viewed, Played, Watched to the End, CTA Clicked, Liked.
  Payload: `campaignId`, `campaignName`, `dynamicCampaignId`, `workspaceId`, `creatorId`, `videoLink`, `embedLink`,
  `thumbnailUrl`, `contactInfo { contactEmail, contactFirstName, jobTitle, company, backgroundUrl, fields }`.
  Signing/retries not documented, so secure it ourselves (secret in URL / match `callbackId`).
- **Polling:** `GET .../prospects/{email}`. Simpler for a hackathon demo (no public URL needed).

## Fit with the sales engine

- Research step already yields `contactName`, `company`, `jobTitle`, firm website → maps straight onto the prospect body;
  firm website as `backgroundUrl` gives the "we looked at your site" effect.
- The spoken script is fixed apart from the variables, so the per-firm angle has to live in variables / `customField`.
- Prospect names/emails go to a third party; fine for public firm info, check before using anything private.
- Repo is public: API key/secret only in `.env` (already gitignored).

## Suggested first probe (from that session)

Get Growth access, record one test video with variables, add one prospect with curl, poll by email
(or point the webhook at a test URL). That confirms payload shape and generation time before any code.

## Sources

- https://www.sendspark.com/video-platform/ai-intros
- https://www.sendspark.com/pricing
- https://help.sendspark.com/en/articles/9051823-api-automatically-create-dynamic-videos-via-api
- https://help.sendspark.com/api-reference/dynamics-campaign/add-prospect-to-dynamics-campaign-and-process-video.md
- https://help.sendspark.com/en/articles/9186828-webhooks-how-to-create-webhooks-in-sendspark
- https://help.sendspark.com/webhooks-properties-in-sendspark-webhooks
- Full API index: https://help.sendspark.com/llms.txt
