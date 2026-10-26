import os
import json
import hmac
import time
import base64
import hashlib
import logging
import random

from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_

from database import (
    Category,
    Question,
    Quiz,
    StudentAttempt,
    SessionLocal,
    init_db,
)

# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("0xgrey")

APP_SECRET = os.getenv("APP_SECRET", "").strip()

if len(APP_SECRET) < 32:
    raise RuntimeError(
        "APP_SECRET must contain at least 32 characters. "
        "Set a persistent random secret in your deployment environment."
    )

app = FastAPI(
    title="0xGREY Premium Mock Test Platform",
    description="Online mock tests and performance analytics",
    version="2.1.0",
)

LETTERS = ["A", "B", "C", "D"]

MOTIVATIONAL_QUOTES = [
    "Consistency creates progress.",
    "Every attempt is an opportunity to improve.",
    "Discipline turns preparation into performance.",
    "Progress comes from learning, practising and repeating.",
    "A mistake understood today becomes strength tomorrow.",
    "Focus on improvement, not perfection.",
    "Strong preparation is built one question at a time.",
    "Keep learning. Keep practising. Keep progressing.",
]


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

@app.on_event("startup")
def startup():
    init_db()
    logger.info("0xGREY platform started.")


@app.get("/health")
def health():
    return {
        "status": "online",
        "platform": "0xGREY",
        "version": "2.1.0",
    }


# ============================================================
# SIGNED TEST TOKEN
# ============================================================

def sign_payload(payload: dict) -> str:
    """
    Sign a test manifest.

    The manifest contains question IDs and displayed-option mappings.
    It does not contain correct answers.
    """

    raw = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return encoded + "." + signature


def verify_payload(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)

        expected = hmac.new(
            APP_SECRET.encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            raise HTTPException(
                status_code=400,
                detail="Invalid test token.",
            )

        padded = encoded + "=" * (-len(encoded) % 4)

        raw = base64.urlsafe_b64decode(padded.encode())

        payload = json.loads(raw.decode())

        if not isinstance(payload, dict):
            raise ValueError("Invalid payload.")

        return payload

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Malformed or invalid test token.",
        )


# ============================================================
# PERSONALIZED PERFORMANCE FEEDBACK
# ============================================================

def generate_feedback(
    accuracy: float,
    correct: int,
    wrong: int,
    unanswered: int,
) -> dict:
    """
    Generate feedback from calculated performance.

    This is a deterministic fallback, not an external AI call.
    """

    if accuracy >= 90:
        tagline = "Outstanding accuracy — excellent work!"
        insight = (
            "Your attempted answers were highly accurate. "
            "Continue practising difficult questions and maintaining "
            "your accuracy."
        )

    elif accuracy >= 75:
        tagline = "Strong performance — keep building momentum."
        insight = (
            "Your accuracy is encouraging. Review incorrect answers "
            "and strengthen the concepts that need more practice."
        )

    elif accuracy >= 50:
        tagline = "Good progress — targeted practice can help."
        insight = (
            "Review the reasoning behind incorrect answers. "
            "Focus on understanding concepts before increasing speed."
        )

    elif correct > 0:
        tagline = "Every correct answer is progress."
        insight = (
            "Use this attempt to identify weaker topics. "
            "Study the explanations and practise similar questions."
        )

    else:
        tagline = "Your preparation journey starts here."
        insight = (
            "Begin by reviewing the concepts tested in this quiz. "
            "Work through the explanations and try again after practice."
        )

    if unanswered > 0:
        insight += (
            f" You left {unanswered} question(s) unanswered. "
            "Review your time-management strategy."
        )

    if wrong > correct and wrong > 0:
        insight += (
            " Incorrect answers exceeded correct answers. "
            "Prioritise careful option analysis."
        )

    return {
        "tagline": tagline,
        "insight": insight,
        "quote": random.choice(MOTIVATIONAL_QUOTES),
    }


# ============================================================
# HOME PAGE
# ============================================================

HOME_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>0xGREY | Premium Mock Tests</title>

<style>
:root {
    color-scheme: dark;
    --bg: #080e1b;
    --panel: #121d30;
    --panel2: #1b2a42;
    --blue: #60a5fa;
    --green: #34d399;
    --text: #f8fafc;
    --muted: #a6b4c8;
    --border: #2b3a50;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Arial, sans-serif;
}

header {
    padding: 18px;
    background: #101a2b;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}

.logo {
    font-size: 24px;
    font-weight: 900;
    color: var(--blue);
}

.badge {
    border: 1px solid #287b65;
    color: var(--green);
    border-radius: 20px;
    padding: 7px 10px;
    font-size: 11px;
}

main {
    max-width: 950px;
    margin: auto;
    padding: 22px 15px 45px;
}

.hero h1 {
    font-size: clamp(28px, 6vw, 44px);
    margin-bottom: 10px;
}

.muted {
    color: var(--muted);
    line-height: 1.6;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 12px;
    margin: 22px 0;
}

.stat, .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 15px;
    padding: 18px;
}

.stat strong {
    display: block;
    font-size: 25px;
    margin-bottom: 6px;
}

.stat span {
    color: var(--muted);
    font-size: 13px;
}

.panel {
    margin-top: 18px;
}

label {
    display: block;
    margin: 15px 0 7px;
    color: var(--muted);
}

input, select {
    width: 100%;
    padding: 13px;
    border: 1px solid var(--border);
    background: #0b1424;
    color: white;
    border-radius: 9px;
    font-size: 16px;
}

button {
    cursor: pointer;
    border: 0;
    border-radius: 9px;
    padding: 13px 15px;
    background: var(--blue);
    color: #071323;
    font-size: 15px;
    font-weight: bold;
}

button:disabled {
    opacity: .5;
    cursor: not-allowed;
}

.primary {
    width: 100%;
    margin-top: 16px;
}

.secondary {
    background: var(--panel2);
    color: white;
    border: 1px solid var(--border);
}

.hidden {
    display: none !important;
}

.question-top {
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 18px;
}

.timer {
    color: var(--green);
    font-weight: bold;
}

#questionText {
    font-size: 20px;
    line-height: 1.6;
    white-space: pre-wrap;
}

.option {
    display: block;
    width: 100%;
    text-align: left;
    margin: 10px 0;
    background: var(--panel2);
    color: white;
    border: 1px solid var(--border);
    line-height: 1.5;
}

.option.selected {
    border: 2px solid var(--blue);
    background: #203b5e;
}

.navigation {
    display: flex;
    gap: 10px;
    margin-top: 20px;
}

.navigation button {
    flex: 1;
}

.result-card {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 15px;
    margin: 12px 0;
    overflow-wrap: anywhere;
}

.score {
    color: var(--green);
    font-size: 30px;
    font-weight: 900;
}

.table-wrap {
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}

th, td {
    border-bottom: 1px solid var(--border);
    padding: 10px 6px;
    text-align: left;
}

footer {
    text-align: center;
    padding: 25px;
    color: var(--muted);
    font-size: 13px;
}
</style>
</head>

<body>

<header>
    <div class="logo">0xGREY</div>
    <div class="badge">PREMIUM MOCK TESTS</div>
</header>

<main>

<section id="dashboard">

    <div class="hero">
        <h1>Practice. Analyse. Improve.</h1>
        <p class="muted">
            Prepare for banking and competitive examinations.
            Learn from every attempt and track your performance.
        </p>
    </div>

    <div class="grid">
        <div class="stat">
            <strong id="categoryCount">—</strong>
            <span>Categories</span>
        </div>

        <div class="stat">
            <strong id="questionCount">—</strong>
            <span>Available questions</span>
        </div>

        <div class="stat">
            <strong>200</strong>
            <span>Maximum questions per test</span>
        </div>
    </div>

    <section class="panel">
        <h2>Start a mock test</h2>

        <label for="studentName">Your display name</label>
        <input id="studentName" maxlength="100"
               placeholder="Enter your name">

        <label for="category">Subject</label>
        <select id="category"></select>

        <label for="count">Number of questions</label>
        <input id="count" type="number"
               min="1" max="200" value="20">

        <label for="duration">Duration in minutes</label>
        <input id="duration" type="number"
               min="1" max="300" value="20">

        <p class="muted">
            Scoring: +1 correct, −0.25 wrong, 0 unanswered.
        </p>

        <button class="primary" id="startButton"
                onclick="startTest()">
            Start test
        </button>

        <p id="message" class="muted" role="status"></p>
    </section>

    <section class="panel">
        <h2>Recent leaderboard</h2>
        <p class="muted">
            Results are grouped by subject and test settings.
        </p>

        <button class="secondary" onclick="loadLeaderboard()">
            Refresh leaderboard
        </button>

        <div id="leaderboard"></div>
    </section>

</section>


<section id="testScreen" class="panel hidden">

    <div class="question-top">
        <span id="progress">Question 1</span>
        <span id="timer" class="timer">20:00</span>
    </div>

    <div id="questionText"></div>
    <div id="options"></div>

    <div class="navigation">
        <button class="secondary" onclick="previousQuestion()">
            Previous
        </button>

        <button class="secondary" onclick="nextQuestion()">
            Next
        </button>
    </div>

    <p id="testMessage" class="muted"></p>

    <button class="primary" onclick="submitTest(false)">
        Submit test
    </button>

</section>


<section id="resultScreen" class="panel hidden">
    <h2>🏆 Your test results</h2>
    <div id="resultContent"></div>

    <button class="primary" onclick="location.reload()">
        Back to dashboard
    </button>
</section>

</main>

<footer>✍️ Created by PH</footer>

<script>
let questions = [];
let answers = {};
let current = 0;
let remaining = 0;
let clock = null;
let submitted = false;
let testToken = "";
let testStartedAt = 0;
let studentName = "";
let submissionInProgress = false;


async function loadDashboard() {
    try {
        const response = await fetch("/api/categories");
        const data = await response.json();

        if (!response.ok) throw new Error();

        document.getElementById("categoryCount").textContent =
            data.length;

        document.getElementById("questionCount").textContent =
            data.reduce((sum, item) => sum + item.question_count, 0);

        const select = document.getElementById("category");
        select.replaceChildren();

        data.forEach(item => {
            const option = document.createElement("option");
            option.value = item.id;
            option.textContent =
                item.name + " (" + item.question_count + ")";
            select.appendChild(option);
        });

        if (!data.length) {
            document.getElementById("message").textContent =
                "No active questions are available yet.";
        }

    } catch {
        document.getElementById("message").textContent =
            "Unable to load categories. Check the server.";
    }
}


async function startTest() {
    const categoryId =
        Number(document.getElementById("category").value);

    const count =
        Number(document.getElementById("count").value);

    const duration =
        Number(document.getElementById("duration").value);

    studentName =
        document.getElementById("studentName").value.trim();

    const message = document.getElementById("message");

    if (!studentName) {
        message.textContent = "Please enter your display name.";
        return;
    }

    if (!Number.isInteger(categoryId) || categoryId < 1) {
        message.textContent = "Choose a valid category.";
        return;
    }

    if (!Number.isInteger(count) || count < 1 || count > 200) {
        message.textContent = "Choose between 1 and 200 questions.";
        return;
    }

    if (!Number.isInteger(duration) ||
        duration < 1 || duration > 300) {
        message.textContent =
            "Duration must be between 1 and 300 minutes.";
        return;
    }

    const button = document.getElementById("startButton");
    button.disabled = true;
    message.textContent = "Preparing your test...";

    try {
        const response = await fetch("/api/practice-test", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                category_id: categoryId,
                question_count: count,
                duration_minutes: duration
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Could not start test.");
        }

        questions = data.questions;
        testToken = data.test_token;
        answers = {};
        current = 0;
        submitted = false;
        submissionInProgress = false;
        remaining = data.duration_minutes * 60;
        testStartedAt = Date.now();

        document.getElementById("dashboard").classList.add("hidden");
        document.getElementById("resultScreen").classList.add("hidden");
        document.getElementById("testScreen").classList.remove("hidden");

        renderQuestion();
        updateTimer();

        if (clock) clearInterval(clock);

        clock = setInterval(() => {
            remaining--;
            updateTimer();

            if (remaining <= 0) {
                submitTest(true);
            }
        }, 1000);

    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}


function renderQuestion() {
    if (!questions.length) return;

    const q = questions[current];

    document.getElementById("progress").textContent =
        "Question " + (current + 1) + " of " + questions.length;

    document.getElementById("questionText").textContent =
        q.question_text;

    const container = document.getElementById("options");
    container.replaceChildren();

    ["A", "B", "C", "D"].forEach(letter => {
        const button = document.createElement("button");

        button.className = "option" +
            (answers[q.id] === letter ? " selected" : "");

        button.textContent = letter + ". " + q.options[letter];

        button.onclick = () => {
            if (submitted) return;

            answers[q.id] = letter;
            renderQuestion();
        };

        container.appendChild(button);
    });

    document.getElementById("testMessage").textContent =
        Object.keys(answers).length + " question(s) answered.";
}


function previousQuestion() {
    if (current > 0 && !submitted) {
        current--;
        renderQuestion();
    }
}


function nextQuestion() {
    if (current < questions.length - 1 && !submitted) {
        current++;
        renderQuestion();
    }
}


function updateTimer() {
    const seconds = Math.max(remaining, 0);
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;

    document.getElementById("timer").textContent =
        String(minutes).padStart(2, "0") + ":" +
        String(remainder).padStart(2, "0");
}


async function submitTest(autoSubmit) {
    if (submitted || submissionInProgress) return;

    submissionInProgress = true;

    if (clock) {
        clearInterval(clock);
        clock = null;
    }

    document.getElementById("testScreen").classList.add("hidden");
    document.getElementById("resultScreen").classList.remove("hidden");

    const result = document.getElementById("resultContent");
    result.textContent = "Submitting your answers...";

    const submittedAnswers = {};

    questions.forEach(q => {
        if (answers[q.id]) {
            submittedAnswers[q.id] = answers[q.id];
        }
    });

    const elapsedSeconds = Math.max(
        0,
        Math.floor((Date.now() - testStartedAt) / 1000)
    );

    try {
        const response = await fetch("/api/submit-test", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                test_token: testToken,
                student_name: studentName,
                answers: submittedAnswers,
                elapsed_seconds: elapsedSeconds
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Submission failed.");
        }

        submitted = true;
        renderResults(data, autoSubmit);
        loadLeaderboard();

    } catch (error) {
        result.textContent =
            "Submission failed: " + error.message +
            ". Please contact the platform administrator.";
        submissionInProgress = false;
    }
}


function renderResults(data, autoSubmit) {
    const result = document.getElementById("resultContent");
    result.replaceChildren();

    const card = document.createElement("div");
    card.className = "result-card";

    function addText(tag, text, className = "") {
        const element = document.createElement(tag);
        element.textContent = text;
        if (className) element.className = className;
        card.appendChild(element);
        return element;
    }

    addText("h3", data.quiz_title);
    addText(
        "div",
        data.score.toFixed(2) + " / " + data.maximum_score.toFixed(2),
        "score"
    );

    const details = [
        ["Rank", data.rank ? "#" + data.rank : "—"],
        ["Score percentage", data.score_percent.toFixed(1) + "%"],
        ["Accuracy", data.accuracy_percent.toFixed(1) + "%"],
        ["Group percentile", formatPercentile(data.group_percentile)],
        ["Overall percentile", formatPercentile(data.overall_percentile)],
        ["Correct", data.correct_count],
        ["Wrong", data.wrong_count],
        ["Unanswered", data.unanswered_count],
        ["Attempted", data.attempted_count],
        ["Time taken", formatTime(data.duration_seconds)]
    ];

    details.forEach(([label, value]) => {
        addText("p", label + ": " + value);
    });

    addText("h3", data.performance_tagline);
    addText("p", data.performance_insight);
    addText("p", "“" + data.motivational_quote + "”", "muted");

    addText(
        "p",
        autoSubmit
            ? "Your test was submitted when the timer expired."
            : "Your test has been submitted successfully.",
        "muted"
    );

    result.appendChild(card);
}


function formatPercentile(value) {
    if (value === null || value === undefined) {
        return "Not available";
    }
    return Number(value).toFixed(1) + "%";
}


function formatTime(seconds) {
    seconds = Math.max(0, Number(seconds) || 0);
    return Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
}


async function loadLeaderboard() {
    const container = document.getElementById("leaderboard");
    if (!container) return;

    try {
        const response = await fetch("/api/leaderboard");
        const data = await response.json();

        if (!response.ok) throw new Error();

        container.replaceChildren();

        if (!data.length) {
            container.textContent = "No completed tests yet.";
            return;
        }

        const wrapper = document.createElement("div");
        wrapper.className = "table-wrap";

        const table = document.createElement("table");
        const head = document.createElement("thead");
        const headerRow = document.createElement("tr");

        ["Rank", "Student", "Score", "Accuracy"].forEach(label => {
            const th = document.createElement("th");
            th.textContent = label;
            headerRow.appendChild(th);
        });

        head.appendChild(headerRow);
        table.appendChild(head);

        const body = document.createElement("tbody");

        data.forEach(item => {
            const row = document.createElement("tr");

            [
                "#" + item.rank,
                item.student_name,
                Number(item.score).toFixed(2),
                Number(item.accuracy_percent).toFixed(1) + "%"
            ].forEach(value => {
                const td = document.createElement("td");
                td.textContent = value;
                row.appendChild(td);
            });

            body.appendChild(row);
        });

        table.appendChild(body);
        wrapper.appendChild(table);
        container.appendChild(wrapper);

    } catch {
        container.textContent = "Leaderboard unavailable.";
    }
}


loadDashboard();
loadLeaderboard();
</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HOME_HTML


# ============================================================
# CATEGORY API
# ============================================================

@app.get("/api/categories")
def get_categories():
    try:
        with SessionLocal() as db:
            categories = db.query(Category).order_by(Category.name).all()
            result = []

            for category in categories:
                count = (
                    db.query(Question)
                    .filter(
                        Question.category_id == category.id,
                        Question.active.is_(True),
                    )
                    .count()
                )

                result.append({
                    "id": category.id,
                    "name": category.name,
                    "question_count": count,
                })

            return result

    except Exception:
        logger.exception("Could not load categories")
        raise HTTPException(
            status_code=500,
            detail="Could not load categories.",
        )


# ============================================================
# CREATE OR REUSE A PRACTICE QUIZ
# ============================================================

class PracticeTestRequest(BaseModel):
    category_id: int = Field(gt=0)
    question_count: int = Field(ge=1, le=200)
    duration_minutes: int = Field(ge=1, le=300)


@app.post("/api/practice-test")
def create_practice_test(request: PracticeTestRequest):
    try:
        with SessionLocal() as db:
            category = db.get(Category, request.category_id)

            if category is None:
                raise HTTPException(
                    status_code=404,
                    detail="Category not found.",
                )

            pool = (
                db.query(Question)
                .filter(
                    Question.category_id == category.id,
                    Question.active.is_(True),
                )
                .all()
            )

            if len(pool) < request.question_count:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Only {len(pool)} active questions "
                        "are available in this category."
                    ),
                )

            title = (
                f"{category.name} Practice Test "
                f"({request.question_count} Questions)"
            )

            quiz = (
                db.query(Quiz)
                .filter(
                    Quiz.category_id == category.id,
                    Quiz.question_count == request.question_count,
                    Quiz.duration_minutes == request.duration_minutes,
                    Quiz.title == title,
                    Quiz.status == "published",
                )
                .first()
            )

            if quiz is None:
                quiz = Quiz(
                    title=title,
                    category_id=category.id,
                    question_count=request.question_count,
                    duration_minutes=request.duration_minutes,
                    marks_per_correct=1.0,
                    negative_marks=0.25,
                    shuffle_questions=True,
                    shuffle_options=True,
                    status="published",
                )
                db.add(quiz)
                db.commit()
                db.refresh(quiz)

            # Use the same question set for participants taking the
            # same quiz configuration, allowing fairer comparisons.
            rng = random.Random(f"0xGREY-QUIZ-{quiz.id}")

            selected = rng.sample(pool, request.question_count)
            rng.shuffle(selected)

            option_maps = {}
            result_questions = []

            for question in selected:
                # Displayed letter -> original option letter.
                original_letters = LETTERS.copy()
                rng.shuffle(original_letters)

                option_map = {
                    display_letter: original_letter
                    for display_letter, original_letter
                    in zip(LETTERS, original_letters)
                }

                option_maps[str(question.id)] = option_map

                original_text = {
                    "A": question.option_a,
                    "B": question.option_b,
                    "C": question.option_c,
                    "D": question.option_d,
                }

                displayed_options = {
                    display_letter: original_text[original_letter]
                    for display_letter, original_letter
                    in option_map.items()
                }

                result_questions.append({
                    "id": question.id,
                    "question_text": question.question_text,
                    "options": displayed_options,
                })

            started_epoch = int(time.time())

            token_payload = {
                "quiz_id": quiz.id,
                "category_id": category.id,
                "question_ids": [q.id for q in selected],
                "option_maps": option_maps,
                "started_epoch": started_epoch,
                "duration_seconds": request.duration_minutes * 60,
            }

            token = sign_payload(token_payload)

            return {
                "quiz_id": quiz.id,
                "category": category.name,
                "duration_minutes": request.duration_minutes,
                "questions": result_questions,
                "test_token": token,
            }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Could not create practice test")
        raise HTTPException(
            status_code=500,
            detail="Could not create practice test.",
        )


# ============================================================
# SUBMIT TEST
# ============================================================

class SubmitTestRequest(BaseModel):
    test_token: str = Field(min_length=10, max_length=100000)

    student_name: str = Field(
        min_length=1,
        max_length=100,
    )

    answers: dict[str, str] = Field(
        default_factory=dict,
    )

    elapsed_seconds: int = Field(
        ge=0,
        le=86400,
    )


@app.post("/api/submit-test")
def submit_test(request: SubmitTestRequest):
    payload = verify_payload(request.test_token)

    try:
        quiz_id = int(payload["quiz_id"])
        category_id = int(payload["category_id"])
        question_ids = [int(value) for value in payload["question_ids"]]
        option_maps = payload["option_maps"]
        started_epoch = int(payload["started_epoch"])
        duration_limit = int(payload["duration_seconds"])

    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="The test token is missing required information.",
        )

    if not question_ids or len(question_ids) > 200:
        raise HTTPException(
            status_code=400,
            detail="Invalid question list.",
        )

    if len(set(question_ids)) != len(question_ids):
        raise HTTPException(
            status_code=400,
            detail="Duplicate question IDs are not permitted.",
        )

    now_epoch = int(time.time())

    if now_epoch < started_epoch:
        raise HTTPException(
            status_code=400,
            detail="Invalid test start time.",
        )

    elapsed_seconds = now_epoch - started_epoch

    # Small allowance for network latency.
    if elapsed_seconds > duration_limit + 30:
        raise HTTPException(
            status_code=400,
            detail="The test deadline has passed.",
        )

    valid_ids = {str(qid) for qid in question_ids}

    for key, value in request.answers.items():
        if key not in valid_ids:
            raise HTTPException(
                status_code=400,
                detail="An answer refers to a question outside this test.",
            )

        if value not in LETTERS:
            raise HTTPException(
                status_code=400,
                detail="An answer contains an invalid option.",
            )

    # Verify every question has a complete, valid mapping.
    for question_id in question_ids:
        mapping = option_maps.get(str(question_id))

        if not isinstance(mapping, dict):
            raise HTTPException(
                status_code=400,
                detail="Invalid option mapping in test token.",
            )

        if set(mapping.keys()) != set(LETTERS):
            raise HTTPException(
                status_code=400,
                detail="Incomplete option mapping.",
            )

        if set(mapping.values()) != set(LETTERS):
            raise HTTPException(
                status_code=400,
                detail="Invalid option mapping.",
            )

    try:
        with SessionLocal() as db:
            quiz = db.get(Quiz, quiz_id)

            if quiz is None:
                raise HTTPException(
                    status_code=404,
                    detail="Quiz not found.",
                )

            if quiz.category_id != category_id:
                raise HTTPException(
                    status_code=400,
                    detail="The test category does not match.",
                )

            questions = (
                db.query(Question)
                .filter(
                    Question.id.in_(question_ids),
                    Question.category_id == category_id,
                )
                .all()
            )

            question_map = {
                question.id: question
                for question in questions
            }

            if len(question_map) != len(question_ids):
                raise HTTPException(
                    status_code=400,
                    detail="Some test questions are unavailable.",
                )

            correct = 0
            wrong = 0
            unanswered = 0

            for question_id in question_ids:
                question = question_map[question_id]
                selected_letter = request.answers.get(str(question_id))

                if selected_letter is None:
                    unanswered += 1
                    continue

                # Translate the displayed letter back to the original
                # answer letter using the signed mapping.
                original_letter = option_maps[str(question_id)][
                    selected_letter
                ]

                correct_letter = question.correct_option.upper().strip()

                if original_letter == correct_letter:
                    correct += 1
                else:
                    wrong += 1

            total = len(question_ids)
            attempted = correct + wrong

            score = (
                correct * float(quiz.marks_per_correct)
                - wrong * float(quiz.negative_marks)
            )

            maximum_score = total * float(quiz.marks_per_correct)

            accuracy = (
                correct / attempted * 100
                if attempted
                else 0.0
            )

            score_percent = (
                score / maximum_score * 100
                if maximum_score
                else 0.0
            )

            feedback = generate_feedback(
                accuracy=accuracy,
                correct=correct,
                wrong=wrong,
                unanswered=unanswered,
            )

            student_name = request.student_name.strip()

            # Web identity is based on the display name. This is not
            # authentication and must not be used as proof of identity.
            student_hash = hashlib.sha256(
                student_name.casefold().encode("utf-8")
            ).hexdigest()[:24]

            student_id = "web-" + student_hash

            # Prevent duplicate submissions for the same displayed
            # student and quiz. This is a basic safeguard, not a
            # substitute for authenticated accounts or one-time tokens.
            existing = (
                db.query(StudentAttempt)
                .filter(
                    StudentAttempt.quiz_id == quiz.id,
                    StudentAttempt.student_telegram_id == student_id,
                )
                .first()
            )

            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A result already exists for this display name "
                        "and test. Use an authenticated student account "
                        "system for reliable identity management."
                    ),
                )

            attempt = StudentAttempt(
                quiz_id=quiz.id,
                student_telegram_id=student_id,
                student_name=student_name,
                group_id="",
                group_title="Web Practice",
                score=score,
                correct_count=correct,
                wrong_count=wrong,
                unanswered_count=unanswered,
                started_at=datetime.fromtimestamp(started_epoch),
                submitted_at=datetime.utcnow(),
                duration_seconds=elapsed_seconds,
                accuracy_percent=accuracy,
                score_percent=score_percent,
                performance_tagline=feedback["tagline"],
                performance_insight=feedback["insight"],
                motivational_quote=feedback["quote"],
                leaderboard_published=False,
            )

            db.add(attempt)
            db.commit()
            db.refresh(attempt)

            # All participants using the same quiz configuration share
            # the same quiz ID and can be ranked together.
            attempts = (
                db.query(StudentAttempt)
                .filter(StudentAttempt.quiz_id == quiz.id)
                .order_by(
                    StudentAttempt.score.desc(),
                    StudentAttempt.submitted_at.asc(),
                    StudentAttempt.id.asc(),
                )
                .all()
            )

            rank = next(
                (
                    index
                    for index, item in enumerate(attempts, start=1)
                    if item.id == attempt.id
                ),
                None,
            )

            total_participants = len(attempts)

            # Percentile = percentage of other participants with
            # strictly lower scores. A single participant has no
            # meaningful comparative percentile.
            lower_scores = sum(
                1
                for item in attempts
                if item.score < attempt.score
            )

            percentile = (
                lower_scores / (total_participants - 1) * 100
                if total_participants > 1
                else None
            )

            attempt.overall_percentile = percentile
            attempt.group_percentile = None

            db.commit()

            return {
                "attempt_id": attempt.id,
                "quiz_id": quiz.id,
                "quiz_title": quiz.title,
                "rank": rank,
                "participant_count": total_participants,
                "score": round(score, 2),
                "maximum_score": round(maximum_score, 2),
                "score_percent": round(score_percent, 2),
                "accuracy_percent": round(accuracy, 2),
                "group_percentile": None,
                "overall_percentile": (
                    round(percentile, 2)
                    if percentile is not None
                    else None
                ),
                "correct_count": correct,
                "wrong_count": wrong,
                "unanswered_count": unanswered,
                "attempted_count": attempted,
                "duration_seconds": elapsed_seconds,
                "performance_tagline": feedback["tagline"],
                "performance_insight": feedback["insight"],
                "motivational_quote": feedback["quote"],
            }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Test submission failed")

        raise HTTPException(
            status_code=500,
            detail="Could not save your test result.",
        )


# ============================================================
# LEADERBOARD
# ============================================================

@app.get("/api/leaderboard")
def get_leaderboard(
    quiz_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    try:
        with SessionLocal() as db:
            if quiz_id is None:
                latest_attempt = (
                    db.query(StudentAttempt)
                    .order_by(StudentAttempt.submitted_at.desc())
                    .first()
                )

                if latest_attempt is None:
                    return []

                quiz_id = latest_attempt.quiz_id

            quiz = db.get(Quiz, quiz_id)

            if quiz is None:
                raise HTTPException(
                    status_code=404,
                    detail="Quiz not found.",
                )

            attempts = (
                db.query(StudentAttempt)
                .filter(StudentAttempt.quiz_id == quiz_id)
                .order_by(
                    StudentAttempt.score.desc(),
                    StudentAttempt.submitted_at.asc(),
                    StudentAttempt.id.asc(),
                )
                .limit(limit)
                .all()
            )

            return [
                {
                    "rank": index,
                    "student_name": attempt.student_name,
                    "score": attempt.score,
                    "accuracy_percent": attempt.accuracy_percent,
                    "correct_count": attempt.correct_count,
                    "wrong_count": attempt.wrong_count,
                    "unanswered_count": attempt.unanswered_count,
                    "duration_seconds": attempt.duration_seconds,
                }
                for index, attempt in enumerate(attempts, start=1)
            ]

    except HTTPException:
        raise

    except Exception:
        logger.exception("Could not load leaderboard")

        raise HTTPException(
            status_code=500,
            detail="Could not load leaderboard.",
        )
