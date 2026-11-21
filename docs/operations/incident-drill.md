# Incident and rollback drill

Do these in **staging**, never prod. Record the outcome in the table at the end. A plan that has not been practised is a guess.

## Drill A: bad release (rollback)
1. Publish a deliberately broken image tag (e.g. a build whose API fails its health check) or set a wrong env value.
2. Apply it to staging. Start a timer when the smoke test first fails.
3. Roll back using `rollback.md` section 1. Stop the timer when the smoke test passes again.
4. Target: back in service in under 15 minutes by someone who did not write the plan.

## Drill B: suspected data exposure (tabletop, 30 minutes)
Scenario: a customer reports seeing another organisation's figures in an export.
1. Who is the incident lead? Who decides to take the service offline? (names in `incident-response.md` contacts table)
2. Which logs answer "who accessed what"? (Activity log; request IDs in Application Insights)
3. Can we prove isolation held? Run the security regression suite (`pytest tests/test_security*.py`) and a cross-tenant check by hand.
4. When must the regulator and the customers be told, and who writes it? (Needs your legal adviser; the outline is not legal advice.)
5. What do we change so it cannot recur, and what test proves it?

## Drill C: lost secrets
Scenario: the JWT secret is exposed. Rotate it in Key Vault and via `TF_VAR_jwt_secret`; confirm everyone is signed out and can sign in again.
Do the same for the registry password and the PostgreSQL admin password.

## Drill record
| Date | Drill | Who ran it | Time to recover | What was wrong with the plan | Fixed? |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
