import os
import random
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from database import Category, Question, SessionLocal, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="0xGREY",
    description="Premium Mock Test Platform",
    version="1.1.0",
)


# --------------------------------------------------
# WEBSITE
# --------------------------------------------------

HOME_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>0xGREY | Mock Test Platform</title>
<style>
:root {
    color-scheme: dark;
    --bg: #0b1120;
    --panel: #151f32;
    --panel2: #1d2a40;
    --blue: #60a5fa;
    --green: #34d399;
    --text: #f8fafc;
    --muted: #a6b4c8;
    --border: #2b3a50;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Arial, sans-serif;
}
header {
    padding: 18px 20px;
    background: #111b2d;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.logo { font-size: 23px; font-weight: 800; color: var(--blue); }
.badge {
    font-size: 12px;
    color: var(--green);
    border: 1px solid #287b65;
    padding: 6px 10px;
    border-radius: 20px;
}
main { max-width: 850px; margin: auto; padding: 25px 16px 50px; }
.hero { padding: 22px 0; }
.hero h1 { font-size: clamp(27px, 6vw, 42px); margin-bottom: 10px; }
.hero p { color: var(--muted); line-height: 1.6; }
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
    gap: 12px;
    margin: 22px 0;
}
.stat, .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 15px;
    padding: 18px;
}
.stat strong { display: block; font-size: 23px; margin-bottom: 7px; }
.stat span { color: var(--muted); font-size: 13px; }
.panel { margin-top: 18px; }
.panel h2 { font-size: 20px; margin-top: 0; }
label { display: block; color: var(--muted); margin: 15px 0 7px; }
select, input {
    width: 100%;
    padding: 12px;
    background: #0c1525;
    color: white;
    border: 1px solid var(--border);
    border-radius: 9px;
    font-size: 16px;
}
button {
    cursor: pointer;
    border: 0;
    border-radius: 9px;
    padding: 13px 16px;
    background: var(--blue);
    color: #071323;
    font-size: 15px;
    font-weight: 700;
}
button:disabled { opacity: .45; cursor: not-allowed; }
.primary { width: 100%; margin-top: 18px; }
.secondary {
    background: var(--panel2);
    color: white;
    border: 1px solid var(--border);
}
.muted { color: var(--muted); }
.hidden { display: none !important; }
.question-top {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 15px;
}
.timer { color: var(--green); font-weight: 800; }
#questionText { font-size: 20px; line-height: 1.55; }
.option {
    display: block;
    width: 100%;
    margin: 10px 0;
    text-align: left;
    background: var(--panel2);
    color: white;
    border: 1px solid var(--border);
    line-height: 1.4;
}
.option.selected { border: 2px solid var(--blue); background: #203b5e; }
.navigation {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-top: 20px;
}
.navigation button { flex: 1; }
#message { margin-top: 14px; color: var(--muted); line-height: 1.5; }
footer { text-align: center; color: var(--muted); padding: 25px; font-size: 12px; }
</style>
</head>
<body>
<header>
    <div class="logo">0xGREY</div>
    <div class="badge">MOCK TEST PLATFORM</div>
</header>

<main>
<section id="dashboard">
    <div class="hero">
        <h1>Practice. Analyse. Improve.</h1>
        <p>Prepare for banking and competitive examinations with 0xGREY.</p>
    </div>

    <div class="grid">
        <div class="stat"><strong id="categoryCount">—</strong><span>Categories</span></div>
        <div class="stat"><strong id="questionCount">—</strong><span>Available questions</span></div>
        <div class="stat"><strong>24/7</strong><span>Practice access</span></div>
    </div>

    <section class="panel">
        <h2>Start a practice test</h2>
        <p class="muted">Choose a category, question count, and duration.</p>

        <label for="category">Subject</label>
        <select id="category"><option value="">Loading categories...</option></select>

        <label for="count">Number of questions</label>
        <select id="count">
            <option>5</option><option>10</option><option>20</option>
            <option>30</option><option>50</option><option>60</option>
            <option>100</option><option>150</option><option>200</option>
        </select>

        <label for="duration">Duration (minutes)</label>
        <input id="duration" type="number" min="1" max="300" value="20">

        <p class="muted">Scoring: +1 correct, −0.25 wrong, 0 unanswered.</p>
        <button class="primary" id="startButton" onclick="startTest()">Start practice test</button>
        <div id="message" role="status"></div>
    </section>
</section>

<section id="testScreen" class="panel hidden">
    <div class="question-top">
        <span id="progress">Question 1</span>
        <span class="timer" id="timer">20:00</span>
    </div>
    <div id="questionText"></div>
    <div id="options"></div>
    <div class="navigation">
        <button class="secondary" onclick="previousQuestion()">Previous</button>
        <button class="secondary" onclick="nextQuestion()">Next</button>
    </div>
    <button class="primary" onclick="submitTest(false)">Submit test</button>
    <div id="testMessage" class="muted"></div>
</section>

<section id="resultScreen" class="panel hidden">
    <h2>Practice test results</h2>
    <div id="resultContent"></div>
    <button class="primary" onclick="location.reload()">Back to dashboard</button>
</section>
</main>

<footer>0xGREY · Premium Mock Test Platform</footer>

<script>
let questions = [];
let answers = {};
let current = 0;
let remaining = 0;
let clock = null;
let submitted = false;

async function loadDashboard() {
    try {
        const response = await fetch('/api/categories');
        if (!response.ok) throw new Error('Could not load categories.');
        const data = await response.json();

        document.getElementById('categoryCount').textContent = data.length;
        const select = document.getElementById('category');
        select.innerHTML = '';

        if (data.length === 0) {
            select.innerHTML = '<option value="">No categories available</option>';
        }

        data.forEach(item => {
            const option = document.createElement('option');
            option.value = item.id;
            option.textContent = item.name + ' (' + item.question_count + ')';
            option.dataset.count = item.question_count;
            select.appendChild(option);
        });

        document.getElementById('questionCount').textContent =
            data.reduce((sum, item) => sum + item.question_count, 0);
    } catch (error) {
        document.getElementById('category').innerHTML =
            '<option value="">Unable to load categories</option>';
        document.getElementById('message').textContent =
            'Could not connect to the question bank. Please try again later.';
    }
}

async function startTest() {
    const category = document.getElementById('category').value;
    const count = Number(document.getElementById('count').value);
    const duration = Number(document.getElementById('duration').value);
    const message = document.getElementById('message');

    if (!category) {
        message.textContent = 'No category is available yet.';
        return;
    }

    if (!Number.isInteger(count) || count < 1 || count > 200) {
        message.textContent = 'Choose between 1 and 200 questions.';
        return;
    }

    if (!Number.isInteger(duration) || duration < 1 || duration > 300) {
        message.textContent = 'Duration must be between 1 and 300 minutes.';
        return;
    }

    const button = document.getElementById('startButton');
    button.disabled = true;
    message.textContent = 'Preparing your test...';

    try {
        const response = await fetch('/api/practice-test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                category_id: Number(category),
                question_count: count,
                duration_minutes: duration
            })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Could not create test.');

        questions = data.questions;
        answers = {};
        current = 0;
        remaining = data.duration_minutes * 60;
        submitted = false;

        document.getElementById('dashboard').classList.add('hidden');
        document.getElementById('resultScreen').classList.add('hidden');
        document.getElementById('testScreen').classList.remove('hidden');

        renderQuestion();
        updateTimer();

        clock = setInterval(() => {
            remaining--;
            updateTimer();
            if (remaining <= 0) submitTest(true);
        }, 1000);
    } catch (error) {
        message.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

function renderQuestion() {
    const q = questions[current];
    document.getElementById('progress').textContent =
        'Question ' + (current + 1) + ' of ' + questions.length;
    document.getElementById('questionText').textContent = q.question_text;

    const container = document.getElementById('options');
    container.innerHTML = '';

    ['A', 'B', 'C', 'D'].forEach(letter => {
        const button = document.createElement('button');
        button.className = 'option' +
            (answers[q.id] === letter ? ' selected' : '');
        button.textContent = letter + '. ' + q.options[letter];
        button.onclick = () => {
            if (submitted) return;
            answers[q.id] = letter;
            renderQuestion();
        };
        container.appendChild(button);
    });

    document.getElementById('testMessage').textContent =
        Object.keys(answers).length + ' question(s) answered.';
}

function previousQuestion() {
    if (current > 0) {
        current--;
        renderQuestion();
    }
}

function nextQuestion() {
    if (current < questions.length - 1) {
        current++;
        renderQuestion();
    }
}

function updateTimer() {
    const mins = Math.floor(Math.max(remaining, 0) / 60);
    const secs = Math.max(remaining, 0) % 60;
    document.getElementById('timer').textContent =
        String(mins).padStart(2, '0') + ':' +
        String(secs).padStart(2, '0');
}

async function submitTest(autoSubmit) {
    if (submitted) return;
    submitted = true;
    if (clock) clearInterval(clock);

    let correct = 0, wrong = 0, unanswered = 0;
    let score = 0;

    questions.forEach(q => {
        const answer = answers[q.id];
        if (!answer) {
            unanswered++;
        } else if (answer === q.correct_option) {
            correct++;
            score += 1;
        } else {
            wrong++;
            score -= 0.25;
        }
    });

    document.getElementById('testScreen').classList.add('hidden');
    document.getElementById('resultScreen').classList.remove('hidden');

    const result = document.getElementById('resultContent');
    result.replaceChildren();

    const details = [
        ['Score', score.toFixed(2)],
        ['Correct', correct],
        ['Wrong', wrong],
        ['Unanswered', unanswered],
        ['Accuracy', questions.length ?
            (correct / questions.length * 100).toFixed(1) + '%' : '0%']
    ];

    details.forEach(([label, value]) => {
        const p = document.createElement('p');
        p.textContent = label + ': ' + value;
        result.appendChild(p);
    });

    const note = document.createElement('p');
    note.className = 'muted';
    note.textContent = autoSubmit ?
        'Your test was submitted because the timer expired.' :
        'Test completed.';
    result.appendChild(note);

    // This first version calculates results in the browser.
    // Server-side answer validation and persistent attempts must
    // be implemented before using this for real examinations.
}

loadDashboard();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return HOME_HTML


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

@app.on_event("startup")
def startup():
    init_db()


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "online",
        "platform": "0xGREY",
        "version": "1.1.0",
    }


# --------------------------------------------------
# CATEGORY API
# --------------------------------------------------

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


# --------------------------------------------------
# PRACTICE TEST API
# --------------------------------------------------

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

            if not pool:
                raise HTTPException(
                    status_code=400,
                    detail="This category has no active questions.",
                )

            if len(pool) < request.question_count:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Only {len(pool)} active questions are available "
                        f"in {category.name}. Add more questions or choose "
                        "a smaller test."
                    ),
                )

            selected = random.sample(pool, request.question_count)
            result = []

            for question in selected:
                options = {
                    "A": question.option_a,
                    "B": question.option_b,
                    "C": question.option_c,
                    "D": question.option_d,
                }

                correct_text = options[question.correct_option]
                shuffled = list(options.values())
                random.shuffle(shuffled)

                shuffled_options = {
                    letter: shuffled[index]
                    for index, letter in enumerate(["A", "B", "C", "D"])
                }

                # Do not send the correct answer to the browser.
                result.append({
                    "id": question.id,
                    "question_text": question.question_text,
                    "options": shuffled_options,
                    "correct_option": next(
                        letter
                        for letter, value in shuffled_options.items()
                        if value == correct_text
                    ),
                })

            # The correct option is needed by this initial client-side
            # practice implementation. Do not use it for secure exams.
            return {
                "category": category.name,
                "duration_minutes": request.duration_minutes,
                "questions": result,
            }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Could not create practice test")
        raise HTTPException(
            status_code=500,
            detail="Could not create practice test.",
        )
