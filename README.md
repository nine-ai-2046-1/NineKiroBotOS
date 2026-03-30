# NineKiroBotOS

A production-ready Telegram Bot framework powered by [kiro-cli](https://kiro.dev/cli/), featuring persistent AI agent identity, per-session context, and real-time progress streaming.

---

## Features

- 🤖 **AI-powered** — Backed by kiro-cli with full tool access (file read/write, bash, web)
- 🧠 **Persistent Identity** — Agent identity survives context window limits
- 💬 **Per-session Context** — DM, Group, Group Topic, Channel each have isolated sessions
- 📡 **Real-time Progress** — Streaming updates show thinking process in-place
- 🖼️ **Image & File Support** — Send images/files for AI analysis
- 📢 **Channel Integration** — `/cc` command to broadcast, @mention to trigger in channels
- 🔄 **Auto-restart** — Recovers from crashes automatically
- 🔒 **Security** — Whitelist-based access, input sanitization, path traversal protection

---

## What is kiro-cli?

[kiro-cli](https://kiro.dev/cli/) is an AI-powered terminal assistant by AWS that brings agentic capabilities to your command line. It can read/write files, execute shell commands, browse the web, analyze images, and more — all driven by natural language.

NineKiroBotOS uses kiro-cli as its AI engine, so your Telegram bot inherits all of kiro-cli's capabilities.

---

## Install kiro-cli

**macOS:**
```bash
curl -fsSL https://cli.kiro.dev/install | bash
```

**Ubuntu/Debian:**
```bash
wget -q https://desktop-release.q.us-east-1.amazonaws.com/latest/kiro-cli.deb
sudo dpkg -i kiro-cli.deb
```

**After install — login:**
```bash
kiro-cli login
```

Full installation guide: [kiro.dev/docs/cli/installation](https://kiro.dev/docs/cli/installation/)

---

## Quick Start

### Prerequisites
- Python 3.11+
- [kiro-cli](https://kiro.dev/cli/) installed and logged in
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### 1. Clone & Setup
```bash
git clone https://github.com/nine-ai-2046-1/NineKiroBotOS
cd NineKiroBotOS
bash setup.sh
```

The setup wizard will ask you for:
- Bot name, nickname, description, role
- Telegram Bot Token
- Your Telegram User ID
- Optional: Group/Channel whitelists

### 2. Start
```bash
python3 bot.py --agent YourBotName
```

### 3. Background (production)
```bash
python3 bot.py --agent YourBotName >> /var/log/tg_bot.log 2>&1 &
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show help |
| `/new` | Clear current session, start fresh |
| `/info` | Show session folder and Chat ID |
| `/cc <msg>` | Broadcast message to all CC channels |
| Any text | Chat with the AI agent |
| Image | Send image for AI analysis (if `HANDLE_IMAGE=true`) |
| File | Send file for AI processing (if `HANDLE_FILE=true`) |

---

## Configuration

All settings are in `.env` (created by `setup.sh`):

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_NAME` | Agent name (English, no spaces) | required |
| `BOT_DESCRIPTION` | Bot description | required |
| `BotNickName` | Display nickname | required |
| `BOT_ROLE` | Role description for identity | required |
| `BOT_TOKEN` | Telegram Bot Token | required |
| `ALLOWED_USER_ID` | Authorized user ID | required |
| `ALLOWED_GROUP_IDS` | Whitelisted group IDs (comma-separated) | optional |
| `ALLOWED_CHANNEL_IDS` | Whitelisted channel IDs (comma-separated) | optional |
| `CC_CHANNEL_IDS` | Target channels for `/cc` command | optional |
| `AUTO_RESTART` | Auto-restart on crash | `true` |
| `HANDLE_IMAGE` | Accept image messages | `true` |
| `HANDLE_FILE` | Accept file messages | `true` |

---

## Force Working Directory

By default, each chat session gets its own isolated folder:
```
sessions/user_123/
sessions/group_456_topic_main/
```

You can force **all sessions** to use a specific path — useful when you want the bot to always work inside a particular project folder:

```env
# .env
AGENT_WORK_DIR=/home/user/my-project
```

With this set, kiro-cli will always run with `/home/user/my-project` as its working directory, regardless of which chat the message came from. The bot can then read, write, and execute commands directly inside your project.

**Use cases:**
- Point the bot at a git repo so it can code, commit, and push
- Set it to a shared folder for team collaboration
- Use a persistent workspace across all conversations

> ⚠️ All sessions share the same directory — there is no per-chat isolation when `AGENT_WORK_DIR` is set.

---

## Session Structure

Each conversation location gets its own isolated working directory:

```
sessions/
├── user_{id}/              ← DM
├── group_{id}_topic_main/  ← Group (no topics)
├── group_{id}_topic_{id}/  ← Group Topic
└── channel_{id}/           ← Channel
```

kiro-cli uses the session folder as its working directory — code files, downloads, and outputs are stored there.

---

## Agent Identity

The bot's personality is defined in `.kiro/agents/Template.md` using placeholders:

| Placeholder | `.env` Variable |
|-------------|----------------|
| `{BotName}` | `BOT_NAME` |
| `{BotDescription}` | `BOT_DESCRIPTION` |
| `{BotNickName}` | `BotNickName` |
| `{BotRole}` | `BOT_ROLE` |

To update identity:
```bash
# Edit Template.md, then regenerate
bash setup.sh
```

Changes take effect on the next message — no restart needed.

---

## Security

- Unauthorized users are silently ignored (no response)
- Group/Channel access via static whitelist
- Forwarded messages filtered
- Input sanitized (control characters removed, 4000 char limit)
- Path traversal protection on session directories
- Agent name validated (alphanumeric only)
- `.env` and `sessions/` excluded from git

---

## Architecture

```
Telegram User
     ↓
  bot.py (python-telegram-bot)
     ↓
  ask_kiro() — subprocess
     ↓
  kiro-cli chat --agent {BOT_NAME} -r "{prompt}"
     ↓  (working dir = sessions/{key}/)
  AI Response (streamed)
     ↓
  Telegram Reply
```

---

## License

MIT
