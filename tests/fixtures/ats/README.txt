Synthetic JSON for fictional Lumen Fictional GmbH; no real vacancies or contacts.
The sandbox proxy denied live requests to all four provider API hosts (403).
Fixtures follow the providers' documented public schema, not captured live data:
Lever: https://github.com/lever/postings-api
SmartRecruiters: https://developers.smartrecruiters.com/docs/endpoints
Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api
Workable: https://workable.readme.io/reference/jobs-1
The Workable docs describe /api/accounts/{subdomain}; the requested widget URL
uses the same public account/jobs fields. That exact route could not be verified.
Ashby's documented schema has no id; use the final jobUrl path component.
Lever's public schema has no guaranteed date; a missing date remains empty.
