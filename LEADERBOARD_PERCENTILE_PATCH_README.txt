PH 0xGREY // Intelligence — Leaderboard Percentile Patch

Included files:
- 0xGREY_bot_fixed.py
- database.py

Changes in this patch:
1. Adds quiz_family_id to StudentAttempt and migrates existing databases safely.
2. Builds a stable quiz-family fingerprint from question text, option meanings, correct answers, marks, and penalties. Question order/option-letter order do not split identical mock cohorts.
3. Overall percentile is recalculated across saved attempts for the same mock family, including group polls and private Telegram attempts.
4. Group percentile is recalculated only against attempts in the participant's own Telegram group for the same mock family.
5. Private quiz completions are saved to the shared StudentAttempt analytics table and included in the overall comparison pool; private attempts do not receive a group percentile.
6. Group leaderboard output remains group-only, while it displays the size of the overall same-mock comparison pool.
7. Existing persisted group sessions can backfill their family ID into older attempt rows when the bot restores those sessions.

Validation performed:
- Python syntax compilation passed for both files.
- Database initialization/migration and quiz_family_id storage passed against a temporary SQLite database.
- Focused tests passed for stable fingerprints, combined cross-group/private overall percentiles, and group-only percentile handling.
- Full Telegram runtime integration was not executed in this environment because python-telegram-bot is not installed here. Run in the project's normal deployment environment before production use.

Deployment:
1. Back up the current bot file and database.
2. Replace the deployed bot file and database.py with the included versions.
3. Install/use the project's existing requirements and environment variables.
4. Restart the bot so database.init_db() applies the additive migration.
5. Test the same exact mock in two approved groups and a private saved-link attempt. Confirm each attempt gets the same quiz_family_id and overall pool count increases across all of them.

Note: Separate publications are grouped only when the normalized question content, correct-answer meanings, marks, and penalties match. Different question sets intentionally have separate percentiles.
