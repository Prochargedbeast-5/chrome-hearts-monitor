# Chrome Hearts monitor

Checks the official storefront for newly observed product links and shopping categories, then sends Discord alerts. Runs on standard GitHub-hosted Linux runners in this public repository.

## Activate
1. Create a webhook for your own Discord text channel (Server Settings → Integrations → Webhooks).
2. In repository Settings → Secrets and variables → Actions, add a repository secret named `DISCORD_WEBHOOK_URL` containing that URL. Never put it in code, issues or README.
3. Open Actions → Chrome Hearts monitor → Run workflow. The first successful run sends a setup confirmation and saves existing products as a baseline.
4. Enable Discord notifications for that channel and verify the confirmation reaches your phone.

Without the secret, the workflow exits without checking or advancing the baseline.

## Behavior and limits
- Checks only between 10 a.m. and 10 p.m. America/Chicago, adjusting for daylight saving time.
- Active sessions check about once per minute (slow checks can take longer), for up to 5 hours 40 minutes. No overlapping checks.
- Scheduled triggers every five minutes during the UTC hours covering CST/CDT queue replacement sessions; concurrency prevents interrupting an active session. Pending runs check out the latest main baseline.
- Initial starts and handoffs can still be delayed or skipped by GitHub. Continuous coverage and exact delivery times are not guaranteed.
- Successful baseline changes are committed immediately. Code/workflow pushes start a run; baseline commits do not.
- Three consecutive check failures end the session visibly as a failed run.
- First run does not alert on existing products. New listings are not proof of stock or checkout availability.
- Detects new URLs, not price changes, size inventory or restocks at previously seen URLs.
- Only discovers products linked from the homepage and observed category pages; hidden or paginated listings can be missed.
- No Reddit monitoring in this script. The existing ChatGPT task remains separate.
- Failed fetches preserve the previous baseline and are retried on the next check; inspect Actions logs for failures.
- A delivery failure can cause an earlier notification in that batch to repeat on retry.
- Public repository schedules may be disabled after 60 days without repository activity. Check Actions periodically.
- Storefront changes or blocking may require maintenance. No sellout or purchase guarantee.

## Local validation
`python3 monitor.py --dry-run` fetches the site without sending messages or changing state.
