# Acctivity: seeding a demo firm

What the Acctivity / Corto platform lets an external service do, for seeding a rep's demo firm (requirement D1).
Code read 19/11 from the Acctivity repos (not in this repo); live probe on the **test** environment 19/11 against one
rep test firm (NZ). No secrets, tokens, firm/user IDs, hostnames or credential locations are recorded here (public
repo); ask the backend team for those.

## Where the data lives

- **Acctivity API**: time entries (`activities`), fixed fees (`fees`),
  disbursements (`cost-recoveries`), invoices, per-user rates (`users/settings`), bank accounts, prompts.
- **Corto Management API**: firm, users, branches, invitations.
- **Docs Core**: matters, contacts, matter-type tree.
- **Dexter**: lookups (areas of law, matter-type tree, party roles).
- Auth: Auth0. One login = one firm (email unique). Test firms: sign up with an `@docorto.ai` / `@cortotest.io` email.

## Service login

- The shared accounting service login (client credentials; ask the backend team for access). It acts on behalf of
  a firm and staff member named in the request, the same way Acctivity's own background services do.

## Probe results (test, 19/11)

| Call | Service login + headers | Rep's own login (admin) |
|------|------------------------|-------------------------|
| Mgmt `GET v1/firm`, `GET v1/users` | 200 | 200 |
| Dexter `GET lookups?countryCode=NZ&industryCategoryId=1` | 200 | 200 |
| Docs Core `GET v1/precedent-tree/matter-types` | 500 "valid CountryCode must be present in the auth token" | 200 |
| Acctivity `GET v1/users/settings/firm` (admin) | 403 "Admin role required" | 200 |
| Acctivity `POST v1/activities` on behalf of a staff member | **201** (entry owned by that staff member) | not tried |
| Acctivity `DELETE v1/activities/{id}` (body `{"activityId": ...}` required) | 204 (soft delete) | not tried |
| Docs Core `POST v1/matters` (real matter-type id) | **201** | not tried |
| Docs Core `DELETE v1/matters/{id}` | 405 (no such route) | not tried |
| Docs Core `PATCH v1/matters/{id}/deleteCode` `{"deleteCode":1}` | not tried | 204 (soft delete) |
| Docs Core `PUT v1/contacts/{id}` (create) | 500 "valid FirmId must be present in the auth token" | 201 |
| Docs Core `PATCH v1/contacts/{id}/deleteCode` | not tried | 204 |
| Mgmt `PATCH v1/firm/name` `{"firmName":...}` (scope `write:firm`) | not tried | 200 (renamed + reverted) |
| Mgmt `PATCH v1/users/{id}` `{"firstName","lastName"}` (scope `manage:users`) | not tried | 200 on a real user; 404 on a pending invitee |
| Mgmt `POST v1/invitations` `{invitationId, userId, emailAddress, isAdministrator}` (caller picks the ids) | not tried | 201; invitee is **not** in `v1/users` until they accept |
| Mgmt `DELETE v1/invitations/{id}?userId=` | not tried | 200 |
| Mgmt `POST v1/users` (create without invite; client-credentials only, scope `create:user`) | lacks scope | 403 (user tokens refused) |
| Acctivity `POST v1/users/settings` on behalf of a staff member | 200 | not tried |

- Matter types are per country; ids are GUIDs from the Docs Core tree (folders → `MatterType` leaves). Dexter's
  `areasOfLaw` (NZ: 20) map to the tree's top-level folders.
- Docs Core ignored `description` on create (stored empty).
- The seeder must only use matter and user ids it created or read from the firm.
- Invitations increase the firm's Stripe subscription quantity; firm rename propagates to Salesforce/Stripe/LEAP.
- Route lists: the services' internal Swagger (VPN only; ask the backend team).
- Probe leftovers: one unused rate setting in the test firm (no delete route; not shown anywhere).

## Which login does what (decided 19/11)

- **Service login**: Acctivity time entries / fees / disbursements / invoices as any staff
  member, per-user rates, Docs Core matter create, Dexter lookups, Management reads.
- **Rep's own demo-firm login** (read from the Acctivity tray app's test token cache, access token only, never
  refreshed; pasted token as fallback): Docs Core matter-type tree, contacts, matter/contact delete, firm rename,
  staff rename, invitations.
