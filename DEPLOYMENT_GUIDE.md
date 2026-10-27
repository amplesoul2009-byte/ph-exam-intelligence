0xGREY — Deployment Guide

1. Project Overview

Project: Daily Current Affairs By PH
Bot name: 0xGREY
Purpose: Banking exam preparation, current affairs, vocabulary, mock tests, and quiz management.

2. Required Files

Keep these files in the project directory:

- "bot.py" — Telegram bot
- "database.py" — SQLAlchemy models and database initialization
- "requirements.txt" — Python dependencies
- "0xGREY_500_quiz_interaction_messages.txt" — Quiz interaction messages
- "main.py" — FastAPI web application, if used
- ".env" — Optional local environment configuration; never upload secrets to GitHub

3. Install Dependencies

Run:

pip install -r requirements.txt

Confirm that your hosting environment uses a Python version compatible with every installed dependency.

4. Configure Environment Variables

Set the following variables through your hosting provider's environment-variable settings, where supported:

BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
OWNER_ID=YOUR_TELEGRAM_USER_ID
DATABASE_URL=YOUR_DATABASE_CONNECTION_STRING
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GROQ_API_KEY=YOUR_GROQ_API_KEY
AI_PROVIDER=gemini
GEMINI_MODEL=YOUR_SUPPORTED_GEMINI_MODEL

Replace the example values with your actual credentials.

Do not publish tokens, API keys, passwords, or database credentials.

Important: The variable names must match those actually read by your "bot.py". Verify them before starting the bot.

5. Database Setup

Back up your existing database before running migrations or changing database configuration.

Initialize the database using the initialization method supported by your project.

If your code supports running "database.py" directly, you can use:

python database.py

Do not run this command against your production database until you have checked the existing schema and backed up the data.

6. PDF and OCR Support

For ordinary text-based PDFs, the project uses "pypdf".

For scanned PDFs, additional components may be required:

- "pdf2image"
- "pytesseract"
- Tesseract OCR system executable
- Poppler utilities

Installing Python packages alone does not install the required system executables.

If OCR is unavailable, verify that the bot reports the problem clearly rather than silently producing incomplete questions.

7. Start the Telegram Bot

Use the startup command appropriate to your hosting provider.

For a bot that runs directly from "bot.py", the command may be:

python bot.py

Check the startup logs for missing environment variables, dependency errors, database failures, and Telegram authentication errors.

Do not start multiple polling instances of the same Telegram bot token unless your application is explicitly designed for that setup.

8. Start the Web Application

If "main.py" contains the FastAPI application and exposes an object named "app", a typical startup command is:

uvicorn main:app --host 0.0.0.0 --port 8000

Use the port required by your hosting provider.

The web application and Telegram bot may need separate processes or services. Confirm the hosting configuration before deploying both.

9. Security Checklist

Before making the bot available to users:

- Verify the owner's Telegram user ID.
- Verify that only authorized owners or explicitly authorized administrators can execute control commands.
- Enforce the approved-group allowlist on the server side.
- Do not trust a Telegram group administrator's role as sufficient authorization.
- Keep API keys and bot tokens private.
- Validate quiz passwords and access permissions on the server.
- Protect student records and quiz results.
- Back up the database regularly.
- Confirm that unauthorized users cannot publish or modify quizzes.

10. Testing Checklist

Test the following in a private environment before publishing:

1. Bot starts successfully.
2. Owner authorization works.
3. Unauthorized control commands are rejected.
4. Database records survive a restart.
5. Questions and options are parsed correctly.
6. Option shuffling preserves the correct answer.
7. Quiz timers work correctly.
8. Negative marking is calculated correctly.
9. Submitted answers are preserved when a quiz is stopped.
10. Pause, resume, restart, and finalization behave correctly.
11. Leaderboards show correct scores and rankings.
12. Only approved groups receive authorized content.
13. AI-generated questions are reviewed before publication.
14. PDF extraction errors are reported clearly.
15. The web application starts and its routes work, if enabled.

11. Deployment Safety

Do not replace existing production files simply because a new file compiles.

First compare the new files with the current project, back up the database, verify imports and model compatibility, and test the complete workflow.

A successful Python syntax check does not establish that Telegram polling, database migrations, AI APIs, quiz scoring, or web routes work correctly.

12. Deployment Status

This guide describes the deployment procedure. It does not certify that the application has been deployed or passed end-to-end tests.

Record the actual results of each test before declaring the release production-ready.
