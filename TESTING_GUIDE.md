# PHASE 2 TESTING GUIDE

## Prerequisites: Start PostgreSQL

### Option 1: Docker (Recommended)
```bash
docker-compose up -d
```
This starts PostgreSQL 16 + pgvector automatically.

### Option 2: Local PostgreSQL
Ensure PostgreSQL 16+ is running on:
- Host: localhost
- Port: 5432
- User: shan_user
- Password: your_db_password_here
- Database: shan_ai

## Start the Server

### Terminal 1: Start FastAPI with Telegram Bot Polling
```bash
python run_server.py
```

**Expected output:**
```
======================================================================
Starting Shan-AI Decision Intelligence Platform
======================================================================
FastAPI: http://0.0.0.0:8000
API Docs: http://0.0.0.0:8000/docs
Telegram Bot: Polling mode
======================================================================

[INFO] Uvicorn running on http://0.0.0.0:8000
[INFO] Database tables initialized.
[INFO] Telegram bot initialized.
[INFO] Telegram bot polling started in background.
```

## Test the Bot with Telegram

### Step 1: Open Telegram
- Open your Telegram app
- Search for bot: `@ShanAIBot_Bot` (or your bot name)
- Start the chat

### Step 2: Send Commands
```
/start       → Bot greets you and creates a user account
/register    → Initiate registration (pending admin approval)
Hello World  → Bot stores message in database
```

### Step 3: Check API Endpoints

#### Register via API
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "telegram_id": 123456789,
    "email": "john@example.com"
  }'
```

#### Approve Role via API
```bash
curl -X POST http://localhost:8000/api/v1/auth/approve-role \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "role": "department_manager"
  }'
```

#### Fetch User Profile
```bash
curl http://localhost:8000/api/v1/auth/users/123456789
```

#### API Docs
```
http://localhost:8000/docs
```
(Interactive Swagger UI - test all endpoints here)

## Check Database

### Connect to PostgreSQL
```bash
psql "postgresql://shan_user:your_db_password_here@localhost:5432/shan_ai"
```

### View Users
```sql
SELECT * FROM users;
```

### View Messages
```sql
SELECT * FROM messages;
```

### View Decisions
```sql
SELECT * FROM decisions;
```

## Troubleshooting

### PostgreSQL Connection Failed
- Make sure Docker container is running: `docker ps`
- Or start local PostgreSQL: `sudo service postgresql start`

### Bot Not Responding
- Check that bot polling is running in terminal output
- Verify TELEGRAM_BOT_TOKEN is set in `.env`
- Check logs for errors

### Port 8000 Already in Use
```bash
# Find process on port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
```

## Workflow Test

1. ✓ Send `/start` to bot → User created in DB
2. ✓ Send `/register` to bot → User status shown
3. ✓ Send message to bot → Message stored in database
4. ✓ Use API to approve role → User gets role in database
5. ✓ Fetch user profile via API → See updated role

---

**Next: PHASE 3 - Claude Decision Engine**
